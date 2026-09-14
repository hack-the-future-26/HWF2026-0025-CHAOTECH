"""
P3 Step 10 -- the recompute job that runs Steps 1-8 end to end.

Reads citizen_request, clusters it, scores every cluster, and writes
demand_cluster + priority_score. Idempotent: each run clears the previous
clustering and scores first, so triggering it after seeding more data
refreshes the whole picture rather than stacking duplicates.

Exposed to P2 as POST /recompute-scores (backend/routes_intelligence.py).
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "backend"))

from models import (  # noqa: E402
    Asset,
    CitizenRequest,
    DemandCluster,
    Gazetteer,
    PriorityScore,
    VillagePriority,
    WorkGroup,
)

from . import config  # noqa: E402
from . import realdata  # noqa: E402
from .clustering import cluster_all, haversine_km  # noqa: E402
from .population import nearest_hq_distance_km, population_in_catchment  # noqa: E402
from .scoring import score_cluster  # noqa: E402


def _load_reports(db) -> list[dict]:
    return [
        {
            "id": row.id,
            "raw_text": row.raw_text,
            "issue_category": row.issue_category,
            "severity": row.severity,
            "district": row.district,
            "block": row.block,
            "village": row.village,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "precise_lat": row.precise_lat,
            "precise_lon": row.precise_lon,
            "facility_id": row.facility_id,
            "pin_source": row.pin_source,
            "is_synthetic": bool(row.is_synthetic),
            "confidence": row.confidence,
        }
        for row in db.query(CitizenRequest).all()
    ]


def _apply_precise_coords(reports: list[dict]) -> list[dict]:
    """
    Substitute citizen-supplied GPS pins for village centroids.

    When both precise_lat and precise_lon are non-null, overwrite the report's
    latitude/longitude so that downstream clustering (DBSCAN + _work_groups)
    uses the actual position.  When either is missing, the village centroid
    is kept -- today's exact behaviour.

    Pure function: returns a new list of shallow-copied dicts; the originals
    are not mutated.
    """
    out = []
    for report in reports:
        r = dict(report)
        plat = r.get("precise_lat")
        plon = r.get("precise_lon")
        if plat is not None and plon is not None:
            r["latitude"] = plat
            r["longitude"] = plon
        out.append(r)
    return out


def _load_gazetteer(db) -> list[dict]:
    return [
        {
            "id": row.id,
            "name": row.name,
            "admin_level": row.admin_level,
            "district": row.district,
            "block": row.block,
            "population": row.population,
            "lat": row.latitude,
            "lon": row.longitude,
        }
        for row in db.query(Gazetteer).all()
    ]


def _modal(values: list) -> object | None:
    """Most common non-null value, used to label a cluster's district/block."""
    present = [v for v in values if v]
    if not present:
        return None
    return Counter(present).most_common(1)[0][0]


def _unique_reporters(reports: list[dict]) -> int:
    """
    Distinct reporters, approximated by distinct report text.

    There is no citizen identity in this dataset, so identical text is
    treated as one voice repeated rather than several independent ones.
    That is the "500 people forwarding the same WhatsApp message is not 500
    corroborations" guard from research report SS10.4, implemented with what
    the schema actually has. Real per-citizen identity would replace this.
    """
    return len({(r.get("raw_text") or "").strip().lower() for r in reports})


def _settlement_count(reports: list[dict]) -> int:
    return len({r.get("village") for r in reports if r.get("village")})


def _attach_stragglers(
    reports: list[dict],
    assignments: dict[int, tuple[str, int]],
    grouped: dict[tuple[str, int], list[dict]],
    by_id: dict[int, dict],
) -> int:
    """
    Second pass: attach still-unclustered reports to an ALREADY-ESTABLISHED
    cluster of the same category, if one sits within the gate radius.

    Why this is principled rather than a fudge. DBSCAN's min_samples rule
    exists so that three scattered reports cannot conjure a cluster out of
    nothing -- that guarantee is about *forming* a cluster, and this pass
    does not weaken it: no new cluster is ever created here. Once a problem
    is established by corroboration, a later nearby report of the same
    category is genuinely more evidence for that known problem, and the
    right place for it is that cluster.

    Without this, a citizen reporting a real issue in a thinly-populated
    block gets cluster_id = NULL and the feedback loop has nothing to say to
    them -- which is precisely the population the whole system exists to
    serve, so silently dropping their report would be the worst possible
    failure mode.
    """
    if not grouped:
        return 0

    centroids = {
        key: (
            sum(m["latitude"] for m in members) / len(members),
            sum(m["longitude"] for m in members) / len(members),
        )
        for key, members in grouped.items()
    }

    attached = 0
    for report in reports:
        if report["id"] in assignments:
            continue
        category = report.get("issue_category")
        if not category:
            continue
        if report.get("latitude") is None or report.get("longitude") is None:
            continue

        candidates = [
            (haversine_km(report["latitude"], report["longitude"], lat, lon), key)
            for key, (lat, lon) in centroids.items()
            if key[0] == category
        ]
        if not candidates:
            continue

        distance, key = min(candidates)
        if distance <= config.GATE_RADIUS_KM:
            assignments[report["id"]] = key
            grouped[key].append(by_id[report["id"]])
            attached += 1

    return attached


# ---------------------------------------------------------------------------
# Work groups -- which specific thing inside a cluster is broken
# ---------------------------------------------------------------------------

# How close two reports must sit to count as the same broken thing. 250 m is
# well under the span of a village but far above GPS jitter, so two people
# standing at one culvert group together while two ends of a village do not.
WORK_GROUP_RADIUS_M = 250.0


def _group_by_radius(members: list[dict], radius_m: float) -> list[list[dict]]:
    """Single-linkage agglomerative clustering of members within radius_m."""
    groups: list[list[dict]] = []

    def near(a: dict, b: dict) -> bool:
        return haversine_km(
            a["latitude"], a["longitude"], b["latitude"], b["longitude"]
        ) * 1000.0 <= radius_m

    for member in members:
        joined = next((g for g in groups if any(near(member, o) for o in g)), None)
        if joined is None:
            groups.append([member])
        else:
            joined.append(member)

    # Single-linkage needs a settling pass: two groups can both be within
    # range of a report seen only after each had already started.
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if any(near(a, b) for a in groups[i] for b in groups[j]):
                    groups[i].extend(groups.pop(j))
                    merged = True
                    break
            if merged:
                break

    return groups


def _work_groups(members: list[dict]) -> list[dict]:
    """
    Split one cluster's reports into the units a crew would actually be sent to.

    The cluster answers "which area and which sector deserves money". It was
    never able to answer "which road", because every report carries its
    village's centroid: inside a village the geographic distance between two
    reports is exactly zero, so nothing could separate them.

    Single-linkage agglomeration at WORK_GROUP_RADIUS_M: a report joins a
    group if it is within that distance of ANY report already in it, so
    complaints strung along one road stay one group instead of fragmenting.
    Clusters hold at most a few dozen reports, so the O(n^2) sweep costs
    nothing and avoids the tuning a second DBSCAN pass would need.

    Today this resolves to one group per village -- already worth showing,
    since a cluster routinely spans several. Once the intake form supplies a
    GPS pin, the identical code separates two roads inside one village with no
    change here.
    """
    groups = _group_by_radius(members, WORK_GROUP_RADIUS_M)
    out: list[dict] = []
    for group in groups:
        lat = sum(m["latitude"] for m in group) / len(group)
        lon = sum(m["longitude"] for m in group) / len(group)
        spread = max(
            (haversine_km(lat, lon, m["latitude"], m["longitude"]) * 1000.0 for m in group),
            default=0.0,
        )
        village = _modal([m.get("village") for m in group])
        block = _modal([m.get("block") for m in group])
        # The longest complaint carries the most detail for an officer; the
        # shortest are usually just "road bad".
        sample = max((m.get("raw_text") or "" for m in group), key=len, default="")
        pin_count = sum(
            1 for m in group
            if m.get("precise_lat") is not None and m.get("precise_lon") is not None
        )
        location_basis = (
            "citizen_gps_pin" if pin_count == len(group)
            else "mixed" if pin_count > 0
            else "village_centroid"
        )
        out.append(
            {
                "label": village or block or "Unnamed location",
                "centroid_lat": lat,
                "centroid_lon": lon,
                "report_count": len(group),
                "distinct_reporters": _unique_reporters(group),
                "spread_m": round(spread, 1),
                "village": village,
                "block": block,
                "sample_text": sample[:300],
                "location_basis": location_basis,
            }
        )

    out.sort(key=lambda g: g["report_count"], reverse=True)
    return out


def _score_members(
    members: list[dict],
    lat: float,
    lon: float,
    category: str,
    *,
    gazetteer: list[dict],
    amenity_index: list[dict],
    works_index: list[dict],
    gw_stations: list[dict],
    water_testing_index: dict[int, dict] | None = None,
    river_readings: list[dict] | None = None,
    hazard_alerts: list[dict] | None = None,
    facility_id: int | None = None,
    school_condition_index: dict[int, dict] | None = None,
    groups: list[dict] | None = None,
) -> dict:
    """
    Score a group of citizen reports (either a DemandCluster or an Asset).

    Calculates catchment population, confidence, real infrastructure deficit,
    vulnerability, reporting capacity deficit, scheme eligibility, and HQ distance
    based on government data surrounding (lat, lon), then runs score_cluster()
    and builds the full evidence dictionary.
    """
    population_affected = population_in_catchment(lat, lon, category, gazetteer)
    confidences = [m["confidence"] for m in members if m.get("confidence") is not None]
    average_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    unique_reporters = _unique_reporters(members)
    settlement_count = _settlement_count(members)
    block = _modal([m.get("block") for m in members])

    nearby_villages = realdata.villages_near(lat, lon, amenity_index)

    infra_value, infra_evidence = realdata.real_infra_deficit(
        category, nearby_villages, works_index, population_affected
    )
    # A specific school's own 2024-25 UDISE+ condition is more specific than
    # the village-wide 2011 Census signal above, so it takes over when we
    # have it -- never blended, since the two are answering slightly
    # different questions (this building vs. this village's schools).
    if category == "education" and facility_id is not None:
        school_value, school_evidence = realdata.school_condition_deficit(
            facility_id, school_condition_index or {}
        )
        if school_value is not None:
            infra_value, infra_evidence = school_value, school_evidence
    gw_station, gw_text = None, None
    wt_evidence, wt_text = None, None
    if category == "water":
        gw_station, gw_text = realdata.lookup_groundwater(lat, lon, gw_stations)
        if gw_text:
            infra_evidence["groundwater_corroboration"] = gw_text
        wt_evidence, wt_text = realdata.water_testing_catchment_evidence(
            nearby_villages, water_testing_index or {}
        )
        if wt_text:
            infra_evidence["water_testing_corroboration"] = wt_text

    # Evidence-only for now (see hazard_near's own docstring): corroborates a
    # possible emergency without moving the score, since the urgency-term
    # redesign that would actually use this hasn't been built yet.
    hazard_flag, hazard_evidence = (False, {})
    if category in ("road", "water"):
        hazard_flag, hazard_evidence = realdata.hazard_near(
            lat, lon, river_readings or [], hazard_alerts or []
        )

    vulnerability_value, vulnerability_evidence = realdata.real_vulnerability(
        nearby_villages
    )
    reporting_value, reporting_evidence = realdata.real_reporting_capacity_deficit(
        nearby_villages
    )
    eligible, eligibility_evidence = realdata.real_scheme_eligibility(
        category, nearby_villages, population_affected
    )
    hq_km, hq_evidence = realdata.real_hq_distance_km(nearby_villages)

    result = score_cluster(
        unique_reporters=unique_reporters,
        population_affected=population_affected,
        settlement_count=settlement_count,
        severities=[m["severity"] for m in members],
        average_confidence=average_confidence,
        issue_category=category,
        block=block,
        distance_to_hq_km=nearest_hq_distance_km(lat, lon, gazetteer),
        real_infra_deficit=infra_value,
        real_vulnerability=vulnerability_value,
        real_reporting_deficit=reporting_value,
        # Only assert eligibility when there were records to judge it on;
        # "we have no data" must not be recorded as "not eligible".
        scheme_eligible=eligible if eligibility_evidence else None,
        real_hq_distance_km=hq_km,
    )
    counted = sorted(
        (v for v in nearby_villages if v.get("population")),
        key=lambda v: v["population"],
        reverse=True,
    )
    result["evidence"] = {
        # Which source each term actually used -- real government records or
        # the fallback proxy. score_cluster computed this to produce the
        # number; before, it was dropped on the floor, so a score could be
        # read but a reader could not tell which terms were guesses. Now it
        # is persisted alongside the rest of the working (§10.2 feature #3).
        "data_basis": result["data_basis"],
        "demand": {
            "distinct_reporters": unique_reporters,
            "total_reports": len(members),
            "saturates_at": config.DEMAND_SATURATION_REPORTERS,
            "settlements": settlement_count,
        },
        "population": {
            "people_affected": population_affected,
            "catchment_radius_km": config.CATCHMENT_RADIUS_KM.get(
                category, config.DEFAULT_CATCHMENT_RADIUS_KM
            ),
            "villages_in_catchment": len(nearby_villages),
            "largest_villages": [
                {"name": v["name"], "population": v["population"]}
                for v in counted[:5]
            ],
        },
        "infra_deficit": infra_evidence,
        "vulnerability": vulnerability_evidence,
        "reporting_capacity": reporting_evidence,
        "scheme_eligibility": eligibility_evidence,
        "feasibility": hq_evidence,
        "cost_benchmark": realdata.cost_benchmark(category, works_index),
        "work_groups": len(groups) if groups is not None else 0,
        "villages_examined": len(nearby_villages),
    }
    if category == "water" and gw_text:
        result["evidence"]["groundwater"] = gw_text
    if category == "water" and wt_text:
        result["evidence"]["water_testing"] = wt_text
    if hazard_flag:
        result["evidence"]["hazard_corroboration"] = hazard_evidence

    return result


def _index_gazetteer(gazetteer: list[dict]):
    by_id = {g["id"]: g for g in gazetteer if g.get("id") is not None}
    by_full = {}
    by_dist_village = {}
    by_village = {}
    for g in gazetteer:
        d = (g.get("district") or "").strip().lower()
        b = (g.get("block") or "").strip().lower()
        v = (g.get("name") or "").strip().lower()
        if d and b and v:
            by_full[(d, b, v)] = g
        if d and v and (d, v) not in by_dist_village:
            by_dist_village[(d, v)] = g
        if v and v not in by_village:
            by_village[v] = g
    return by_id, by_full, by_dist_village, by_village


def _lookup_gazetteer_entry(
    r: dict,
    by_full: dict,
    by_dist_village: dict,
    by_village: dict,
) -> dict | None:
    d = (r.get("district") or "").strip().lower()
    b = (r.get("block") or "").strip().lower()
    v = (r.get("village") or "").strip().lower()
    if d and b and v and (d, b, v) in by_full:
        return by_full[(d, b, v)]
    if d and v and (d, v) in by_dist_village:
        return by_dist_village[(d, v)]
    if v and v in by_village:
        return by_village[v]
    return None


def _determine_location_basis(members: list[dict]) -> str:
    has_pin = [
        m.get("precise_lat") is not None or bool(m.get("pin_source"))
        for m in members
    ]
    if not any(has_pin):
        return "village_centroid"
    sources = [
        m.get("pin_source") or ("citizen_gps" if m.get("precise_lat") is not None else None)
        for m in members
    ]
    if all(s == "citizen_gps" for s in sources):
        return "citizen_gps_pin"
    if all(s == "synthetic_seed" for s in sources):
        return "synthetic_seed"
    return "mixed"


def _find_school_candidates(lat: float, lon: float, schools: list[dict]) -> list[dict]:
    candidates = []
    for s in schools:
        if s.get("lat") is None or s.get("lon") is None:
            continue
        dist_m = haversine_km(lat, lon, s["lat"], s["lon"]) * 1000.0
        if dist_m <= config.SCHOOL_CANDIDATE_RADIUS_M:
            candidates.append((dist_m, s))
    candidates.sort(key=lambda pair: pair[0])
    return [
        {
            "name": s["name"],
            "external_id": s.get("external_id"),
            "distance_m": round(d),
        }
        for d, s in candidates[: config.SCHOOL_CANDIDATE_MAX]
    ]


def _build_assets(
    reports: list[dict],
    *,
    facilities_by_category: dict[str, list[dict]],
    works_by_village: dict[str, list[dict]] | None = None,
    gazetteer: list[dict],
    amenity_index: list[dict],
    works_index: list[dict],
    gw_stations: list[dict],
    road_segments: list[dict] | None = None,
    water_testing_index: dict[int, dict] | None = None,
    river_readings: list[dict] | None = None,
    hazard_alerts: list[dict] | None = None,
    school_condition_index: dict[int, dict] | None = None,
    db=None,
) -> list[Asset]:
    valid_reports = [
        r for r in reports if r.get("latitude") is not None and r.get("longitude") is not None
    ]
    by_id, by_full, by_dist_village, by_village = _index_gazetteer(gazetteer)
    facilities_by_id = {
        f["id"]: f
        for flist in facilities_by_category.values()
        for f in flist
        if f.get("id") is not None
    }

    asset_groups: dict[tuple, list[tuple[dict, str]]] = {}
    road_water_reports: dict[tuple[tuple, str], list[dict]] = {}

    for r in valid_reports:
        cat = r.get("issue_category")
        g = _lookup_gazetteer_entry(r, by_full, by_dist_village, by_village)
        v_name = g["name"] if g else (r.get("village") or "Unknown")
        village_key = (g["id"],) if g else (r.get("district", "").lower(), r.get("block", "").lower(), v_name.lower())

        assigned = False
        if cat in ("education", "health"):
            # Rule 1: Citizen-selected facility
            fid = r.get("facility_id")
            if fid is not None and fid in facilities_by_id:
                fac = facilities_by_id[fid]
                if fac.get("category") == cat:
                    # A facility attached by the demo-seeding script was never
                    # picked by a citizen; labelling it "citizen picked" would
                    # claim a choice nobody made.
                    basis = "demo_assigned" if r.get("pin_source") == "synthetic_seed" else "citizen_selected"
                    asset_groups.setdefault(("facility", fac["id"]), []).append((r, basis))
                    assigned = True

            # Rule 2: Pin next to a facility (within ASSET_PIN_SNAP_M)
            if not assigned and r.get("precise_lat") is not None and r.get("precise_lon") is not None:
                plat, plon = r["precise_lat"], r["precise_lon"]
                facs_in_snap = []
                for fac in facilities_by_category.get(cat, []):
                    if fac.get("lat") is not None and fac.get("lon") is not None:
                        dist_m = haversine_km(plat, plon, fac["lat"], fac["lon"]) * 1000.0
                        if dist_m <= config.ASSET_PIN_SNAP_M:
                            facs_in_snap.append((dist_m, fac))
                if facs_in_snap:
                    facs_in_snap.sort(key=lambda pair: pair[0])
                    asset_groups.setdefault(("facility", facs_in_snap[0][1]["id"]), []).append(
                        (r, "nearest_register")
                    )
                    assigned = True

            # Rule 3: Health with no pin (within HEALTH_NEAREST_MAX_KM)
            if not assigned and cat == "health":
                rlat, rlon = r["latitude"], r["longitude"]
                facs_in_health = []
                for fac in facilities_by_category.get("health", []):
                    if fac.get("lat") is not None and fac.get("lon") is not None:
                        dist_km = haversine_km(rlat, rlon, fac["lat"], fac["lon"])
                        if dist_km <= config.HEALTH_NEAREST_MAX_KM:
                            facs_in_health.append((dist_km, fac))
                if facs_in_health:
                    facs_in_health.sort(key=lambda pair: pair[0])
                    asset_groups.setdefault(("facility", facs_in_health[0][1]["id"]), []).append(
                        (r, "nearest_register")
                    )
                    assigned = True
                else:
                    asset_groups.setdefault(("unresolved", village_key, "health"), []).append(
                        (r, "unresolved_village")
                    )
                    assigned = True

            # Rule 4: Education with no facility and no pin
            if not assigned and cat == "education":
                asset_groups.setdefault(("unresolved", village_key, "education"), []).append(
                    (r, "unresolved_village")
                )
                assigned = True

        if not assigned:
            # Rule 5: Road and water (or other categories)
            road_water_reports.setdefault((village_key, cat), []).append(r)

    # Road and water grouping: ASSET_GROUP_RADIUS_M single-linkage per (village, category)
    for (v_key, cat), cat_reports in road_water_reports.items():
        groups = _group_by_radius(cat_reports, config.ASSET_GROUP_RADIUS_M)
        for idx, grp in enumerate(groups):
            key = ("group", v_key, cat, idx, len(groups))
            asset_groups[key] = [(m, "unnamed_pin") for m in grp]

    created_assets: list[Asset] = []
    synthetic_id_counter = 1
    # Two complaint spots on the same PMGSY road would otherwise get the
    # same name; the second and later ones are numbered.
    segment_uses: Counter = Counter()

    for key, member_pairs in asset_groups.items():
        members = [p[0] for p in member_pairs]
        name_bases = [p[1] for p in member_pairs]
        road_geometry = None

        if key[0] == "facility":
            fid = key[1]
            fac = facilities_by_id[fid]
            cat = fac["category"]
            name = fac["name"]
            if "citizen_selected" in name_bases:
                name_basis = "citizen_selected"
            elif "demo_assigned" in name_bases:
                name_basis = "demo_assigned"
            else:
                name_basis = "nearest_register"
            facility_id = fac["id"]
            source = fac.get("source")
            external_id = fac.get("external_id")
            lat = fac["lat"]
            lon = fac["lon"]
            location_basis = "register_coordinates"
            candidates = None

            v_names = [m.get("village") for m in members if m.get("village")]
            primary_v_name = Counter(v_names).most_common(1)[0][0] if v_names else fac.get("village") or "Unknown"
            matching_m = next((m for m in members if m.get("village") == primary_v_name), members[0])
            pg = _lookup_gazetteer_entry(matching_m, by_full, by_dist_village, by_village)
            primary_gazetteer_id = pg["id"] if pg else None
            village = pg["name"] if pg else primary_v_name
            block = pg["block"] if pg else matching_m.get("block")
            district = pg["district"] if pg else matching_m.get("district")
            villages_served = sorted(list({m.get("village") for m in members if m.get("village")}))

        elif key[0] == "unresolved":
            v_key, cat = key[1], key[2]
            matching_m = members[0]
            g = by_id.get(v_key[0]) if (isinstance(v_key, tuple) and len(v_key) == 1 and isinstance(v_key[0], int)) else None
            if not g:
                g = _lookup_gazetteer_entry(matching_m, by_full, by_dist_village, by_village)
            primary_gazetteer_id = g["id"] if g else None
            village = g["name"] if g else (matching_m.get("village") or "Unknown")
            block = g["block"] if g else matching_m.get("block")
            district = g["district"] if g else matching_m.get("district")
            villages_served = [village]
            facility_id = None
            source = None
            external_id = None
            lat = sum(m["latitude"] for m in members) / len(members)
            lon = sum(m["longitude"] for m in members) / len(members)
            location_basis = _determine_location_basis(members)
            name_basis = "unresolved_village"

            if cat == "education":
                name = f"School in {village} (not specified)"
                vlat = g["lat"] if g and g.get("lat") is not None else lat
                vlon = g["lon"] if g and g.get("lon") is not None else lon
                candidates = _find_school_candidates(vlat, vlon, facilities_by_category.get("education", []))
            else:
                name = f"Health facility in {village} (not specified)"
                candidates = None

        elif key[0] == "group":
            v_key, cat, grp_idx, total_grps = key[1], key[2], key[3], key[4]
            matching_m = members[0]
            g = by_id.get(v_key[0]) if (isinstance(v_key, tuple) and len(v_key) == 1 and isinstance(v_key[0], int)) else None
            if not g:
                g = _lookup_gazetteer_entry(matching_m, by_full, by_dist_village, by_village)
            primary_gazetteer_id = g["id"] if g else None
            village = g["name"] if g else (matching_m.get("village") or "Unknown")
            block = g["block"] if g else matching_m.get("block")
            district = g["district"] if g else matching_m.get("district")
            villages_served = [village]
            facility_id = None
            lat = sum(m["latitude"] for m in members) / len(members)
            lon = sum(m["longitude"] for m in members) / len(members)
            location_basis = _determine_location_basis(members)

            if cat == "road":
                works = (works_by_village or {}).get(village) or (works_by_village or {}).get(village.lower()) or []
                # A village-centre coordinate is not where the problem is, so
                # the road that happens to pass near the centre is not "the"
                # road; only a real pin is matched to a GeoSadak segment.
                segment, distance_m = (None, None)
                if road_segments and location_basis != "village_centroid":
                    segment, distance_m = realdata.nearest_road_segment(lat, lon, road_segments)
                if segment:
                    base_name = (
                        (segment.get("road_name") or "").strip()
                        or segment.get("drrp_road_code")
                        or f"Road near {village}"
                    )
                    segment_uses[segment["id"]] += 1
                    uses = segment_uses[segment["id"]]
                    name = base_name if uses == 1 else f"{base_name} #{uses}"
                    name_basis = "geosadak_segment"
                    source = "pmgsy_geosadak"
                    external_id = segment.get("external_id")
                    candidates = None
                    road_geometry = {
                        "segment_id": segment["id"],
                        "external_id": segment.get("external_id"),
                        "road_name": segment.get("road_name"),
                        "drrp_road_code": segment.get("drrp_road_code"),
                        "road_category": segment.get("road_category"),
                        "road_owner": segment.get("road_owner"),
                        "distance_m": round(distance_m, 1),
                        "points": segment["points"],
                    }
                elif len(works) == 1 and total_grps == 1:
                    name = works[0]["name"]
                    name_basis = "pmgsy_work"
                    source = "pmgsy"
                    external_id = works[0].get("external_id")
                    candidates = None
                else:
                    name = f"Road near {village}" if grp_idx == 0 else f"Road near {village} #{grp_idx + 1}"
                    name_basis = "unnamed_pin"
                    source = None
                    external_id = None
                    if works:
                        candidates = [
                            {
                                "name": w["name"],
                                "external_id": w.get("external_id"),
                                "detail": " · ".join(
                                    part for part in (
                                        w.get("status"),
                                        f"Rs {w['cost_lakh']:,.2f} lakh" if w.get("cost_lakh") else None,
                                        f"sanctioned {w['year']}" if w.get("year") else None,
                                    ) if part
                                ),
                                "distance_m": None,
                            }
                            for w in works[:config.SCHOOL_CANDIDATE_MAX]
                        ]
                    else:
                        candidates = None
            else:
                # water
                name = f"Water point near {village}" if grp_idx == 0 else f"Water point near {village} #{grp_idx + 1}"
                name_basis = "unnamed_pin"
                source = None
                external_id = None
                candidates = None

        result = _score_members(
            members,
            lat,
            lon,
            cat,
            gazetteer=gazetteer,
            amenity_index=amenity_index,
            works_index=works_index,
            gw_stations=gw_stations,
            water_testing_index=water_testing_index,
            river_readings=river_readings,
            hazard_alerts=hazard_alerts,
            facility_id=facility_id,
            school_condition_index=school_condition_index,
            groups=None,
        )
        evidence = dict(result["evidence"])
        evidence["name_basis"] = name_basis
        evidence["location_basis"] = location_basis
        if candidates is not None:
            evidence["candidates"] = candidates
        evidence["villages_served"] = villages_served
        if road_geometry is not None:
            evidence["road_geometry"] = road_geometry

        is_demo = all(bool(m.get("is_synthetic")) for m in members)

        asset = Asset(
            asset_type=cat,
            name=name,
            name_basis=name_basis,
            facility_id=facility_id,
            source=source,
            external_id=external_id,
            latitude=lat,
            longitude=lon,
            location_basis=location_basis,
            primary_gazetteer_id=primary_gazetteer_id,
            village=village,
            block=block,
            district=district,
            villages_served=json.dumps(villages_served),
            report_count=len(members),
            distinct_reporters=_unique_reporters(members),
            priority_score=result["priority_score"],
            breakdown=json.dumps(result["breakdown"]),
            evidence=json.dumps(evidence, default=str),
            candidates=json.dumps(candidates, default=str) if candidates is not None else None,
            is_demo=is_demo,
        )

        if db is not None:
            db.add(asset)
            db.flush()
            for m in members:
                m["asset_id"] = asset.id
                db.query(CitizenRequest).filter(CitizenRequest.id == m["id"]).update(
                    {CitizenRequest.asset_id: asset.id}, synchronize_session=False
                )
        else:
            asset.id = synthetic_id_counter
            synthetic_id_counter += 1
            for m in members:
                m["asset_id"] = asset.id

        created_assets.append(asset)

    if db is not None:
        db.commit()

    return created_assets


def _build_villages(
    reports: list[dict],
    assets: list[Asset],
    gazetteer: list[dict],
    *,
    db=None,
) -> list[VillagePriority]:
    by_id, by_full, by_dist_village, by_village = _index_gazetteer(gazetteer)
    assets_by_id = {a.id: a for a in assets if a.id is not None}

    reports_by_gid: dict[int, list[dict]] = {}
    for r in reports:
        g = _lookup_gazetteer_entry(r, by_full, by_dist_village, by_village)
        if g and g.get("id") is not None:
            reports_by_gid.setdefault(g["id"], []).append(r)

    created_villages: list[VillagePriority] = []
    synthetic_id_counter = 1

    for gid, v_reports in reports_by_gid.items():
        g = by_id[gid]
        v_asset_ids = {r.get("asset_id") for r in v_reports if r.get("asset_id") is not None}
        v_assets = [assets_by_id[aid] for aid in v_asset_ids if aid in assets_by_id]

        if v_assets:
            top_asset = max(v_assets, key=lambda a: (a.priority_score, a.id or 0))
            priority_score = top_asset.priority_score
            top_asset_id = top_asset.id
        else:
            top_asset = None
            priority_score = 0.0
            top_asset_id = None

        counts_by_cat = dict(Counter(r.get("issue_category") for r in v_reports if r.get("issue_category")))
        is_demo = all(bool(r.get("is_synthetic")) for r in v_reports)

        vp = VillagePriority(
            gazetteer_id=g["id"],
            village=g["name"],
            block=g.get("block"),
            district=g.get("district"),
            latitude=g.get("lat"),
            longitude=g.get("lon"),
            population=g.get("population"),
            report_count=len(v_reports),
            counts_by_category=json.dumps(counts_by_cat),
            asset_count=len(v_assets),
            priority_score=priority_score,
            top_asset_id=top_asset_id,
            rank_in_district=None,
            is_demo=is_demo,
        )
        created_villages.append(vp)

    # Rank villages by priority_score descending within district
    by_district: dict[str, list[VillagePriority]] = {}
    for vp in created_villages:
        by_district.setdefault(vp.district or "Unknown", []).append(vp)

    for dist_villages in by_district.values():
        dist_villages.sort(key=lambda v: (-v.priority_score, -v.report_count, v.village or ""))
        for rank, v in enumerate(dist_villages, start=1):
            v.rank_in_district = rank

    if db is not None:
        for vp in created_villages:
            db.add(vp)
        db.commit()
    else:
        for vp in created_villages:
            vp.id = synthetic_id_counter
            synthetic_id_counter += 1

    return created_villages


def recompute(db, verbose: bool = True) -> dict:
    """Run the full P3 pass. Returns a summary dict."""

    def log(message: str) -> None:
        if verbose:
            print(message)

    reports = _load_reports(db)
    reports = _apply_precise_coords(reports)
    gazetteer = _load_gazetteer(db)
    log(f"loaded {len(reports)} reports, {len(gazetteer)} gazetteer rows")

    # Real government data, loaded once and reused for every cluster. If the
    # loaders have not been run these come back empty and every term falls
    # back to its proxy -- the engine degrades rather than failing.
    amenity_index = realdata.load_amenity_index(db)
    works_index = realdata.load_works_index(db)
    gw_stations = realdata.load_groundwater_index(db)
    road_segments = realdata.load_road_segment_index(db)
    water_testing_index = realdata.load_water_testing_index(db)
    river_readings = realdata.load_river_readings_index(db)
    hazard_alerts = realdata.load_hazard_alerts_index(db)
    school_condition_index = realdata.load_school_condition_index(db)

    # Named public assets, so a work group can say "Z.P.SCHOOL DABHADI"
    # instead of "Dabhadi". Empty until load_udise_schools.py has been run,
    # in which case groups keep their village label and say so.
    facility_index = realdata.load_facility_index(db)
    facilities_by_category: dict[str, list[dict]] = {}
    for facility in facility_index:
        facilities_by_category.setdefault(facility["category"], []).append(facility)

    # PMGSY works keyed by the village name they were matched to, so a road
    # group can name the actual sanctioned work rather than measure distances
    # to a road that has no point geometry.
    village_name_by_id = {v["gazetteer_id"]: v["name"] for v in amenity_index}
    works_by_village: dict[str, list[dict]] = {}
    for work in works_index:
        name = village_name_by_id.get(work["gazetteer_id"])
        if name:
            works_by_village.setdefault(name, []).append(work)

    log(
        f"named assets: {len(facility_index)} facilities, "
        f"{len(works_by_village)} villages with a sanctioned road work, "
        f"{len(road_segments)} PMGSY GeoSadak road segments"
    )
    with_records = sum(1 for v in amenity_index if v.get("has_real_data"))
    log(
        f"real data: {with_records}/{len(amenity_index)} villages with government "
        f"records, {len(works_index)} sanctioned works pinned to a village, "
        f"{len(gw_stations)} groundwater telemetry stations, "
        f"{len(river_readings)} CWC river readings, {len(hazard_alerts)} SACHET alerts, "
        f"{len(school_condition_index)} UDISE+ school condition records"
    )

    # --- Steps 1-3: embed, gate, cluster -----------------------------------
    assignments = cluster_all(reports)
    log(f"clustered {len(assignments)} reports into groups")

    grouped: dict[tuple[str, int], list[dict]] = {}
    by_id = {r["id"]: r for r in reports}
    for report_id, key in assignments.items():
        grouped.setdefault(key, []).append(by_id[report_id])

    attached = _attach_stragglers(reports, assignments, grouped, by_id)
    log(f"attached {attached} further reports to established clusters")

    # --- clear the previous pass -------------------------------------------
    db.query(CitizenRequest).update(
        {CitizenRequest.cluster_id: None, CitizenRequest.asset_id: None},
        synchronize_session=False,
    )
    db.query(PriorityScore).delete(synchronize_session=False)
    # Work groups belong to clusters that are about to be deleted, so they go
    # first -- otherwise the next pass accumulates orphans pointing at cluster
    # ids that have been reused for something else entirely.
    db.query(WorkGroup).delete(synchronize_session=False)
    db.query(DemandCluster).delete(synchronize_session=False)
    db.query(VillagePriority).delete(synchronize_session=False)
    db.query(Asset).delete(synchronize_session=False)
    db.commit()

    # --- Steps 4-7: population, gap score, priority score, breakdown -------
    scored: list[tuple[DemandCluster, dict]] = []

    for (category, _label), members in grouped.items():
        centroid_lat = sum(m["latitude"] for m in members) / len(members)
        centroid_lon = sum(m["longitude"] for m in members) / len(members)

        population_affected = population_in_catchment(
            centroid_lat, centroid_lon, category, gazetteer
        )
        confidences = [m["confidence"] for m in members if m["confidence"] is not None]
        average_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        cluster = DemandCluster(
            issue_category=category,
            centroid_lat=centroid_lat,
            centroid_lon=centroid_lon,
            report_count=len(members),
            unique_reporters=_unique_reporters(members),
            population_affected=population_affected,
            district=_modal([m["district"] for m in members]),
            block=_modal([m["block"] for m in members]),
            avg_confidence=round(average_confidence, 4),
            settlement_count=_settlement_count(members),
        )
        db.add(cluster)
        db.flush()  # assign cluster.id

        for member in members:
            db.query(CitizenRequest).filter(CitizenRequest.id == member["id"]).update(
                {CitizenRequest.cluster_id: cluster.id}, synchronize_session=False
            )

        # Which specific things inside this cluster are broken, and -- where
        # the registers can establish it -- what each one is actually called.
        groups = _work_groups(members)
        for group in groups:
            named = realdata.name_work_group(
                group["centroid_lat"],
                group["centroid_lon"],
                category,
                facilities_by_category.get(category, []),
                works_by_village=works_by_village,
                village=group.get("village"),
            )
            db.add(
                WorkGroup(
                    cluster_id=cluster.id,
                    asset_label=named["label"],
                    asset_source=named["source"],
                    asset_external_id=named["external_id"],
                    asset_candidates=json.dumps(named["candidates"], default=str),
                    **group,
                )
            )

        result = _score_members(
            members,
            centroid_lat,
            centroid_lon,
            category,
            gazetteer=gazetteer,
            amenity_index=amenity_index,
            works_index=works_index,
            gw_stations=gw_stations,
            water_testing_index=water_testing_index,
            river_readings=river_readings,
            hazard_alerts=hazard_alerts,
            groups=groups,
        )
        scored.append((cluster, result))

    db.commit()
    log(f"created {len(scored)} clusters")

    # --- Step 8: runner-up / counterfactual --------------------------------
    # Sorted descending, each cluster's runner-up is simply the next one down,
    # so P4 can render "why not this other one" without a second query.
    scored.sort(key=lambda pair: pair[1]["priority_score"], reverse=True)

    for index, (cluster, result) in enumerate(scored):
        runner_up = scored[index + 1][0].id if index + 1 < len(scored) else None
        db.add(
            PriorityScore(
                cluster_id=cluster.id,
                priority_score=result["priority_score"],
                breakdown=json.dumps(result["breakdown"]),
                evidence=json.dumps(result["evidence"], default=str),
                runner_up_cluster_id=runner_up,
            )
        )

    db.commit()

    # --- Steps 9-10: build assets and village priorities -------------------
    assets = _build_assets(
        reports,
        facilities_by_category=facilities_by_category,
        works_by_village=works_by_village,
        gazetteer=gazetteer,
        amenity_index=amenity_index,
        works_index=works_index,
        gw_stations=gw_stations,
        road_segments=road_segments,
        water_testing_index=water_testing_index,
        river_readings=river_readings,
        hazard_alerts=hazard_alerts,
        school_condition_index=school_condition_index,
        db=db,
    )
    villages = _build_villages(reports, assets, gazetteer, db=db)
    log(f"created {len(assets)} assets across {len(villages)} villages")

    summary = {
        "reports_total": len(reports),
        "reports_clustered": len(assignments),
        "clusters_created": len(scored),
        "assets_created": len(assets),
        "villages_created": len(villages),
        "top_score": scored[0][1]["priority_score"] if scored else None,
        "categories": dict(Counter(c.issue_category for c, _ in scored)),
    }
    log(f"summary: {summary}")
    return summary


def main() -> None:
    from database import SessionLocal

    db = SessionLocal()
    try:
        recompute(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
