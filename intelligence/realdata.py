"""
Turns the loaded government data into the inputs the Priority Engine scores on.

This module is the bridge between `backend/load_*.py` (which fetch real
records) and `scoring.py` (which ranks). Before it existed, four of the nine
scoring terms were computed from proxies: infrastructure condition came from
the severity a citizen asserted, vulnerability from settlement size, equity
from a hardcoded list of ten block names, and scheme eligibility from a bare
population threshold.

Every function here answers the same question in a different domain: **what do
the government's own records say about the places this cluster covers?**

DESIGN RULES
------------
1. Each function returns `(value, evidence)`. The evidence dict is not
   decoration -- it is what lets the dashboard say *why* a number is what it
   is, and it is the difference between "trust the score" and "audit the
   score" (research report §16.2).

2. Missing data returns None, never 0.0. A village with no recorded amenities
   is not a village with no amenities. Callers fall back to the old proxy and
   pay a confidence penalty for doing so, rather than silently scoring a
   fabricated deficit (§16.1).

3. Where a norm exists, the deficit is measured AGAINST THE NORM, not against
   other villages. "Short of the IPHS standard by this much" is a statement a
   government officer can act on; "worse than average" is not.

MEASURED LIMITS OF THE UNDERLYING DATA (see REAL_DATA_RESEARCH.md §5.5)
----------------------------------------------------------------------
* Census all-weather road: only 12 of 942 villages fail it -- too flat to rank
  on, so the road deficit leans on road *surface* and on PMGSY's undelivered
  works instead.
* Census primary school: all 942 have one -- the RTE primary test finds no
  violations here, so education leans on middle/secondary.
* Census water (2011) predates JJM (2019) and overstates water need; JJM
  coverage is preferred wherever it has been crawled.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta


from . import config

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

AMENITY_FIELDS = (
    "households",
    "road_black_topped",
    "road_all_weather",
    "road_gravel",
    "water_tap_treated",
    "water_tap_treated_summer",
    "jjm_tap_coverage_pct",
    "jjm_households",
    "health_phc_count",
    "health_chc_count",
    "health_subcentre_count",
    "health_doctors_sanctioned",
    "health_doctors_in_position",
    "health_nearest_band",
    "school_primary",
    "school_middle",
    "school_secondary",
    "conn_internet_csc",
    "conn_mobile_coverage",
    "conn_bharatnet_status",
    "dist_subdistrict_hq_km",
    "dist_district_hq_km",
    "dist_nearest_town_km",
    "power_domestic_summer_hrs",
    "drainage_none",
)


def load_amenity_index(db) -> list[dict]:
    """
    Every gazetteer village that has coordinates, with its recorded amenities
    attached where we have them.

    Imported lazily so the intelligence package stays importable without the
    backend directory on sys.path.
    """
    from models import Gazetteer, VillageAmenities  # noqa: E402

    amenities_by_village: dict[int, object] = {
        row.gazetteer_id: row for row in db.query(VillageAmenities).all()
    }

    index = []
    for village in db.query(Gazetteer).all():
        if village.latitude is None or village.longitude is None:
            continue
        record = {
            "gazetteer_id": village.id,
            "name": village.name,
            "district": village.district,
            "block": village.block,
            "population": village.population,
            "lat": village.latitude,
            "lon": village.longitude,
            "has_real_data": False,
        }
        amenities = amenities_by_village.get(village.id)
        if amenities is not None:
            record["has_real_data"] = True
            for field in AMENITY_FIELDS:
                record[field] = getattr(amenities, field, None)
        index.append(record)
    return index


def load_works_index(db) -> list[dict]:
    """Sanctioned government works that are pinned to a village."""
    from models import GovernmentProject  # noqa: E402

    works = []
    for work in db.query(GovernmentProject).all():
        if work.matched_gazetteer_id is None:
            continue
        works.append(
            {
                "gazetteer_id": work.matched_gazetteer_id,
                "status": work.work_status,
                "year": work.sanctioned_year,
                "cost_lakh": work.sanctioned_cost_lakh,
                "length_km": work.length_km,
                "scheme": work.scheme_name,
                "name": work.work_name,
                "external_id": work.external_id,
            }
        )
    return works


def cost_benchmark(category: str | None, works: list[dict]) -> dict:
    """
    What this kind of work has actually cost the government here.

    "A scheme exists" is only half an answer for someone holding a budget --
    the next question is always how much. Rather than quoting a national
    outlay we do not hold, this reports the median sanctioned cost per
    kilometre from the PMGSY works already in this database, and says how many
    works it was computed from so a reader can judge how much to trust it.

    Roads only: PMGSY is the one scheme whose per-unit costs we actually have.
    Returning an empty dict for the other sectors is the honest answer, and
    the panel renders nothing rather than inventing a figure.
    """
    if category != "road":
        return {}

    priced = [
        w for w in works
        if (w.get("cost_lakh") or 0) > 0 and (w.get("length_km") or 0) > 0
    ]
    if len(priced) < 5:
        return {}

    per_km = sorted(w["cost_lakh"] / w["length_km"] for w in priced)
    middle = len(per_km) // 2
    median = (
        per_km[middle]
        if len(per_km) % 2
        else (per_km[middle - 1] + per_km[middle]) / 2
    )
    return {
        "scheme": "PMGSY",
        "median_cost_lakh_per_km": round(median, 2),
        "works_sampled": len(priced),
        "source": "sanctioned costs of PMGSY works in this database",
    }


def villages_near(lat: float, lon: float, index: list[dict], radius_km: float | None = None):
    """Villages within the amenity lookup radius of a point."""
    radius = radius_km if radius_km is not None else config.AMENITY_LOOKUP_RADIUS_KM
    return [
        village
        for village in index
        if haversine_km(lat, lon, village["lat"], village["lon"]) <= radius
    ]


def _with_data(villages: list[dict]) -> list[dict]:
    return [v for v in villages if v.get("has_real_data")]


def _share(villages: list[dict], field: str, absent_value=0) -> float | None:
    """Share of villages whose `field` equals `absent_value` (i.e. lacking it)."""
    known = [v for v in villages if v.get(field) is not None]
    if not known:
        return None
    return sum(1 for v in known if v[field] == absent_value) / len(known)


# ---------------------------------------------------------------------------
# infra_deficit -- per category, measured against the real record
# ---------------------------------------------------------------------------


def _road_deficit(villages: list[dict], works: list[dict]) -> tuple[float, dict]:
    """
    Road condition from what is actually recorded.

    The obvious test -- "does it have an all-weather road" -- was measured
    after loading and fails for only 12 of 942 villages, so it cannot carry
    this on its own. Surface quality (black-topped vs gravel) varies more, and
    an undelivered PMGSY work is direct evidence that the government itself
    has already judged this road inadequate.
    """
    evidence: dict = {}
    signals: list[float] = []

    no_all_weather = _share(villages, "road_all_weather")
    if no_all_weather is not None:
        evidence["share_without_all_weather_road"] = round(no_all_weather, 3)
        signals.append(no_all_weather)

    no_black_top = _share(villages, "road_black_topped")
    if no_black_top is not None:
        evidence["share_without_black_topped_road"] = round(no_black_top, 3)
        signals.append(no_black_top)

    village_ids = {v["gazetteer_id"] for v in villages}
    undelivered = [
        w
        for w in works
        if w["gazetteer_id"] in village_ids
        and w["status"] in {"Not Started", "In Progress", "Agreement Cancelled"}
    ]
    if undelivered:
        oldest = min((w["year"] for w in undelivered if w["year"]), default=None)
        evidence["undelivered_sanctioned_works"] = len(undelivered)
        evidence["undelivered_sanctioned_cost_lakh"] = round(
            sum(w["cost_lakh"] or 0 for w in undelivered), 2
        )
        if oldest:
            evidence["oldest_undelivered_sanction_year"] = oldest
        # A sanctioned-but-unbuilt road is the government's own finding that
        # this connection is missing. Treated as a strong, saturating signal.
        signals.append(min(1.0, 0.5 + 0.1 * len(undelivered)))

    if not signals:
        return 0.0, evidence
    return max(signals), evidence


def _water_deficit(villages: list[dict]) -> tuple[float, dict]:
    """
    Water: JJM tap coverage where crawled, Census only as a fallback.

    Order matters. Census water columns are from 2011 and JJM has since
    connected most of these households -- scoring on Census alone measurably
    overstates water need (REAL_DATA_RESEARCH.md §5.5).
    """
    evidence: dict = {}

    covered = [v for v in villages if v.get("jjm_tap_coverage_pct") is not None]
    if covered:
        households = sum(v.get("jjm_households") or 0 for v in covered)
        if households > 0:
            weighted = sum(
                (v["jjm_tap_coverage_pct"] / 100.0) * (v.get("jjm_households") or 0)
                for v in covered
            )
            coverage = weighted / households
        else:
            coverage = sum(v["jjm_tap_coverage_pct"] for v in covered) / len(covered) / 100.0
        evidence["jjm_tap_coverage_pct"] = round(coverage * 100, 1)
        evidence["jjm_villages_measured"] = len(covered)
        evidence["source"] = "jal_jeevan_mission_current"
        return max(0.0, min(1.0, 1.0 - coverage)), evidence

    no_treated = _share(villages, "water_tap_treated")
    fails_summer = _share(villages, "water_tap_treated_summer")
    signals = [s for s in (no_treated, fails_summer) if s is not None]
    if not signals:
        return 0.0, evidence

    if no_treated is not None:
        evidence["share_without_treated_tap"] = round(no_treated, 3)
    if fails_summer is not None:
        evidence["share_failing_in_summer"] = round(fails_summer, 3)
    evidence["source"] = "census_2011_may_overstate"
    return max(signals), evidence


def _health_deficit(villages: list[dict], population: int) -> tuple[float, dict]:
    """
    Health measured against the IPHS population norms, not against other
    villages: "this many people per facility, against a standard of N".
    """
    evidence: dict = {}
    with_data = _with_data(villages)
    if not with_data:
        return 0.0, evidence

    subcentres = sum(v.get("health_subcentre_count") or 0 for v in with_data)
    phcs = sum(v.get("health_phc_count") or 0 for v in with_data)
    chcs = sum(v.get("health_chc_count") or 0 for v in with_data)
    evidence["facilities"] = {"sub_centre": subcentres, "phc": phcs, "chc": chcs}

    signals: list[float] = []
    if population > 0:
        # Entitled facilities under IPHS vs what exists. Ratio > 1 means short.
        entitled_sc = population / config.IPHS_POPULATION_PER_SUBCENTRE
        if entitled_sc > 0:
            shortfall = max(0.0, (entitled_sc - subcentres) / entitled_sc)
            evidence["iphs_subcentre_entitled"] = round(entitled_sc, 2)
            evidence["iphs_subcentre_shortfall"] = round(shortfall, 3)
            signals.append(shortfall)

        entitled_phc = population / config.IPHS_POPULATION_PER_PHC
        if entitled_phc >= 1:
            shortfall = max(0.0, (entitled_phc - phcs) / entitled_phc)
            evidence["iphs_phc_entitled"] = round(entitled_phc, 2)
            signals.append(shortfall)

    far = [v for v in with_data if v.get("health_nearest_band") in {"b", "c"}]
    if far:
        share = len(far) / len(with_data)
        evidence["share_with_facility_over_5km"] = round(share, 3)
        signals.append(share)

    sanctioned = sum(v.get("health_doctors_sanctioned") or 0 for v in with_data)
    in_position = sum(v.get("health_doctors_in_position") or 0 for v in with_data)
    if sanctioned > 0:
        vacancy = max(0.0, (sanctioned - in_position) / sanctioned)
        evidence["doctor_vacancy_rate"] = round(vacancy, 3)
        evidence["doctors"] = {"sanctioned": sanctioned, "in_position": in_position}
        signals.append(vacancy)

    if not signals:
        return 0.0, evidence
    return max(0.0, min(1.0, max(signals))), evidence


def _education_deficit(villages: list[dict]) -> tuple[float, dict]:
    """
    Education against the RTE Act's neighbourhood-school duty.

    Primary school coverage is complete in these districts (all 942 villages
    have one), so a primary-level breach cannot be shown here and the signal
    comes from middle and secondary provision.
    """
    evidence: dict = {}
    signals: list[float] = []

    no_middle = _share(villages, "school_middle")
    if no_middle is not None:
        evidence["share_without_middle_school"] = round(no_middle, 3)
        evidence["rte_upper_primary_limit_km"] = config.RTE_UPPER_PRIMARY_MAX_KM
        signals.append(no_middle)

    no_secondary = _share(villages, "school_secondary")
    if no_secondary is not None:
        evidence["share_without_secondary_school"] = round(no_secondary, 3)
        # Secondary schooling is outside the RTE duty, so it is weighted below
        # a middle-school gap rather than treated as an equal breach.
        signals.append(no_secondary * 0.7)

    if not signals:
        return 0.0, evidence
    return max(0.0, min(1.0, max(signals))), evidence


def real_infra_deficit(
    category: str | None,
    villages: list[dict],
    works: list[dict],
    population: int,
) -> tuple[float | None, dict]:
    """Real infrastructure deficit 0-1, or (None, {}) if nothing is recorded."""
    if not _with_data(villages):
        return None, {}

    if category == "road":
        value, evidence = _road_deficit(villages, works)
    elif category == "water":
        value, evidence = _water_deficit(villages)
    elif category == "health":
        value, evidence = _health_deficit(villages, population)
    elif category == "education":
        value, evidence = _education_deficit(villages)
    else:
        return None, {}

    evidence["villages_with_records"] = len(_with_data(villages))
    return value, evidence


# ---------------------------------------------------------------------------
# vulnerability -- real deprivation, aggregate only, no caste data
# ---------------------------------------------------------------------------


def real_vulnerability(villages: list[dict]) -> tuple[float | None, dict]:
    """
    Multi-dimensional deprivation from published, area-level records:
    electricity supply, sanitation and digital access -- all Census 2011,
    a historical structural baseline, not a claim about current conditions
    (see `evidence["period"]` below).

    Isolation (distance to the district HQ) was removed from this term
    2026-09-15: it and `feasibility_points()`'s distance signal both derived
    from the same Census Village Amenities CSVs (`dist_district_hq_km` here,
    `dist_subdistrict_hq_km`/now `dist_nearest_town_km` there) -- the same
    "distance to a government/economic centre" construct scored twice under
    two different names. Distance now belongs to feasibility only.

    Deliberately excludes the Scheduled Caste / Scheduled Tribe population
    columns that exist in the same census source. That exclusion is a standing
    decision, not an oversight (REAL_DATA_RESEARCH.md §2.3).
    """
    with_data = _with_data(villages)
    if not with_data:
        return None, {}

    evidence: dict = {"period": "census_2011_structural_baseline"}
    signals: list[float] = []

    power = [
        v["power_domestic_summer_hrs"]
        for v in with_data
        if v.get("power_domestic_summer_hrs") is not None
    ]
    if power:
        mean_power = sum(power) / len(power)
        deficit = max(0.0, min(1.0, (24.0 - mean_power) / 24.0))
        evidence["mean_power_hours_summer"] = round(mean_power, 1)
        signals.append(deficit)

    no_drainage = _share(with_data, "drainage_none", absent_value=1)
    if no_drainage is not None:
        evidence["share_without_drainage"] = round(no_drainage, 3)
        signals.append(no_drainage)

    no_internet = _share(with_data, "conn_internet_csc")
    if no_internet is not None:
        evidence["share_without_internet_csc"] = round(no_internet, 3)
        signals.append(no_internet)

    if not signals:
        return None, {}

    value = sum(signals) / len(signals)
    evidence["dimensions_used"] = len(signals)
    return max(0.0, min(1.0, value)), evidence


# ---------------------------------------------------------------------------
# equity -- reporting capacity, from BharatNet
# ---------------------------------------------------------------------------


def real_reporting_capacity_deficit(villages: list[dict]) -> tuple[float | None, dict]:
    """
    How hard it is to report from here, from BharatNet's 2022 fibre status.

    This replaces a hardcoded list of ten block names. Census mobile coverage
    was the obvious alternative and was rejected on measurement: 9 of 942
    villages lack it, so it cannot separate anywhere from anywhere.

    Measures institutional connectivity (fibre to the gram panchayat office),
    NOT household mobile signal. Any text rendered from this must say so.
    """
    with_status = [v for v in villages if v.get("conn_bharatnet_status")]
    if not with_status:
        return None, {}

    impaired = [
        v
        for v in with_status
        if (v["conn_bharatnet_status"] or "").upper() in config.BHARATNET_IMPAIRED_STATUSES
    ]
    share = len(impaired) / len(with_status)
    return share, {
        "bharatnet_villages_checked": len(with_status),
        "bharatnet_villages_impaired": len(impaired),
        "share_digitally_impaired": round(share, 3),
        "measures": "fibre_to_gram_panchayat_not_household_mobile",
    }


# ---------------------------------------------------------------------------
# strategic -- real scheme eligibility, per category
# ---------------------------------------------------------------------------


def real_scheme_eligibility(
    category: str | None, villages: list[dict], population: int
) -> tuple[bool, dict]:
    """
    Does a published government scheme already entitle this place to the thing
    it is short of? Answering yes turns "build a road here" into "this crosses
    PMGSY's threshold and is not yet covered" -- a far easier thing for an
    official to act on (research report §10, feature #12).
    """
    with_data = _with_data(villages)
    if not with_data:
        return False, {}

    if category == "road":
        unconnected = [v for v in with_data if v.get("road_all_weather") == 0]
        qualifying = [
            v
            for v in unconnected
            if (v.get("population") or 0) >= config.PMGSY_MIN_POPULATION_PLAIN
        ]
        if qualifying:
            return True, {
                "scheme": "PMGSY",
                "rule": f"population >= {config.PMGSY_MIN_POPULATION_PLAIN} and no all-weather road",
                "qualifying_villages": [v["name"] for v in qualifying][:5],
            }
        return False, {"scheme": "PMGSY", "eligible": False}

    if category == "health":
        entitled = population / config.IPHS_POPULATION_PER_SUBCENTRE
        existing = sum(v.get("health_subcentre_count") or 0 for v in with_data)
        if entitled > existing:
            return True, {
                "scheme": "IPHS / National Health Mission",
                "rule": f"1 sub-centre per {config.IPHS_POPULATION_PER_SUBCENTRE:,} people",
                "entitled": round(entitled, 2),
                "existing": existing,
            }
        return False, {"scheme": "IPHS", "eligible": False}

    if category == "education":
        breaching = [
            v
            for v in with_data
            if v.get("school_middle") == 0
            or v.get("school_primary_nearest_band") in config.CENSUS_BANDS_PROVING_RTE_BREACH
        ]
        if breaching:
            return True, {
                "scheme": "RTE Act 2009 (statutory duty)",
                "rule": f"upper primary within {config.RTE_UPPER_PRIMARY_MAX_KM} km",
                "villages_short": len(breaching),
            }
        return False, {"scheme": "RTE Act 2009", "eligible": False}

    if category == "water":
        measured = [v for v in with_data if v.get("jjm_tap_coverage_pct") is not None]
        short = [
            v for v in measured if v["jjm_tap_coverage_pct"] < config.JJM_FULL_COVERAGE_PCT
        ]
        if short:
            return True, {
                "scheme": "Jal Jeevan Mission",
                "rule": "functional household tap connection for every household (55 lpcd)",
                "villages_below_full_coverage": len(short),
                "lowest_coverage_pct": round(
                    min(v["jjm_tap_coverage_pct"] for v in short), 1
                ),
            }
        if measured:
            return False, {"scheme": "Jal Jeevan Mission", "eligible": False}
        # Not crawled here -- unknown, not "no".
        return False, {"scheme": "Jal Jeevan Mission", "eligible": None}

    return False, {}


# ---------------------------------------------------------------------------
# feasibility -- distance to a real town, plus existing road connectivity
# ---------------------------------------------------------------------------


def real_town_distance_km(villages: list[dict]) -> tuple[float | None, dict]:
    """
    Distance to the nearest actual town as the Census recorded it, in
    preference to our own straight-line calculation to an administrative HQ.

    Changed 2026-09-15 from `dist_subdistrict_hq_km` (distance to a
    government office -- bureaucratic remoteness) to `dist_nearest_town_km`
    (distance to a real town -- where labour, materials and contractors
    actually come from). This is also what stops feasibility and
    vulnerability from reading the same "distance to an administrative
    centre" construct under two different names.
    """
    values = [
        v["dist_nearest_town_km"]
        for v in villages
        if v.get("dist_nearest_town_km") is not None
    ]
    if not values:
        return None, {}
    mean_distance = sum(values) / len(values)
    return mean_distance, {
        "mean_km_to_nearest_town": round(mean_distance, 1),
        "source": "census_recorded_distance",
    }


def real_road_connectivity(villages: list[dict]) -> tuple[float | None, dict]:
    """
    Share of villages in catchment with an all-weather road, as a real
    feasibility signal: a site already reachable by road is genuinely
    cheaper to build in and supply than one that isn't.

    Low variance by design, not by bug: only 12 of 942 villages in this
    dataset are recorded without one, so this mostly moves the score for
    that minority -- see `_road_deficit`'s own note on the same field.
    """
    with_data = _with_data(villages)
    if not with_data:
        return None, {}
    no_all_weather = _share(with_data, "road_all_weather")
    if no_all_weather is None:
        return None, {}
    connected_share = round(1.0 - no_all_weather, 3)
    return connected_share, {
        "share_all_weather_road": connected_share,
        "villages_checked": len(with_data),
    }


# ---------------------------------------------------------------------------
# Naming the actual asset behind a work group
# ---------------------------------------------------------------------------

# How far from a work group a facility can be and still be a candidate for
# what the reports are about. Reports carry village centroids, so this is
# village scale, not street scale.
ASSET_CANDIDATE_RADIUS_M = 2000.0

# Ranked shortlist length. Beyond a handful, a list stops helping anyone
# decide and starts reading as noise.
ASSET_MAX_CANDIDATES = 6


def load_facility_index(db, category: str | None = None) -> list[dict]:
    """Named public assets with coordinates, for naming work groups."""
    from models import PublicFacility  # noqa: E402

    query = db.query(PublicFacility).filter(
        PublicFacility.latitude.isnot(None),
        PublicFacility.longitude.isnot(None),
    )
    if category:
        query = query.filter(PublicFacility.category == category)

    return [
        {
            "id": f.id,
            "name": f.name,
            "external_id": f.external_id,
            "source": f.source,
            "category": f.category,
            "sub_type": f.sub_type,
            "management": f.management,
            "village": f.village,
            "lat": f.latitude,
            "lon": f.longitude,
        }
        for f in query.all()
    ]


def name_work_group(
    lat: float,
    lon: float,
    category: str | None,
    facilities: list[dict],
    works_by_village: dict[str, list[dict]] | None = None,
    village: str | None = None,
) -> dict:
    """
    Work out which real, named asset a work group is about.

    Returns {label, source, external_id, candidates}. `label` is filled in
    ONLY when the answer is unambiguous -- one candidate in range. This is the
    honest line: a village with 29 schools cannot be resolved to one of them
    by measuring from its centroid, and asserting the nearest would be
    inventing a fact that an officer would then act on. In that case the
    shortlist is returned and a person chooses.

    Roads are resolved differently and better: PMGSY works carry real endpoint
    names already matched to a village, so a road complaint in a village with
    one sanctioned work names that work outright, no distance guessing.
    """
    result: dict = {
        "label": None,
        "source": None,
        "external_id": None,
        "candidates": [],
    }

    if category == "road":
        works = (works_by_village or {}).get(village or "", [])
        result["candidates"] = [
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
            for w in works[:ASSET_MAX_CANDIDATES]
        ]
        if len(works) == 1:
            result["label"] = works[0]["name"]
            result["source"] = "pmgsy"
            result["external_id"] = works[0].get("external_id")
        return result

    near = []
    for facility in facilities:
        distance = haversine_km(lat, lon, facility["lat"], facility["lon"]) * 1000.0
        if distance <= ASSET_CANDIDATE_RADIUS_M:
            near.append((distance, facility))
    near.sort(key=lambda pair: pair[0])

    result["candidates"] = [
        {
            "name": f["name"],
            "external_id": f["external_id"],
            "detail": " · ".join(p for p in (f.get("sub_type"), f.get("management")) if p),
            "distance_m": round(d),
        }
        for d, f in near[:ASSET_MAX_CANDIDATES]
    ]

    if len(near) == 1:
        distance, facility = near[0]
        result["label"] = facility["name"]
        result["source"] = facility["source"]
        result["external_id"] = facility["external_id"]

    return result


# ---------------------------------------------------------------------------
# NWDP Groundwater Telemetry (Feature corroboration for water scarcity)
# ---------------------------------------------------------------------------

DEFAULT_GROUNDWATER_MAX_DISTANCE_KM = 25.0


def load_groundwater_index(db=None) -> list[dict]:
    """
    Groundwater telemetry stations from NWDP (National Water Data Portal).

    Reads from the database if available, otherwise falls back to the
    snapshot CSV in backend/data/nwdp_groundwater_stations.csv.
    """
    stations: list[dict] = []
    if db is not None:
        try:
            from models import NwdpGroundwater

            for row in db.query(NwdpGroundwater).all():
                if row.latitude is None or row.longitude is None:
                    continue
                stations.append(
                    {
                        "station_name": row.station_name,
                        "district": row.district,
                        "tehsil": row.tehsil,
                        "lat": row.latitude,
                        "lon": row.longitude,
                        "current_level_m": row.current_level_m,
                        "previous_level_m": row.previous_level_m,
                        "trend": row.trend or "stable",
                        "recorded_at": row.recorded_at,
                    }
                )
        except Exception:
            stations = []

    if not stations:
        csv_path = (
            Path(__file__).resolve().parents[1]
            / "backend"
            / "data"
            / "nwdp_groundwater_stations.csv"
        )
        if csv_path.exists():
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        lat = float(row["latitude"])
                        lon = float(row["longitude"])
                        curr = float(row["current_level_m"])
                        prev = (
                            float(row["previous_level_m"])
                            if row.get("previous_level_m")
                            else None
                        )
                        stations.append(
                            {
                                "station_name": row.get("station_name"),
                                "district": row.get("district"),
                                "tehsil": row.get("tehsil"),
                                "lat": lat,
                                "lon": lon,
                                "current_level_m": curr,
                                "previous_level_m": prev,
                                "trend": row.get("trend") or "stable",
                                "recorded_at": row.get("recorded_at"),
                            }
                        )
                    except (ValueError, TypeError, KeyError):
                        continue

    return stations


def lookup_groundwater(
    lat: float,
    lon: float,
    stations: list[dict],
    max_distance_km: float = DEFAULT_GROUNDWATER_MAX_DISTANCE_KM,
) -> tuple[dict | None, str | None]:
    """
    Find the nearest NWDP groundwater telemetry station within max_distance_km.

    Returns (station_dict, evidence_str).
    If no station is within max_distance_km or stations list is empty,
    returns (None, None).
    """
    if not stations:
        return None, None

    nearest = None
    min_dist = float("inf")

    for s in stations:
        dist = haversine_km(lat, lon, s["lat"], s["lon"])
        if dist < min_dist:
            min_dist = dist
            nearest = s

    if nearest is None or min_dist > max_distance_km:
        return None, None

    level = nearest.get("current_level_m")
    trend = nearest.get("trend") or "stable"
    if level is None:
        return None, None

    # Level format: NWDP telemetry is meters below ground level (recorded as negative).
    # e.g., "groundwater level in this area: 1.7m bgl, trend: falling"
    evidence_text = f"groundwater level in this area: {abs(level):.1f}m bgl, trend: {trend}"
    result = dict(nearest)
    result["distance_km"] = round(min_dist, 1)
    return result, evidence_text


# ---------------------------------------------------------------------------
# PMGSY GeoSadak Road Segments (Feature 5)
# ---------------------------------------------------------------------------

# Owner decision 2026-09-15: raised from 150m. At 150m, 84 of 199 real road
# assets (42%) never matched a real GeoSadak segment at all, falling back to
# the generic "Road near <village>" name. Tested live against those real 84:
# 250m recovers 31, 400m recovers 64, 600m recovers 81, 1000m recovers all
# 84 -- but a village can have more than one road, and too wide a radius
# risks a confident WRONG road name, which is worse than the honest
# fallback. 500m is the chosen middle ground: recovers most of the real
# misses while staying close enough that grabbing the wrong road is
# unlikely. Revisit if a village with multiple close roads is found to be
# mismatched.
DEFAULT_ROAD_SEGMENT_MAX_DISTANCE_M = 500.0


def load_road_segment_index(db) -> list[dict]:
    """
    Load physical PMGSY GeoSadak road segments from the database.
    Provides line geometry, road name, category, and ownership.
    """
    import json
    from models import PMGSYRoadSegment  # noqa: E402

    segments = []
    try:
        rows = db.query(PMGSYRoadSegment).all()
        for r in rows:
            points = []
            if r.points_json:
                try:
                    points = json.loads(r.points_json)
                except Exception:
                    points = []

            segments.append(
                {
                    "id": r.id,
                    "external_id": r.external_id,
                    "district": r.district,
                    "block": r.block,
                    "drrp_road_code": r.drrp_road_code,
                    "road_name": r.road_name,
                    "road_category": r.road_category,
                    "road_owner": r.road_owner,
                    "start_lat": r.start_lat,
                    "start_lon": r.start_lon,
                    "end_lat": r.end_lat,
                    "end_lon": r.end_lon,
                    "points": points,
                    "point_count": r.point_count or len(points),
                    "min_lat": r.min_lat,
                    "max_lat": r.max_lat,
                    "min_lon": r.min_lon,
                    "max_lon": r.max_lon,
                }
            )
    except Exception:
        segments = []

    return segments


def nearest_road_segment(
    lat: float | None,
    lon: float | None,
    segments: list[dict],
    max_distance_m: float = DEFAULT_ROAD_SEGMENT_MAX_DISTANCE_M,
) -> tuple[dict | None, float | None]:
    """
    Find the nearest PMGSY GeoSadak road segment to (lat, lon) within max_distance_m.

    Uses bounding box pruning, followed by exact point-to-polyline distance
    using equirectangular projection (accurate to centimeters within local bounds).

    Returns:
        (nearest_segment_dict, distance_meters) if within max_distance_m,
        else (None, None).
    """
    if lat is None or lon is None or not segments:
        return None, None

    if not math.isfinite(lat) or not math.isfinite(lon):
        return None, None

    max_dist_km = max_distance_m / 1000.0
    lat_buf = (max_dist_km / 111.0) * 1.5
    cos_lat = max(0.1, math.cos(math.radians(lat)))
    lon_buf = (max_dist_km / (111.0 * cos_lat)) * 1.5

    m_per_deg_lat = 111139.0
    m_per_deg_lon = 111139.0 * cos_lat

    nearest = None
    min_dist_m = float("inf")

    for seg in segments:
        s_min_lat = seg.get("min_lat")
        s_max_lat = seg.get("max_lat")
        s_min_lon = seg.get("min_lon")
        s_max_lon = seg.get("max_lon")

        # Fast bounding box rejection if bbox is present
        if s_min_lat is not None and s_max_lat is not None:
            if lat < s_min_lat - lat_buf or lat > s_max_lat + lat_buf:
                continue
        if s_min_lon is not None and s_max_lon is not None:
            if lon < s_min_lon - lon_buf or lon > s_max_lon + lon_buf:
                continue

        points = seg.get("points")
        if not points:
            p_list = []
            if seg.get("start_lat") is not None and seg.get("start_lon") is not None:
                p_list.append([seg["start_lat"], seg["start_lon"]])
            if seg.get("end_lat") is not None and seg.get("end_lon") is not None:
                p_list.append([seg["end_lat"], seg["end_lon"]])
            points = p_list

        if not points:
            continue

        # Project point-to-line segments
        seg_min_d = float("inf")
        if len(points) == 1:
            plat, plon = points[0][0], points[0][1]
            seg_min_d = haversine_km(lat, lon, plat, plon) * 1000.0
        else:
            for j in range(len(points) - 1):
                p1_lat, p1_lon = points[j][0], points[j][1]
                p2_lat, p2_lon = points[j + 1][0], points[j + 1][1]

                x1 = (p1_lon - lon) * m_per_deg_lon
                y1 = (p1_lat - lat) * m_per_deg_lat
                x2 = (p2_lon - lon) * m_per_deg_lon
                y2 = (p2_lat - lat) * m_per_deg_lat

                dx = x2 - x1
                dy = y2 - y1
                seg_len_sq = dx * dx + dy * dy

                if seg_len_sq == 0:
                    d = math.hypot(x1, y1)
                else:
                    t = max(0.0, min(1.0, -(x1 * dx + y1 * dy) / seg_len_sq))
                    proj_x = x1 + t * dx
                    proj_y = y1 + t * dy
                    d = math.hypot(proj_x, proj_y)

                if d < seg_min_d:
                    seg_min_d = d

        if seg_min_d < min_dist_m:
            min_dist_m = seg_min_d
            nearest = seg

    if nearest is None or min_dist_m > max_distance_m:
        return None, None

    return nearest, round(min_dist_m, 1)


# ---------------------------------------------------------------------------
# UDISE+ School Condition & Staffing (Workstream A1)
# ---------------------------------------------------------------------------


def load_school_condition_index(db) -> dict[int, dict]:
    """
    facility_id -> its latest SchoolCondition row, as a plain dict.
    Schools with fetch_failed=True or no row at all are simply absent —
    callers must treat "not in this dict" as "no data", never as zero.
    """
    if db is None:
        return {}
    from models import SchoolCondition

    result = {}
    try:
        rows = (
            db.query(SchoolCondition)
            .filter(SchoolCondition.fetch_failed.is_(False))
            .order_by(SchoolCondition.id.asc())
            .all()
        )
        for r in rows:
            if r.facility_id is None:
                continue
            result[r.facility_id] = {
                "id": r.id,
                "facility_id": r.facility_id,
                "udise_code": r.udise_code,
                "year_desc": r.year_desc,
                "teachers_regular": r.teachers_regular,
                "teachers_contract": r.teachers_contract,
                "teachers_part_time": r.teachers_part_time,
                "classrooms_total": r.classrooms_total,
                "classrooms_good": r.classrooms_good,
                "classrooms_minor_repair": r.classrooms_minor_repair,
                "classrooms_major_repair": r.classrooms_major_repair,
                "toilet_boys_functional": r.toilet_boys_functional,
                "toilet_girls_functional": r.toilet_girls_functional,
                "drinking_water": r.drinking_water,
                "electricity": r.electricity,
                "boundary_wall_status": r.boundary_wall_status,
                "total_grant": r.total_grant,
                "total_expenditure": r.total_expenditure,
                "raw_json": r.raw_json,
                "fetched_at": r.fetched_at,
            }
    except Exception:
        result = {}
    return result


def school_condition_evidence(facility_id: int, index: dict[int, dict]) -> tuple[dict | None, str | None]:
    """
    Returns (raw_row_dict, a short human evidence sentence) or (None, None)
    if this school has no data. Example sentence:
    "5 teachers (2024-25); 4 of 5 classrooms good, 1 needs minor repair;
    grant ₹25,000, spent ₹25,000."
    Do not compute a 0-1 deficit score here — that belongs to Workstream B
    (B5), which will call this function and turn it into a score.
    """
    if not index or facility_id not in index:
        return None, None

    row = index[facility_id]
    if not row:
        return None, None

    parts = []

    # 1. Teachers
    t_reg = row.get("teachers_regular")
    t_cont = row.get("teachers_contract")
    t_part = row.get("teachers_part_time")
    if t_reg is not None or t_cont is not None or t_part is not None:
        t_total = (t_reg or 0) + (t_cont or 0) + (t_part or 0)
        yr_str = f" ({row['year_desc']})" if row.get("year_desc") else ""
        t_label = "teacher" if t_total == 1 else "teachers"
        parts.append(f"{t_total} {t_label}{yr_str}")

    # 2. Classrooms
    cls_tot = row.get("classrooms_total")
    cls_gd = row.get("classrooms_good")
    cls_min = row.get("classrooms_minor_repair")
    cls_maj = row.get("classrooms_major_repair")

    if cls_tot is not None:
        cls_parts = []
        if cls_gd is not None:
            cls_parts.append(f"{cls_gd} of {cls_tot} classrooms good")
        if cls_min is not None and cls_min > 0:
            verb = "needs" if cls_min == 1 else "need"
            cls_parts.append(f"{cls_min} {verb} minor repair")
        if cls_maj is not None and cls_maj > 0:
            verb = "needs" if cls_maj == 1 else "need"
            cls_parts.append(f"{cls_maj} {verb} major repair")
        if cls_parts:
            parts.append(", ".join(cls_parts))
        elif cls_tot == 0:
            parts.append("0 classrooms")
    elif any(c is not None for c in (cls_gd, cls_min, cls_maj)):
        cls_parts = []
        if cls_gd is not None:
            cls_parts.append(f"{cls_gd} classrooms good")
        if cls_min is not None and cls_min > 0:
            cls_parts.append(f"{cls_min} need minor repair")
        if cls_maj is not None and cls_maj > 0:
            cls_parts.append(f"{cls_maj} need major repair")
        if cls_parts:
            parts.append(", ".join(cls_parts))

    # 3. Grant and expenditure
    grant = row.get("total_grant")
    spent = row.get("total_expenditure")
    if grant is not None or spent is not None:
        money_parts = []
        if grant is not None:
            money_parts.append(f"grant ₹{grant:,.0f}")
        if spent is not None:
            money_parts.append(f"spent ₹{spent:,.0f}")
        parts.append(", ".join(money_parts))

    if not parts:
        sentence = f"UDISE+ data available ({row.get('year_desc') or '2024-25'})."
    else:
        sentence = "; ".join(parts) + "."

    return row, sentence


def school_condition_deficit(
    facility_id: int, index: dict[int, dict]
) -> tuple[float | None, dict]:
    """
    Real per-school infrastructure deficit from UDISE+ 2024-25 condition data
    -- classroom repair need, electricity, drinking water, functional toilets.

    Returns (None, {}) when this specific school has no UDISE+ record, so the
    caller falls back to the village-wide Census signal rather than
    asserting a school we simply haven't fetched has zero deficit.

    Independent signals, worst one wins -- same pattern as the road/water/
    health deficits in this file. A school can be short on one thing and
    fine on the others; a single average would hide that.
    """
    if not index or facility_id not in index:
        return None, {}
    row = index[facility_id]
    if not row:
        return None, {}

    evidence: dict = {}
    signals: list[float] = []

    total = row.get("classrooms_total") or 0
    major = row.get("classrooms_major_repair") or 0
    if total > 0:
        share = major / total
        evidence["share_classrooms_major_repair"] = round(share, 3)
        signals.append(share)

    if row.get("electricity") is False:
        evidence["no_electricity"] = True
        signals.append(1.0)

    if row.get("drinking_water") is False:
        evidence["no_drinking_water"] = True
        signals.append(1.0)

    toilet_b = row.get("toilet_boys_functional")
    toilet_g = row.get("toilet_girls_functional")
    if toilet_b == 0 or toilet_g == 0:
        evidence["no_functional_toilet"] = True
        signals.append(1.0)

    if not signals:
        return 0.0, evidence

    evidence["year"] = row.get("year_desc")
    evidence["source"] = "udise_2024_25_this_school"
    return max(signals), evidence



# ---------------------------------------------------------------------------
# Task 1: JJM Water Testing Data
# ---------------------------------------------------------------------------

def load_water_testing_index(db) -> dict[int, dict]:
    if db is None:
        return {}
    try:
        from models import WaterTesting
        rows = db.query(WaterTesting).filter(WaterTesting.gazetteer_id.isnot(None)).all()
        return {r.gazetteer_id: {
            "samples_tested": r.samples_tested,
            "villages_not_tested": r.villages_not_tested,
            "ph": r.ph,
            "frc": r.frc,
            "turbidity": r.turbidity,
            "tds": r.tds,
            "hardness": r.hardness
        } for r in rows}
    except Exception:
        return {}

def water_testing_evidence(gazetteer_id: int, index: dict[int, dict]) -> tuple[dict | None, str | None]:
    if not index or gazetteer_id not in index:
        return None, None
    row = index[gazetteer_id]
    samples = row.get("samples_tested", 0)
    untested = row.get("villages_not_tested", 0)
    sentence = f"{samples} samples tested this year, {untested} villages untested nearby"
    return row, sentence


def water_testing_catchment_evidence(
    villages: list[dict], index: dict[int, dict]
) -> tuple[dict | None, str | None]:
    """
    Aggregate current-year JJM testing coverage across a water cluster's
    whole catchment, not just one village. Many JJM rows are at a finer
    habitation grain than the gazetteer (hamlets under a village), so most
    catchments will only partially match -- report exactly how many of the
    catchment's gazetteer villages matched, never invent coverage for the
    rest. Returns (None, None) if nothing in the catchment matched this
    year's JJM data at all -- that means "not found in this year's JJM
    testing round," never "these villages need no testing."
    """
    if not index:
        return None, None
    matched = [
        index[v["gazetteer_id"]] for v in villages
        if v.get("gazetteer_id") in index
    ]
    if not matched:
        return None, None
    total_samples = sum(m.get("samples_tested") or 0 for m in matched)
    evidence = {
        "villages_tested_this_year": len(matched),
        "villages_in_catchment": len(villages),
        "samples_tested_total": total_samples,
    }
    sentence = (
        f"{len(matched)} of {len(villages)} catchment villages tested this "
        f"year (JJM WQMIS, current cycle): {total_samples} samples total"
    )
    return evidence, sentence


def load_jjm_scheme_index(db) -> dict[int, list[dict]]:
    """
    Every real JJM water-supply scheme (identity, cost, expenditure,
    status), grouped by gazetteer_id -- a village can have more than one.
    Loaded once per recompute() call, same pattern as every other index
    here. See `backend/load_jjm_village_schemes.py` for the source report.
    """
    if db is None:
        return {}
    from models import JjmVillageScheme

    index: dict[int, list[dict]] = {}
    for row in db.query(JjmVillageScheme).all():
        index.setdefault(row.gazetteer_id, []).append(
            {
                "scheme_id": row.scheme_id,
                "scheme_name": row.scheme_name,
                "scheme_type": row.scheme_type,
                "scheme_category": row.scheme_category,
                "work_order_date": row.work_order_date,
                "estimated_cost_lakh": row.estimated_cost_lakh,
                "reported_expenditure_lakh": row.reported_expenditure_lakh,
                "status": row.status,
            }
        )
    return index


# Statuses that mean "sanctioned, not yet delivered" -- same reasoning as
# investment.py's UNDELIVERED_STATUSES for PMGSY roads, JJM's own wording.
JJM_UNDELIVERED_STATUSES = {"ongoing", "in progress", "not started"}


def jjm_scheme_catchment_evidence(
    villages: list[dict], index: dict[int, list[dict]]
) -> tuple[dict | None, str | None]:
    """
    Real scheme identity, cost and status for a water cluster's catchment --
    the Village -> Scheme -> Cost -> Status chain, not just a tap-coverage
    percentage. Evidence only: this does not change infra_deficit's number,
    which already has a better real signal (current JJM tap coverage, see
    `_water_deficit`) -- it answers a different question, "what scheme is
    this, and is the government's own money for it still unspent."

    Returns (None, None) when no village in this catchment has a scheme
    record -- most won't, since only 656 of 1,042 gazetteer villages have a
    matched LGD code to look one up by.
    """
    if not index:
        return None, None
    matched_schemes = [
        s
        for v in villages
        for s in index.get(v.get("gazetteer_id"), [])
    ]
    if not matched_schemes:
        return None, None

    undelivered = [
        s for s in matched_schemes
        if (s.get("status") or "").strip().lower() in JJM_UNDELIVERED_STATUSES
    ]
    unspent_lakh = round(
        sum(
            max(0.0, (s.get("estimated_cost_lakh") or 0.0) - (s.get("reported_expenditure_lakh") or 0.0))
            for s in undelivered
        ),
        2,
    )
    evidence = {
        "schemes_found": len(matched_schemes),
        "undelivered_schemes": len(undelivered),
        "unspent_estimate_lakh": unspent_lakh,
        "schemes": matched_schemes[:5],
    }
    if undelivered:
        sentence = (
            f"{len(undelivered)} of {len(matched_schemes)} real JJM scheme(s) in this "
            f"catchment still ongoing, ₹{unspent_lakh} lakh of the estimated cost unspent"
        )
    else:
        sentence = f"{len(matched_schemes)} real JJM scheme(s) found in this catchment, all completed"
    return evidence, sentence


# ---------------------------------------------------------------------------
# Task 2 & 3: Hazard Near (CWC River Levels & SACHET Alerts)
# ---------------------------------------------------------------------------

def _as_utc(dt):
    """
    SQLite drops tzinfo on round-trip, so a DateTime column always comes
    back naive even though every writer in this project stores UTC
    (datetime.now(timezone.utc)). Stamp it back on here, once, rather than
    at every comparison site -- comparing a naive value against an aware
    `now` raises TypeError instead of silently doing the wrong thing, which
    is what surfaced this in the first place.
    """
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


def load_river_readings_index(db) -> list[dict]:
    """
    Every stored CWC river reading, as plain dicts. Loaded once per
    recompute() call, same pattern as load_groundwater_index -- hazard_near
    below must never query the database itself per cluster.
    """
    if db is None:
        return []
    try:
        from models import RiverReading
        return [
            {
                "name": r.name, "lat": r.lat, "lon": r.lon,
                "value": r.value, "datatype_code": r.datatype_code,
                "observed_at": _as_utc(r.observed_at),
            }
            for r in db.query(RiverReading).all()
        ]
    except Exception:
        return []


def load_hazard_alerts_index(db) -> list[dict]:
    """Every stored SACHET alert, as plain dicts. Loaded once per recompute()."""
    if db is None:
        return []
    try:
        from models import HazardAlert
        return [
            {"districts": a.districts, "effective": _as_utc(a.effective),
             "expires": _as_utc(a.expires), "event": a.event}
            for a in db.query(HazardAlert).all()
        ]
    except Exception:
        return []


def hazard_near(
    lat: float | None,
    lon: float | None,
    river_readings: list[dict],
    hazard_alerts: list[dict],
    hours: int = 72,
) -> tuple[bool, dict]:
    """
    True if an unusual CWC river reading or an active SACHET alert is near
    (lat, lon) within the last `hours`.

    Evidence-only for now: it corroborates a possible emergency, it does not
    move the priority score -- the urgency-term redesign that would actually
    score bridge/building emergencies (FEATURE_ROADMAP.md #7) has not been
    built yet, so this stays visible in the evidence panel until it has.

    `river_readings` / `hazard_alerts` are the plain-dict lists from
    `load_river_readings_index` / `load_hazard_alerts_index` -- pre-loaded
    once per recompute() call, not queried live per cluster.
    """
    if lat is None or lon is None:
        return False, {}

    evidence: dict = {}
    is_hazard = False
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    near_readings = [
        r for r in (river_readings or [])
        if r.get("observed_at") is not None and r["observed_at"] >= cutoff
        and r.get("lat") is not None and r.get("lon") is not None
        and haversine_km(lat, lon, r["lat"], r["lon"]) <= 25.0
    ]
    if near_readings:
        is_hazard = True
        evidence["cwc_river_warnings"] = len(near_readings)
        nearest = near_readings[0]
        evidence["cwc_nearest"] = f"{nearest['name']} ({nearest['value']} {nearest['datatype_code']})"

    active_alerts = []
    for a in (hazard_alerts or []):
        districts = a.get("districts") or ""
        effective, expires = a.get("effective"), a.get("expires")
        if not (effective and expires and effective <= now <= expires):
            continue
        in_bounds = (
            ("Kolhapur" in districts and 15.7 <= lat <= 17.1 and 73.7 <= lon <= 74.7)
            or ("Nashik" in districts and 19.6 <= lat <= 20.9 and 73.3 <= lon <= 75.0)
        )
        if in_bounds:
            active_alerts.append(a)

    if active_alerts:
        is_hazard = True
        evidence["sachet_alerts"] = len(active_alerts)
        events = [a["event"] for a in active_alerts if a.get("event")]
        if events:
            evidence["sachet_events"] = ", ".join(events)

    return is_hazard, evidence


# ---------------------------------------------------------------------------
# Historical flood exposure (NDEM, real satellite-derived 2013/2021 extents)
# ---------------------------------------------------------------------------


def load_flood_event_index(db) -> list[dict]:
    """
    Every real NDEM flood-inundation event (2013/2021), as plain dicts --
    loaded once per recompute() call, same pattern as every other real_*
    index in this file. See backend/load_flood_inundation.py for the source.
    """
    if db is None:
        return []
    try:
        from models import FloodEvent

        return [
            {
                "year": r.year,
                "xmin": r.bbox_xmin,
                "ymin": r.bbox_ymin,
                "xmax": r.bbox_xmax,
                "ymax": r.bbox_ymax,
            }
            for r in db.query(FloodEvent).all()
        ]
    except Exception:
        return []


def _distance_to_bbox_km(lat: float, lon: float, ev: dict) -> float:
    """
    Real distance from (lat, lon) to the nearest point on a flood event's
    bounding box -- 0.0 if the point falls inside it. A lower bound on the
    true distance to the actual flood polygon, not an approximation that
    could understate exposure (see FloodEvent's own docstring).
    """
    clamped_lat = min(max(lat, ev["ymin"]), ev["ymax"])
    clamped_lon = min(max(lon, ev["xmin"]), ev["xmax"])
    return haversine_km(lat, lon, clamped_lat, clamped_lon)


def flood_exposure_evidence(
    lat: float | None, lon: float | None, flood_events: list[dict]
) -> tuple[dict | None, str | None]:
    """
    Real historical flood-inundation exposure near (lat, lon), from NDEM's
    satellite-derived 2013/2021 flood extent record (FEATURE_ROADMAP.md
    #17). Evidence only -- like groundwater, water testing, JJM schemes and
    MGNREGA before it, this corroborates a real background risk without
    moving infra_deficit, vulnerability, or any other score term; wiring a
    historical-exposure signal into the score itself is a separate, later
    decision.

    Applies to every category, not just road/water -- a flood affects
    whatever is standing in it, regardless of what kind of asset it is.

    Returns (None, None) when nothing is within FLOOD_EXPOSURE_RADIUS_KM --
    that means "no recorded 2013/2021 flood event this close," never "this
    place cannot flood."
    """
    if lat is None or lon is None or not flood_events:
        return None, None

    # Cheap plain-degree pre-filter before the real (trig-based) haversine
    # call below. flood_events is ~14,000 rows and this function runs once
    # per cluster/asset (roughly 1,000 times a recompute) -- doing the trig
    # call unconditionally measurably slowed recompute() when first wired in
    # (confirmed live: a recompute that normally finishes in well under a
    # minute took over two). ~111 km/degree of latitude, with a 1.5x margin
    # so nothing genuinely within FLOOD_EXPOSURE_RADIUS_KM is ever dropped --
    # this filter can only keep more candidates than the exact check needs,
    # never fewer.
    margin_deg = (config.FLOOD_EXPOSURE_RADIUS_KM / 111.0) * 1.5
    candidates = [
        ev for ev in flood_events
        if ev["ymin"] - margin_deg <= lat <= ev["ymax"] + margin_deg
        and ev["xmin"] - margin_deg <= lon <= ev["xmax"] + margin_deg
    ]
    if not candidates:
        return None, None

    near = [
        (_distance_to_bbox_km(lat, lon, ev), ev["year"])
        for ev in candidates
    ]
    near = [(d, y) for d, y in near if d <= config.FLOOD_EXPOSURE_RADIUS_KM]
    if not near:
        return None, None

    years = sorted({y for _, y in near})
    nearest_km = round(min(d for d, _ in near), 2)
    evidence = {
        "events_found": len(near),
        "years": years,
        "nearest_km": nearest_km,
        "source": "NDEM satellite-derived flood inundation (2013, 2021)",
    }
    sentence = (
        f"{len(near)} recorded flood-inundation event"
        f"{'s' if len(near) != 1 else ''} ({', '.join(years)}) within "
        f"{config.FLOOD_EXPOSURE_RADIUS_KM:.0f} km, nearest {nearest_km} km away"
    )
    return evidence, sentence


# ---------------------------------------------------------------------------
# GPDP District Summary (index.do)
# ---------------------------------------------------------------------------


def load_gpdp_district_summary_index(db) -> dict[str, dict]:
    """
    district name (lowercased) -> its most recent plan_year's row, as a
    plain dict. A district with fetch_failed=True or no row at all is
    simply absent -- callers must treat "not in this dict" as "no data,"
    never as zero investment.
    """
    if db is None:
        return {}
    try:
        from models import GpdpDistrictSummary

        rows = (
            db.query(GpdpDistrictSummary)
            .filter(GpdpDistrictSummary.fetch_failed.is_(False))
            .order_by(GpdpDistrictSummary.plan_year.asc())
            .all()
        )
        result = {}
        for r in rows:
            if not r.district:
                continue
            key = r.district.strip().lower()
            # Because we ordered by plan_year.asc(), the latest year ("2026-27" > "2025-26") overwrites earlier ones
            result[key] = {
                "id": r.id,
                "district": r.district,
                "district_code": r.district_code,
                "state_code": r.state_code,
                "plan_year": r.plan_year,
                "total_panchayats": r.total_panchayats,
                "panchayats_with_plan": r.panchayats_with_plan,
                "approved_activities": r.approved_activities,
                "gram_sabhas_conducted": r.gram_sabhas_conducted,
                "estimated_outlay_lakh": r.estimated_outlay_lakh,
                "popular_activities_json": r.popular_activities_json,
                "underpicked_activities_json": r.underpicked_activities_json,
                "recent_activities_json": r.recent_activities_json,
                "data_as_of": r.data_as_of,
                "fetched_at": r.fetched_at,
            }
        return result
    except Exception:
        return {}


def gpdp_district_evidence(district: str, index: dict[str, dict]) -> tuple[dict | None, str | None]:
    """
    Returns (raw_row_dict, a short human evidence sentence) or (None, None)
    if this district has no data. Example sentence:
    "Kolhapur district, FY 2026-27: 1,025 panchayats, all with a
    registered plan; 64,418 approved activities; ₹34,082.32 lakh
    estimated outlay (data as of 14 Sep 2026 21:09)."
    This is district-level context, not a per-village or per-asset score
    input -- do not attribute it to a specific cluster or asset here or
    anywhere downstream.
    """
    if not district or not index:
        return None, None

    key = district.strip().lower()
    if key not in index:
        return None, None

    row = index[key]
    if not row:
        return None, None

    dist_name = row.get("district") or district.title()
    fy = f"FY {row.get('plan_year')}" if row.get("plan_year") else ""
    header_part = f"{dist_name} district, {fy}:" if fy else f"{dist_name} district:"

    parts = []
    tot_p = row.get("total_panchayats")
    with_p = row.get("panchayats_with_plan")
    if tot_p is not None:
        if with_p is not None and with_p >= tot_p:
            parts.append(f"{tot_p:,} panchayats, all with a registered plan")
        elif with_p is not None:
            parts.append(f"{with_p:,} of {tot_p:,} panchayats with a registered plan")
        else:
            parts.append(f"{tot_p:,} panchayats")

    acts = row.get("approved_activities")
    if acts is not None:
        parts.append(f"{acts:,} approved activities")

    outlay = row.get("estimated_outlay_lakh")
    if outlay is not None:
        parts.append(f"₹{outlay:,.2f} lakh estimated outlay")

    freshness = f" (data as of {row['data_as_of']})" if row.get("data_as_of") else ""

    if not parts:
        return row, f"{header_part} GPDP data available{freshness}."

    sentence = f"{header_part} {'; '.join(parts)}{freshness}."
    return row, sentence


# ---------------------------------------------------------------------------
# PRIASoft Village Panchayat Finance (recExpVpNew.do)
# ---------------------------------------------------------------------------


def load_village_finance_index(db) -> dict[int, dict]:
    """
    gazetteer_id -> its most recent fin_year's row, as a plain dict.
    Rows without gazetteer_id are excluded.
    """
    if db is None:
        return {}
    try:
        from models import VillagePanchayatFinance

        rows = (
            db.query(VillagePanchayatFinance)
            .filter(VillagePanchayatFinance.gazetteer_id.isnot(None))
            .order_by(VillagePanchayatFinance.fin_year.asc())
            .all()
        )
        result = {}
        for r in rows:
            # Ordered by fin_year.asc(), so newer years (e.g. 2025-2026 > 2024-2025) overwrite earlier ones
            result[r.gazetteer_id] = {
                "id": r.id,
                "gazetteer_id": r.gazetteer_id,
                "village_name": r.village_name,
                "district": r.district,
                "fin_year": r.fin_year,
                "scheme_code": r.scheme_code,
                "untied_opening_balance": r.untied_opening_balance,
                "untied_receipts": r.untied_receipts,
                "untied_payments": r.untied_payments,
                "untied_closing_balance": r.untied_closing_balance,
                "tied_opening_balance": r.tied_opening_balance,
                "tied_receipts": r.tied_receipts,
                "tied_payments": r.tied_payments,
                "tied_closing_balance": r.tied_closing_balance,
                "fetched_at": r.fetched_at,
            }
        return result
    except Exception:
        return {}


def village_finance_evidence(gazetteer_id: int, index: dict[int, dict]) -> tuple[dict | None, str | None]:
    """
    Returns (raw_row_dict, a short human evidence sentence) or (None, None).
    Example: "FY 2025-26: untied grant ₹64,059 received, ₹0 spent (0% utilised); tied grant ₹112,086 received, ₹0 spent."
    """
    if not gazetteer_id or not index:
        return None, None

    row = index.get(gazetteer_id)
    if not row:
        return None, None

    fy = f"FY {row.get('fin_year')}:" if row.get("fin_year") else "PRIASoft finance:"
    parts = []

    # Untied component
    u_rec = row.get("untied_receipts")
    u_pay = row.get("untied_payments")
    if u_rec is not None or u_pay is not None:
        rec_str = f"₹{u_rec:,.0f}" if u_rec is not None else "₹0"
        pay_str = f"₹{u_pay:,.0f}" if u_pay is not None else "₹0"
        pct_str = ""
        if u_rec and u_rec > 0 and u_pay is not None:
            pct = min(100.0, (u_pay / u_rec) * 100.0)
            pct_str = f" ({pct:.0f}% utilised)"
        parts.append(f"untied grant {rec_str} received, {pay_str} spent{pct_str}")

    # Tied component
    t_rec = row.get("tied_receipts")
    t_pay = row.get("tied_payments")
    if t_rec is not None or t_pay is not None:
        rec_str = f"₹{t_rec:,.0f}" if t_rec is not None else "₹0"
        pay_str = f"₹{t_pay:,.0f}" if t_pay is not None else "₹0"
        parts.append(f"tied grant {rec_str} received, {pay_str} spent")

    if not parts:
        return row, f"{fy} PRIASoft accounting records available."

    sentence = f"{fy} {'; '.join(parts)}."
    return row, sentence


# ---------------------------------------------------------------------------
# MGNREGA Village Panchayat Expenditure (gp_cummulative_report1.aspx)
# ---------------------------------------------------------------------------


def load_village_mgnrega_index(db) -> dict[int, dict]:
    """
    gazetteer_id -> its most recent fin_year's row, as a plain dict.
    Rows without gazetteer_id are excluded.
    """
    if db is None:
        return {}
    try:
        from models import VillageMgnregaExpenditure

        rows = (
            db.query(VillageMgnregaExpenditure)
            .filter(VillageMgnregaExpenditure.gazetteer_id.isnot(None))
            .order_by(VillageMgnregaExpenditure.fin_year.asc())
            .all()
        )
        result = {}
        for r in rows:
            result[r.gazetteer_id] = {
                "id": r.id,
                "gazetteer_id": r.gazetteer_id,
                "village_name": r.village_name,
                "district": r.district,
                "block": r.block,
                "fin_year": r.fin_year,
                "total_expenditure_lakh": r.total_expenditure_lakh,
                "wages_lakh": r.wages_lakh,
                "material_lakh": r.material_lakh,
                "wages_percent": r.wages_percent,
                "material_percent": r.material_percent,
                "fetched_at": r.fetched_at,
            }
        return result
    except Exception:
        return {}


def village_mgnrega_evidence(gazetteer_id: int, index: dict[int, dict]) -> tuple[dict | None, str | None]:
    """
    Returns (raw_row_dict, a short human evidence sentence) or (None, None).
    Example: "FY 2026-2027: MGNREGA expenditure ₹5.37 lakh (wages ₹1.24 lakh [23%], material ₹4.12 lakh [77%])."
    """
    if not gazetteer_id or not index:
        return None, None

    row = index.get(gazetteer_id)
    if not row:
        return None, None

    fy = f"FY {row.get('fin_year')}:" if row.get("fin_year") else "MGNREGA expenditure:"
    tot = row.get("total_expenditure_lakh")
    wages = row.get("wages_lakh")
    mat = row.get("material_lakh")
    w_pct = row.get("wages_percent")
    m_pct = row.get("material_percent")

    if tot is None and wages is None and mat is None:
        return row, f"{fy} MGNREGA accounting records available."

    parts = []
    if tot is not None:
        parts.append(f"MGNREGA expenditure ₹{tot:,.2f} lakh")

    sub_parts = []
    if wages is not None:
        w_str = f"wages ₹{wages:,.2f} lakh"
        if w_pct is not None:
            w_str += f" [{w_pct:.0f}%]"
        sub_parts.append(w_str)

    if mat is not None:
        m_str = f"material ₹{mat:,.2f} lakh"
        if m_pct is not None:
            m_str += f" [{m_pct:.0f}%]"
        sub_parts.append(m_str)

    if sub_parts:
        detail_str = f" ({', '.join(sub_parts)})"
    else:
        detail_str = ""

    sentence = f"{fy} {'; '.join(parts)}{detail_str}."
    return row, sentence


def mgnrega_catchment_evidence(
    villages: list[dict], index: dict[int, dict]
) -> tuple[dict | None, str | None]:
    """
    Aggregate real MGNREGA (rural employment guarantee) expenditure across a
    cluster's whole catchment, not just one village -- same pattern as
    jjm_scheme_catchment_evidence and water_testing_catchment_evidence.

    Evidence only, same as every other corroboration source wired in
    alongside this one (groundwater, water testing, JJM schemes, hazard
    alerts): it does not move infra_deficit or any other score term.
    Wiring a real government-works spending signal into the score itself is
    a separate, later decision (see BUILD_PROMPT_MGNREGA_LOADER.md's own
    ground rule 4), same as it was for every other real-data source before
    its evidence shape had been checked against live clusters first.

    Returns (None, None) when no village in the catchment has a matched
    MGNREGA record -- most won't yet, since only 119 real rows are loaded
    so far.
    """
    if not index:
        return None, None
    matched = [index[v["gazetteer_id"]] for v in villages if v.get("gazetteer_id") in index]
    if not matched:
        return None, None

    total = sum(m.get("total_expenditure_lakh") or 0.0 for m in matched)
    wages = sum(m.get("wages_lakh") or 0.0 for m in matched)
    material = sum(m.get("material_lakh") or 0.0 for m in matched)
    fin_years = sorted({m["fin_year"] for m in matched if m.get("fin_year")})

    evidence = {
        "villages_matched": len(matched),
        "villages_in_catchment": len(villages),
        "total_expenditure_lakh": round(total, 2),
        "wages_lakh": round(wages, 2),
        "material_lakh": round(material, 2),
        "fin_years": fin_years,
    }
    sentence = (
        f"{len(matched)} of {len(villages)} catchment villages have a real MGNREGA "
        f"record ({fin_years[-1] if fin_years else 'unknown year'}): "
        f"Rs {total:,.2f} lakh total expenditure "
        f"(Rs {wages:,.2f} lakh wages, Rs {material:,.2f} lakh material)"
    )
    return evidence, sentence


