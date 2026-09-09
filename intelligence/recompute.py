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
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "backend"))

from models import (  # noqa: E402
    CitizenRequest,
    DemandCluster,
    Gazetteer,
    PriorityScore,
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
            "confidence": row.confidence,
        }
        for row in db.query(CitizenRequest).all()
    ]


def _load_gazetteer(db) -> list[dict]:
    return [
        {
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
    groups: list[list[dict]] = []

    def near(a: dict, b: dict) -> bool:
        return haversine_km(
            a["latitude"], a["longitude"], b["latitude"], b["longitude"]
        ) * 1000.0 <= WORK_GROUP_RADIUS_M

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
            }
        )

    out.sort(key=lambda g: g["report_count"], reverse=True)
    return out


def recompute(db, verbose: bool = True) -> dict:
    """Run the full P3 pass. Returns a summary dict."""

    def log(message: str) -> None:
        if verbose:
            print(message)

    reports = _load_reports(db)
    gazetteer = _load_gazetteer(db)
    log(f"loaded {len(reports)} reports, {len(gazetteer)} gazetteer rows")

    # Real government data, loaded once and reused for every cluster. If the
    # loaders have not been run these come back empty and every term falls
    # back to its proxy -- the engine degrades rather than failing.
    amenity_index = realdata.load_amenity_index(db)
    works_index = realdata.load_works_index(db)

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
        f"{len(works_by_village)} villages with a sanctioned road work"
    )
    with_records = sum(1 for v in amenity_index if v.get("has_real_data"))
    log(
        f"real data: {with_records}/{len(amenity_index)} villages with government "
        f"records, {len(works_index)} sanctioned works pinned to a village"
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
    db.query(CitizenRequest).update({CitizenRequest.cluster_id: None}, synchronize_session=False)
    db.query(PriorityScore).delete(synchronize_session=False)
    # Work groups belong to clusters that are about to be deleted, so they go
    # first -- otherwise the next pass accumulates orphans pointing at cluster
    # ids that have been reused for something else entirely.
    db.query(WorkGroup).delete(synchronize_session=False)
    db.query(DemandCluster).delete(synchronize_session=False)
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

        # --- what the government's own records say about this place --------
        nearby_villages = realdata.villages_near(centroid_lat, centroid_lon, amenity_index)

        infra_value, infra_evidence = realdata.real_infra_deficit(
            category, nearby_villages, works_index, population_affected
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
            unique_reporters=cluster.unique_reporters,
            population_affected=population_affected,
            settlement_count=cluster.settlement_count,
            severities=[m["severity"] for m in members],
            average_confidence=average_confidence,
            issue_category=category,
            block=cluster.block,
            distance_to_hq_km=nearest_hq_distance_km(centroid_lat, centroid_lon, gazetteer),
            real_infra_deficit=infra_value,
            real_vulnerability=vulnerability_value,
            real_reporting_deficit=reporting_value,
            # Only assert eligibility when there were records to judge it on;
            # "we have no data" must not be recorded as "not eligible".
            scheme_eligible=eligible if eligibility_evidence else None,
            real_hq_distance_km=hq_km,
        )
        # Everything a reader needs to check a term rather than take it on
        # trust: which villages were counted and how many people live in them,
        # which scheme and which rule, what a comparable work has actually
        # cost. All of it was already computed to produce the score.
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
                "distinct_reporters": cluster.unique_reporters,
                "total_reports": len(members),
                "saturates_at": config.DEMAND_SATURATION_REPORTERS,
                "settlements": cluster.settlement_count,
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
            "work_groups": len(groups),
            "villages_examined": len(nearby_villages),
        }
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

    summary = {
        "reports_total": len(reports),
        "reports_clustered": len(assignments),
        "clusters_created": len(scored),
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
