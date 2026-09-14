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

import math

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
    isolation, electricity supply, sanitation and digital access.

    Deliberately excludes the Scheduled Caste / Scheduled Tribe population
    columns that exist in the same census source. That exclusion is a standing
    decision, not an oversight (REAL_DATA_RESEARCH.md §2.3).
    """
    with_data = _with_data(villages)
    if not with_data:
        return None, {}

    evidence: dict = {}
    signals: list[float] = []

    distances = [
        v["dist_district_hq_km"] for v in with_data if v.get("dist_district_hq_km") is not None
    ]
    if distances:
        mean_distance = sum(distances) / len(distances)
        # 60km from the district headquarters is treated as full isolation;
        # the observed median across these districts is ~50km.
        isolation = min(1.0, mean_distance / 60.0)
        evidence["mean_km_to_district_hq"] = round(mean_distance, 1)
        signals.append(isolation)

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
# feasibility -- the government's own recorded distance
# ---------------------------------------------------------------------------


def real_hq_distance_km(villages: list[dict]) -> tuple[float | None, dict]:
    """
    Distance to the sub-district headquarters as the Census recorded it, in
    preference to our own straight-line calculation between coordinates.
    """
    values = [
        v["dist_subdistrict_hq_km"]
        for v in villages
        if v.get("dist_subdistrict_hq_km") is not None
    ]
    if not values:
        return None, {}
    mean_distance = sum(values) / len(values)
    return mean_distance, {
        "mean_km_to_subdistrict_hq": round(mean_distance, 1),
        "source": "census_recorded_distance",
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
