"""
P3 test suite. Real assertions -- every check here can fail.

Run from the repo root:  python -m intelligence.test_intelligence

The most important test in this file is test_breakdown_sums_to_score: the
entire explainability claim rests on the nine breakdown terms adding up to
the number shown on the dashboard. If someone adds a tenth term to the
formula and forgets to surface it, that test goes red immediately.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from intelligence import config, realdata
from intelligence.clustering import build_distance_matrix, haversine_km
from intelligence.population import population_in_catchment
from intelligence.scoring import (
    confidence_gate,
    demand_term,
    equity_points,
    feasibility_points,
    infra_deficit_term,
    population_term,
    score_cluster,
    vulnerability_term,
)
from intelligence.recompute import (
    _build_assets,
    _build_villages,
    _find_school_candidates,
    _group_by_radius,
)
from intelligence.whatif import budget_to_points

_passed = 0
_failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed.append(name)
        print(f"  [FAIL] {name}  {detail}")


def _baseline_kwargs(**overrides):
    kwargs = dict(
        unique_reporters=10,
        population_affected=20_000,
        settlement_count=4,
        severities=["medium", "medium"],
        average_confidence=0.9,
        issue_category="road",
        block="Kagal",
        distance_to_hq_km=5.0,
    )
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# Gap Score terms
# ---------------------------------------------------------------------------

def test_demand_is_capped() -> None:
    """Volume must not dominate -- research report SS16.2."""
    at_saturation = demand_term(config.DEMAND_SATURATION_REPORTERS)
    way_over = demand_term(config.DEMAND_SATURATION_REPORTERS * 100)
    check("demand saturates at the cap", math.isclose(at_saturation, 1.0))
    check(
        "demand cannot exceed the cap however loud a district is",
        math.isclose(way_over, at_saturation),
        f"{way_over} != {at_saturation}",
    )
    check("zero reporters gives zero demand", demand_term(0) == 0.0)


def test_population_is_log_scaled() -> None:
    """A city must not automatically bury a hamlet."""
    small, medium, large = (
        population_term(1_000),
        population_term(10_000),
        population_term(100_000),
    )
    check("population term increases with population", small < medium < large)
    check(
        "population is log-scaled, not linear (10x people is far less than 10x score)",
        (medium / small) < 3.0,
        f"ratio was {medium / small:.2f}",
    )


def test_infra_deficit_tracks_severity() -> None:
    high = infra_deficit_term(["high", "high"])
    low = infra_deficit_term(["low", "low"])
    check("high severity yields a larger deficit than low", high > low)
    check("no severities yields zero deficit", infra_deficit_term([]) == 0.0)


def test_vulnerability_favours_small_settlements() -> None:
    scattered = vulnerability_term(population_affected=4_000, settlement_count=8)
    concentrated = vulnerability_term(population_affected=40_000, settlement_count=2)
    check(
        "small scattered settlements score more vulnerable than one large town",
        scattered > concentrated,
        f"{scattered} vs {concentrated}",
    )
    check(
        "unknown data returns 0.5, never 0 (absence of data is not absence of need)",
        vulnerability_term(0, 0) == 0.5,
    )


def test_real_vulnerability_no_longer_double_counts_distance() -> None:
    """
    Isolation (dist_district_hq_km) was removed 2026-09-15: it and
    feasibility's distance signal both came from the same Census CSV under
    two different administrative-level names. Confirm it's gone, and that
    the three remaining real signals still work standalone.
    """
    village = {
        "has_real_data": True,
        "dist_district_hq_km": 80.0,  # would have driven isolation to 1.0 before the fix
        "power_domestic_summer_hrs": 6.0,
        "drainage_none": 1,
        "conn_internet_csc": 1,
    }
    value, evidence = realdata.real_vulnerability([village])
    check(
        "real_vulnerability no longer reports district-HQ distance",
        "mean_km_to_district_hq" not in evidence,
    )
    check("real_vulnerability still returns a value from the other three signals", value is not None)
    check(
        "real_vulnerability labels its period as a structural baseline, not current condition",
        evidence.get("period") == "census_2011_structural_baseline",
    )

    distance_only = {"has_real_data": True, "dist_district_hq_km": 80.0}
    value2, evidence2 = realdata.real_vulnerability([distance_only])
    check(
        "a village with only distance data now yields no vulnerability signal at all",
        value2 is None and evidence2 == {},
    )


def test_feasibility_is_continuous_and_blends_road_connectivity() -> None:
    """Replaces the old <=15km binary cliff -- see scoring.py's feasibility_points."""
    full = feasibility_points(5.0, 1.0)
    zero = feasibility_points(60.0, 0.0)
    mid = feasibility_points(30.0, None)
    check("feasibility reaches full points when near and road-connected", math.isclose(full, config.FEASIBILITY_POINTS))
    check("feasibility reaches zero when far and unconnected", zero == 0.0)
    check(
        "feasibility fades continuously between the two, not a step function",
        0.0 < mid < full,
        f"mid={mid}",
    )

    connected = feasibility_points(30.0, 1.0)
    unconnected = feasibility_points(30.0, 0.0)
    check(
        "an all-weather-road village scores higher feasibility than an identical unconnected one",
        connected > unconnected,
        f"{connected} vs {unconnected}",
    )
    check(
        "unknown road status lands strictly between connected and unconnected, not penalised",
        unconnected < mid < connected,
    )
    check("no distance data yields zero feasibility however good the road", feasibility_points(None, 1.0) == 0.0)


def test_real_town_distance_and_road_connectivity() -> None:
    village = {"has_real_data": True, "dist_nearest_town_km": 12.5, "road_all_weather": 1}
    dist_val, dist_ev = realdata.real_town_distance_km([village])
    check("real_town_distance_km reads the Census nearest-town field", dist_val == 12.5)
    check("its evidence is labelled a real Census record", dist_ev.get("source") == "census_recorded_distance")

    road_val, _ = realdata.real_road_connectivity([village])
    check("an all-weather-road village returns a full connected share", road_val == 1.0, f"{road_val}")

    unconnected = {"has_real_data": True, "road_all_weather": 0}
    road_val2, _ = realdata.real_road_connectivity([unconnected])
    check("an unconnected village returns a zero connected share", road_val2 == 0.0)

    check("no data returns None, never a guessed 0", realdata.real_town_distance_km([{}]) == (None, {}))
    check("no data returns None for road connectivity too", realdata.real_road_connectivity([{}]) == (None, {}))


# ---------------------------------------------------------------------------
# Priority Score
# ---------------------------------------------------------------------------

def test_confidence_gate_damps_but_never_zeroes() -> None:
    """SS16.1: a missing input must not erase a real gap."""
    check("full confidence passes through", confidence_gate(1.0) == 1.0)
    check("confidence at threshold passes through", confidence_gate(config.CONFIDENCE_THRESHOLD) == 1.0)
    partial = confidence_gate(config.CONFIDENCE_THRESHOLD / 2)
    check("low confidence damps the score", 0 < partial < 1, f"gate was {partial}")

    low = score_cluster(**_baseline_kwargs(average_confidence=0.1))
    check(
        "a low-confidence cluster still scores above zero",
        low["priority_score"] > 0,
        f"score was {low['priority_score']}",
    )


def test_breakdown_sums_to_score() -> None:
    """
    THE load-bearing test. The dashboard explains a score by printing its
    nine parts; if they stop summing to the total, that explanation is a lie.
    """
    for label, kwargs in [
        ("typical", _baseline_kwargs()),
        ("low confidence", _baseline_kwargs(average_confidence=0.2)),
        ("equity block", _baseline_kwargs(block="Chandgad")),
        ("tiny cluster", _baseline_kwargs(unique_reporters=1, population_affected=200, settlement_count=1)),
        ("huge cluster", _baseline_kwargs(unique_reporters=900, population_affected=500_000, settlement_count=40)),
        ("confirmed collapse", _baseline_kwargs(emergency_grade=1.0, emergency_confidence=1.0, emergency_grade_label="collapse")),
        ("partial damage", _baseline_kwargs(emergency_grade=2/3, emergency_confidence=0.75, emergency_grade_label="partial")),
        ("crack", _baseline_kwargs(emergency_grade=1/3, emergency_confidence=0.5, emergency_grade_label="crack")),
    ]:
        result = score_cluster(**kwargs)
        total = sum(result["breakdown"].values())
        check(
            f"breakdown sums to priority_score ({label})",
            math.isclose(total, result["priority_score"], abs_tol=0.05),
            f"{total} != {result['priority_score']}",
        )


def test_breakdown_has_the_contract_keys() -> None:
    """The build plan's Interface Contract fixes these nine names."""
    expected = {
        "demand", "population", "infra_deficit", "vulnerability", "equity",
        "strategic", "urgency", "feasibility", "cost_penalty",
    }
    got = set(score_cluster(**_baseline_kwargs())["breakdown"])
    check("breakdown keys match the Interface Contract exactly", got == expected, f"got {sorted(got)}")


def test_score_stays_within_scale() -> None:
    maxed = score_cluster(
        unique_reporters=10_000,
        population_affected=1_000_000,
        settlement_count=500,
        severities=["high"] * 10,
        average_confidence=1.0,
        issue_category="road",
        block="Chandgad",
        distance_to_hq_km=1.0,
        emergency_grade=1.0,
        emergency_confidence=1.0,
    )
    check(
        "even a maximal cluster stays at or below 100",
        "even a maximal cluster with full emergency urgency stays at or below 100",
        maxed["priority_score"] <= 100.0,
        f"score was {maxed['priority_score']}",
    )


def test_equity_can_flip_the_ranking() -> None:
    """
    The entire thesis, as an assertion: a quieter cluster in a
    low-connectivity block must be able to outrank a louder one that
    reports easily.
    """
    quiet_underserved = score_cluster(
        **_baseline_kwargs(unique_reporters=4, block="Chandgad", severities=["high", "high"])
    )
    loud_well_served = score_cluster(
        **_baseline_kwargs(unique_reporters=20, block="Kagal", severities=["low", "low"])
    )
    check(
        "4 reports from an underserved block outrank 20 from a well-served one",
        quiet_underserved["priority_score"] > loud_well_served["priority_score"],
        f"{quiet_underserved['priority_score']} vs {loud_well_served['priority_score']}",
    )
    check("equity term is what carries it", equity_points("Chandgad") > equity_points("Kagal"))


# ---------------------------------------------------------------------------
# Clustering + geography
# ---------------------------------------------------------------------------

def test_haversine_is_sane() -> None:
    # Kolhapur city to Kagal is roughly 20 km on the ground.
    d = haversine_km(16.7050, 74.2433, 16.5833, 74.3167)
    check("haversine gives a plausible Kolhapur-Kagal distance", 10 < d < 30, f"{d:.1f} km")
    check("distance from a point to itself is zero", haversine_km(16.7, 74.2, 16.7, 74.2) == 0.0)


def test_gate_blocks_distant_pairs() -> None:
    """Step 2's gate must make out-of-range pairs unclusterable."""
    import numpy as np

    coords = [(16.70, 74.24), (16.71, 74.25), (20.00, 73.79)]  # third is ~370km away
    vectors = np.array([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]], dtype=np.float32)
    distance = build_distance_matrix(coords, vectors)

    check(
        "nearby identical reports stay within eps",
        distance[0][1] < config.DBSCAN_EPS_KM,
        f"{distance[0][1]:.2f}",
    )
    check(
        "a far-away report is pushed beyond the gate penalty",
        distance[0][2] >= config.GATE_PENALTY_KM,
        f"{distance[0][2]:.2f}",
    )


def test_semantic_distance_separates_meanings() -> None:
    import numpy as np

    coords = [(16.70, 74.24), (16.70, 74.24)]
    same = build_distance_matrix(coords, np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32))
    different = build_distance_matrix(coords, np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
    check(
        "same-meaning reports at one spot are closer than different-meaning ones",
        same[0][1] < different[0][1],
        f"{same[0][1]:.2f} vs {different[0][1]:.2f}",
    )


def test_catchment_population_respects_radius() -> None:
    gazetteer = [
        {"name": "Near", "population": 5_000, "lat": 16.700, "lon": 74.240},
        {"name": "Far", "population": 9_000, "lat": 20.000, "lon": 73.790},
        {"name": "NoPop", "population": None, "lat": 16.701, "lon": 74.241},
    ]
    total = population_in_catchment(16.700, 74.240, "road", gazetteer)
    check("only settlements inside the catchment are counted", total == 5_000, f"got {total}")


# ---------------------------------------------------------------------------
# What-if
# ---------------------------------------------------------------------------

def test_budget_points_are_capped() -> None:
    huge = budget_to_points(500 * config.RUPEES_PER_CRORE)
    check(
        "an enormous budget cannot swamp the formula",
        huge <= config.WHATIF_MAX_POINTS,
        f"{huge}",
    )
    check("a negative budget delta reduces the score", budget_to_points(-10 * config.RUPEES_PER_CRORE) < 0)
    check("zero budget change is neutral", budget_to_points(0) == 0.0)


def test_weights_sum_to_one() -> None:
    total = config.W_DEMAND + config.W_POPULATION + config.W_INFRA_DEFICIT + config.W_VULNERABILITY
    check("Gap Score weights sum to 1.0", math.isclose(total, 1.0), f"sum was {total}")


def test_apply_precise_coords() -> None:
    """Unit-test the coordinate substitution helper."""
    from intelligence.recompute import _apply_precise_coords

    base = {"id": 1, "latitude": 16.7, "longitude": 74.2}

    # Both present -> pin
    result = _apply_precise_coords([{**base, "precise_lat": 16.8, "precise_lon": 74.3}])
    check("both precise coords present -> uses pin lat",
          result[0]["latitude"] == 16.8, f"got {result[0]['latitude']}")
    check("both precise coords present -> uses pin lon",
          result[0]["longitude"] == 74.3, f"got {result[0]['longitude']}")

    # One missing -> centroid
    result = _apply_precise_coords([{**base, "precise_lat": 16.8, "precise_lon": None}])
    check("one precise coord missing -> keeps centroid lat",
          result[0]["latitude"] == 16.7, f"got {result[0]['latitude']}")

    # Both missing -> centroid
    result = _apply_precise_coords([{**base, "precise_lat": None, "precise_lon": None}])
    check("both precise coords missing -> keeps centroid",
          result[0]["latitude"] == 16.7 and result[0]["longitude"] == 74.2, "")

    # No keys at all -> centroid (pre-feature reports)
    result = _apply_precise_coords([dict(base)])
    check("no precise keys at all -> keeps centroid",
          result[0]["latitude"] == 16.7, f"got {result[0]['latitude']}")


def test_precise_coords_split_work_groups() -> None:
    """Two reports >250m apart must form two work groups; at centroid, one."""
    from intelligence.recompute import _work_groups

    centroid_lat, centroid_lon = 16.7050, 74.2433
    point_a = (16.7070, 74.2433)  # ~220m north
    point_b = (16.7034, 74.2433)  # ~180m south (total ~400m apart)

    base = {"id": 1, "raw_text": "road broken", "issue_category": "road",
            "severity": "high", "district": "Kolhapur", "block": "Karvir",
            "village": "Shinganapur", "confidence": 0.9,
            "precise_lat": None, "precise_lon": None}

    # With precise coords >250m apart -> TWO groups
    members = [
        {**base, "id": 1, "latitude": point_a[0], "longitude": point_a[1], "precise_lat": point_a[0], "precise_lon": point_a[1]},
        {**base, "id": 2, "latitude": point_b[0], "longitude": point_b[1], "precise_lat": point_b[0], "precise_lon": point_b[1]},
    ]
    groups = _work_groups(members)
    check("precise coords >250m apart -> two work groups",
          len(groups) == 2,
          f"got {len(groups)}")
    check("both groups record citizen_gps_pin location_basis",
          all(g.get("location_basis") == "citizen_gps_pin" for g in groups),
          f"got {[g.get('location_basis') for g in groups]}")

    # At centroid -> ONE group
    members_centroid = [
        {**base, "id": 1, "latitude": centroid_lat, "longitude": centroid_lon},
        {**base, "id": 2, "latitude": centroid_lat, "longitude": centroid_lon},
    ]
    groups_centroid = _work_groups(members_centroid)
    check("same reports at centroid -> one work group",
          len(groups_centroid) == 1,
          f"got {len(groups_centroid)}")
    check("centroid group records village_centroid location_basis",
          groups_centroid[0].get("location_basis") == "village_centroid",
          f"got {groups_centroid[0].get('location_basis')}")


def test_nwdp_groundwater_lookup() -> None:
    # 1. Fallback / CSV loader
    stations = realdata.load_groundwater_index(db=None)
    check("NWDP stations loaded from snapshot", len(stations) >= 9, f"got {len(stations)}")

    # 2. Near station lookup (Hatkanangale coords: 16.7444, 74.4258)
    near_st, evidence = realdata.lookup_groundwater(16.74, 74.42, stations, max_distance_km=25.0)
    check("found station within 25km radius", near_st is not None)
    check("matched Hatkanangale station", near_st.get("station_name") == "Hatkanangale" if near_st else False)
    check(
        "evidence matches expected bgl format and trend",
        evidence == "groundwater level in this area: 1.7m bgl, trend: falling",
        f"got {evidence}",
    )

    # 3. Distant point beyond threshold (Delhi)
    far_st, far_ev = realdata.lookup_groundwater(28.61, 77.20, stations, max_distance_km=25.0)
    check("distant point returns no station", far_st is None)
    check("distant point returns no evidence", far_ev is None)

    # 4. Empty stations list
    none_st, none_ev = realdata.lookup_groundwater(16.74, 74.42, [], max_distance_km=25.0)
    check("empty stations list returns None", none_st is None and none_ev is None)


# ---------------------------------------------------------------------------
# Village & Asset Priority tests
# ---------------------------------------------------------------------------

def test_asset_identity_five_rules() -> None:
    gazetteer = [
        {"id": 1, "name": "Girgaon", "district": "Kolhapur", "block": "Karvir", "population": 4000, "lat": 16.700, "lon": 74.200},
    ]
    facilities_by_category = {
        "education": [
            {"id": 10, "name": "Z.P. Primary School", "category": "education", "lat": 16.701, "lon": 74.201, "source": "udise", "external_id": "U10"},
            {"id": 11, "name": "Vidyamandir School", "category": "education", "lat": 16.702, "lon": 74.202, "source": "udise", "external_id": "U11"},
        ],
        "health": [
            {"id": 20, "name": "Primary Health Centre Girgaon", "category": "health", "lat": 16.705, "lon": 74.205, "source": "healthgis", "external_id": "H20"},
        ],
    }
    works_by_village = {
        "Girgaon": [{"name": "Girgaon to Phata Road", "external_id": "P1", "status": "completed", "cost_lakh": 25.0, "year": 2022}]
    }

    # 1. Rule 1: Citizen-selected facility
    r1 = [{
        "id": 1, "issue_category": "education", "facility_id": 11, "village": "Girgaon",
        "district": "Kolhapur", "block": "Karvir", "latitude": 16.700, "longitude": 74.200,
        "raw_text": "Need repairs at Vidyamandir", "severity": "medium", "confidence": 0.9,
    }]
    assets_r1 = _build_assets(r1, facilities_by_category=facilities_by_category, works_by_village=works_by_village, gazetteer=gazetteer, amenity_index=[], works_index=[], gw_stations=[])
    check("rule 1 citizen-selected: creates 1 asset", len(assets_r1) == 1)
    check("rule 1 citizen-selected: name_basis is citizen_selected", assets_r1[0].name_basis == "citizen_selected")
    check("rule 1 citizen-selected: names the chosen facility", assets_r1[0].name == "Vidyamandir School")

    # 2. Rule 2: Pin next to a facility (within ASSET_PIN_SNAP_M = 300m)
    # 16.701, 74.201 is Z.P. Primary School. Pin at 16.7011, 74.2010 is ~11m away.
    r2 = [{
        "id": 2, "issue_category": "education", "facility_id": None, "village": "Girgaon",
        "district": "Kolhapur", "block": "Karvir", "latitude": 16.7011, "longitude": 74.2010,
        "precise_lat": 16.7011, "precise_lon": 74.2010, "pin_source": "citizen_gps",
        "raw_text": "Roof leaking", "severity": "high", "confidence": 0.95,
    }]
    assets_r2 = _build_assets(r2, facilities_by_category=facilities_by_category, works_by_village=works_by_village, gazetteer=gazetteer, amenity_index=[], works_index=[], gw_stations=[])
    check("rule 2 pin-snap: creates 1 asset", len(assets_r2) == 1)
    check("rule 2 pin-snap: name_basis is nearest_register", assets_r2[0].name_basis == "nearest_register")
    check("rule 2 pin-snap: snaps to nearest school", assets_r2[0].name == "Z.P. Primary School")

    # 3. Rule 3: Health with no pin (nearest within 8km)
    r3 = [{
        "id": 3, "issue_category": "health", "facility_id": None, "village": "Girgaon",
        "district": "Kolhapur", "block": "Karvir", "latitude": 16.700, "longitude": 74.200,
        "raw_text": "Doctor not present", "severity": "high", "confidence": 0.9,
    }]
    assets_r3 = _build_assets(r3, facilities_by_category=facilities_by_category, works_by_village=works_by_village, gazetteer=gazetteer, amenity_index=[], works_index=[], gw_stations=[])
    check("rule 3 health no pin: creates 1 asset", len(assets_r3) == 1)
    check("rule 3 health no pin: name_basis is nearest_register", assets_r3[0].name_basis == "nearest_register")
    check("rule 3 health no pin: names PHC", assets_r3[0].name == "Primary Health Centre Girgaon")

    # 4. Rule 4: Education with no facility and no pin -> unresolved village school
    r4 = [{
        "id": 4, "issue_category": "education", "facility_id": None, "village": "Girgaon",
        "district": "Kolhapur", "block": "Karvir", "latitude": 16.700, "longitude": 74.200,
        "raw_text": "Desks broken", "severity": "medium", "confidence": 0.8,
    }]
    assets_r4 = _build_assets(r4, facilities_by_category={"education": [], "health": []}, works_by_village={}, gazetteer=gazetteer, amenity_index=[], works_index=[], gw_stations=[])
    check("rule 4 education unresolved: creates 1 asset", len(assets_r4) == 1)
    check("rule 4 education unresolved: name_basis is unresolved_village", assets_r4[0].name_basis == "unresolved_village")
    check("rule 4 education unresolved: expected name format", assets_r4[0].name == "School in Girgaon (not specified)")

    # 5. Rule 5: Road (1 PMGSY work) and Water
    r5_road = [{
        "id": 5, "issue_category": "road", "village": "Girgaon", "district": "Kolhapur", "block": "Karvir",
        "latitude": 16.700, "longitude": 74.200, "raw_text": "Potholes", "severity": "medium", "confidence": 0.9,
    }]
    assets_r5_road = _build_assets(r5_road, facilities_by_category=facilities_by_category, works_by_village=works_by_village, gazetteer=gazetteer, amenity_index=[], works_index=[], gw_stations=[])
    check("rule 5 road 1 pmgsy work: names pmgsy work", assets_r5_road[0].name == "Girgaon to Phata Road")
    check("rule 5 road 1 pmgsy work: name_basis is pmgsy_work", assets_r5_road[0].name_basis == "pmgsy_work")

    r5_water = [{
        "id": 6, "issue_category": "water", "village": "Girgaon", "district": "Kolhapur", "block": "Karvir",
        "latitude": 16.700, "longitude": 74.200, "raw_text": "No tap water", "severity": "high", "confidence": 0.9,
    }]
    assets_r5_water = _build_assets(r5_water, facilities_by_category=facilities_by_category, works_by_village=works_by_village, gazetteer=gazetteer, amenity_index=[], works_index=[], gw_stations=[])
    check("rule 5 water: name_basis is unnamed_pin", assets_r5_water[0].name_basis == "unnamed_pin")
    check("rule 5 water: name is Water point near Girgaon", assets_r5_water[0].name == "Water point near Girgaon")


def test_citizen_selected_beats_nearby_pin() -> None:
    gazetteer = [{"id": 1, "name": "Girgaon", "district": "Kolhapur", "block": "Karvir", "population": 4000, "lat": 16.700, "lon": 74.200}]
    facilities_by_category = {
        "education": [
            {"id": 10, "name": "Facility A (Selected)", "category": "education", "lat": 16.710, "lon": 74.210, "source": "udise", "external_id": "FA"},
            {"id": 11, "name": "Facility B (Near Pin)", "category": "education", "lat": 16.7001, "lon": 74.2001, "source": "udise", "external_id": "FB"},
        ],
    }
    # Pin sits right at Facility B (~15m away), but citizen explicitly selected Facility A
    report = [{
        "id": 1, "issue_category": "education", "facility_id": 10, "village": "Girgaon",
        "district": "Kolhapur", "block": "Karvir", "latitude": 16.7001, "longitude": 74.2001,
        "precise_lat": 16.7001, "precise_lon": 74.2001, "pin_source": "citizen_gps",
        "raw_text": "Selected Facility A complaint", "severity": "medium", "confidence": 0.9,
    }]
    assets = _build_assets(report, facilities_by_category=facilities_by_category, works_by_village={}, gazetteer=gazetteer, amenity_index=[], works_index=[], gw_stations=[])
    check("citizen-selected beats pin: 1 asset created", len(assets) == 1)
    check("citizen-selected beats pin: names selected facility", assets[0].name == "Facility A (Selected)")
    check("citizen-selected beats pin: name_basis is citizen_selected", assets[0].name_basis == "citizen_selected")


def test_demo_seeded_facility_is_not_labelled_citizen_picked() -> None:
    """A facility attached by the demo-seeding script must say so, not claim a citizen chose it."""
    gazetteer = [{"id": 1, "name": "Girgaon", "district": "Kolhapur", "block": "Karvir", "population": 4000, "lat": 16.700, "lon": 74.200}]
    facilities_by_category = {
        "education": [
            {"id": 10, "name": "Z.P. School Girgaon", "category": "education", "lat": 16.701, "lon": 74.201, "source": "udise", "external_id": "Z10"},
        ],
    }
    base = {
        "issue_category": "education", "facility_id": 10, "village": "Girgaon",
        "district": "Kolhapur", "block": "Karvir", "latitude": 16.701, "longitude": 74.201,
        "precise_lat": 16.701, "precise_lon": 74.201, "severity": "medium", "confidence": 0.9,
    }
    demo_only = [{**base, "id": 1, "pin_source": "synthetic_seed", "raw_text": "demo complaint"}]
    assets = _build_assets(demo_only, facilities_by_category=facilities_by_category, works_by_village={}, gazetteer=gazetteer, amenity_index=[], works_index=[], gw_stations=[])
    check("demo-seeded facility pick: name_basis is demo_assigned",
          assets[0].name_basis == "demo_assigned", f"got {assets[0].name_basis}")

    mixed = demo_only + [{**base, "id": 2, "pin_source": "citizen_gps", "raw_text": "real complaint"}]
    assets = _build_assets(mixed, facilities_by_category=facilities_by_category, works_by_village={}, gazetteer=gazetteer, amenity_index=[], works_index=[], gw_stations=[])
    check("one real citizen pick outranks demo picks: name_basis is citizen_selected",
          assets[0].name_basis == "citizen_selected", f"got {assets[0].name_basis}")


def test_asset_breakdown_sums_to_priority_score() -> None:
    import json
    gazetteer = [{"id": 1, "name": "Girgaon", "district": "Kolhapur", "block": "Karvir", "population": 4000, "lat": 16.700, "lon": 74.200}]
    reports = [{
        "id": 1, "issue_category": "road", "village": "Girgaon", "district": "Kolhapur", "block": "Karvir",
        "latitude": 16.700, "longitude": 74.200, "raw_text": "Potholes", "severity": "high", "confidence": 0.85,
    }]
    assets = _build_assets(reports, facilities_by_category={}, works_by_village={}, gazetteer=gazetteer, amenity_index=[], works_index=[], gw_stations=[])
    asset = assets[0]
    bd = json.loads(asset.breakdown)
    check("asset breakdown sums to priority_score", math.isclose(round(sum(bd.values()), 2), round(asset.priority_score, 2), abs_tol=0.01))


def test_village_score_equals_max_asset_score() -> None:
    gazetteer = [{"id": 1, "name": "Girgaon", "district": "Kolhapur", "block": "Karvir", "population": 4000, "lat": 16.700, "lon": 74.200}]
    # Two reports with different severities yielding two different road assets (>400m apart)
    reports = [
        {"id": 1, "issue_category": "road", "village": "Girgaon", "district": "Kolhapur", "block": "Karvir",
         "latitude": 16.700, "longitude": 74.200, "precise_lat": 16.700, "precise_lon": 74.200, "raw_text": "Road 1 bad", "severity": "low", "confidence": 0.9},
        {"id": 2, "issue_category": "road", "village": "Girgaon", "district": "Kolhapur", "block": "Karvir",
         "latitude": 16.710, "longitude": 74.210, "precise_lat": 16.710, "precise_lon": 74.210, "raw_text": "Road 2 collapsed", "severity": "critical", "confidence": 0.95},
    ]
    assets = _build_assets(reports, facilities_by_category={}, works_by_village={}, gazetteer=gazetteer, amenity_index=[], works_index=[], gw_stations=[])
    villages = _build_villages(reports, assets, gazetteer)
    check("village score equals max of asset scores", len(villages) == 1 and villages[0].priority_score == max(a.priority_score for a in assets))
    check("village top_asset_id matches max asset id", villages[0].top_asset_id == max(assets, key=lambda a: a.priority_score).id)


def test_hospital_reports_from_two_villages_one_asset() -> None:
    import json
    gazetteer = [
        {"id": 1, "name": "Girgaon", "district": "Kolhapur", "block": "Karvir", "population": 4000, "lat": 16.700, "lon": 74.200},
        {"id": 2, "name": "Balinge", "district": "Kolhapur", "block": "Karvir", "population": 3000, "lat": 16.705, "lon": 74.205},
    ]
    facilities = {
        "health": [{"id": 50, "name": "Sub-District Hospital", "category": "health", "lat": 16.702, "lon": 74.202, "source": "healthgis", "external_id": "SDH1"}],
    }
    reports = [
        {"id": 1, "issue_category": "health", "facility_id": 50, "village": "Girgaon", "district": "Kolhapur", "block": "Karvir", "latitude": 16.700, "longitude": 74.200, "raw_text": "T1", "severity": "high", "confidence": 0.9},
        {"id": 2, "issue_category": "health", "facility_id": 50, "village": "Balinge", "district": "Kolhapur", "block": "Karvir", "latitude": 16.705, "longitude": 74.205, "raw_text": "T2", "severity": "high", "confidence": 0.9},
    ]
    assets = _build_assets(reports, facilities_by_category=facilities, works_by_village={}, gazetteer=gazetteer, amenity_index=[], works_index=[], gw_stations=[])
    check("hospital from 2 villages creates 1 asset", len(assets) == 1)
    serv = json.loads(assets[0].villages_served)
    check("asset records both villages_served", sorted(serv) == ["Balinge", "Girgaon"])
    villages = _build_villages(reports, assets, gazetteer)
    check("both villages include this asset", len(villages) == 2 and all(v.asset_count == 1 for v in villages))


def test_candidate_list_sorted_and_capped() -> None:
    schools = [
        {"name": f"School {i}", "external_id": f"S{i}", "lat": 16.700 + (i * 0.001), "lon": 74.200}
        for i in range(1, 9)
    ]
    cands = _find_school_candidates(16.700, 74.200, schools)
    check("candidates capped at 6", len(cands) == 6)
    dists = [c["distance_m"] for c in cands]
    check("candidates sorted by distance ascending", dists == sorted(dists))


def test_road_pins_clustering_distance() -> None:
    r_400m = [
        {"latitude": 16.7000, "longitude": 74.2000},
        {"latitude": 16.7036, "longitude": 74.2000},
    ]
    groups_400 = _group_by_radius(r_400m, config.ASSET_GROUP_RADIUS_M)
    check("two road pins 400m apart give two groups", len(groups_400) == 2)

    r_100m = [
        {"latitude": 16.7000, "longitude": 74.2000},
        {"latitude": 16.7009, "longitude": 74.2000},
    ]
    groups_100 = _group_by_radius(r_100m, config.ASSET_GROUP_RADIUS_M)
    check("two road pins 100m apart give one group", len(groups_100) == 1)


def test_pmgsy_road_segment_lookup() -> None:
    from .realdata import nearest_road_segment, load_road_segment_index

    # 1. None coordinates return None
    res, dist = nearest_road_segment(None, None, [])
    check("nearest road: None coords returns (None, None)", res is None and dist is None)

    # 2. Empty segments returns None
    res, dist = nearest_road_segment(16.5, 74.5, [])
    check("nearest road: empty segments returns (None, None)", res is None and dist is None)

    # 3. Test road segment: straight line from (16.500, 74.200) to (16.510, 74.200) (~1.1 km long)
    test_seg = {
        "id": 1,
        "road_name": "Test Village Road",
        "drrp_road_code": "VR-1",
        "road_category": "RR(VR)",
        "district": "Kolhapur",
        "block": "Karvir",
        "points": [[16.500, 74.200], [16.505, 74.200], [16.510, 74.200]],
        "min_lat": 16.500,
        "max_lat": 16.510,
        "min_lon": 74.200,
        "max_lon": 74.200,
    }

    # Point at (16.505, 74.2002): ~21 meters East of midpoint
    res, dist = nearest_road_segment(16.505, 74.2002, [test_seg], max_distance_m=150.0)
    check("nearest road: point ~21m away matches", res is not None and res["id"] == 1)
    check("nearest road: matched distance is ~21m", dist is not None and 18.0 <= dist <= 25.0)

    # Point at (16.505, 74.205): ~515 meters away
    res, dist = nearest_road_segment(16.505, 74.205, [test_seg], max_distance_m=150.0)
    check("nearest road: point >150m away returns None", res is None and dist is None)

    # Point at (16.505, 74.205) with max_distance_m=600.0 matches
    res, dist = nearest_road_segment(16.505, 74.205, [test_seg], max_distance_m=600.0)
    check("nearest road: point matches with wider threshold", res is not None and dist is not None and dist > 450.0)

    # 4. Test load_road_segment_index with mock DB session
    class MockRow:
        id = 10
        external_id = 999
        district = "Kolhapur"
        block = "Karvir"
        drrp_road_code = "VR 10"
        road_name = "Mock Road"
        road_category = "RR(VR)"
        road_owner = "RWD"
        start_lat = 16.500
        start_lon = 74.200
        end_lat = 16.510
        end_lon = 74.200
        points_json = '[[16.500, 74.200], [16.510, 74.200]]'
        point_count = 2
        min_lat = 16.500
        max_lat = 16.510
        min_lon = 74.200
        max_lon = 74.200

    class MockQuery:
        def all(self):
            return [MockRow()]

    class MockDB:
        def query(self, model):
            return MockQuery()

    loaded = load_road_segment_index(MockDB())
    check("load_road_segment_index: returns records from db", len(loaded) == 1)
    check("load_road_segment_index: parses road_name", loaded[0]["road_name"] == "Mock Road")
    check("load_road_segment_index: parses points list", len(loaded[0]["points"]) == 2)


def test_road_asset_matches_geosadak_only_with_a_real_pin() -> None:
    import json

    gazetteer = [
        {"id": 1, "name": "Girgaon", "district": "Kolhapur", "block": "Karvir", "population": 4000, "lat": 16.700, "lon": 74.200},
    ]
    works_by_village = {
        "Girgaon": [{"name": "Girgaon to Phata Road", "external_id": "P1", "status": "completed", "cost_lakh": 25.0, "year": 2022}]
    }
    # A straight north-south road along lon 74.200, ~1.7 km long.
    road = {
        "id": 7, "external_id": "GS7", "district": "Kolhapur", "block": "Karvir",
        "drrp_road_code": "VR 18", "road_name": "Girgaon Link Road",
        "road_category": "RR(VR)", "road_owner": "ZP",
        "points": [[16.695, 74.200], [16.7025, 74.200], [16.710, 74.200]],
        "min_lat": 16.695, "max_lat": 16.710, "min_lon": 74.200, "max_lon": 74.200,
    }

    def road_report(rid, lat, lon, pinned=True):
        r = {
            "id": rid, "issue_category": "road", "village": "Girgaon", "district": "Kolhapur",
            "block": "Karvir", "latitude": lat, "longitude": lon,
            "raw_text": "Road broken", "severity": "medium", "confidence": 0.9,
        }
        if pinned:
            r.update({"precise_lat": lat, "precise_lon": lon, "pin_source": "citizen_gps"})
        return r

    def build(reports, segments):
        return _build_assets(
            reports, facilities_by_category={}, works_by_village=works_by_village,
            gazetteer=gazetteer, amenity_index=[], works_index=[], gw_stations=[],
            road_segments=segments,
        )

    # ~106 m east of the road, pinned by a citizen -> matched.
    [a] = build([road_report(1, 16.7000, 74.2010)], [road])
    ev = json.loads(a.evidence)
    check("pinned road ~106 m away: named from GeoSadak", a.name == "Girgaon Link Road")
    check("pinned road: name_basis geosadak_segment", a.name_basis == "geosadak_segment")
    check("pinned road: source pmgsy_geosadak", a.source == "pmgsy_geosadak" and a.external_id == "GS7")
    check("pinned road: full road shape stored", ev.get("road_geometry", {}).get("points") == road["points"])
    check("pinned road: distance recorded (~106 m)",
          90 < ev["road_geometry"]["distance_m"] < 125)

    # Same spot but only the village centre is known -> NOT matched to a road.
    [a] = build([road_report(2, 16.7000, 74.2010, pinned=False)], [road])
    check("village-centre road is never named after a nearby road", a.name_basis != "geosadak_segment")
    check("village-centre road falls back to the PMGSY work", a.name_basis == "pmgsy_work")
    check("village-centre road carries no road shape", "road_geometry" not in json.loads(a.evidence))

    # Pinned ~320 m from the road -> inside the 500m default (raised
    # 2026-09-15 from 150m, owner decision, see DEFAULT_ROAD_SEGMENT_MAX_DISTANCE_M),
    # so this now matches where it used to fall back.
    [a] = build([road_report(3, 16.7000, 74.2030)], [road])
    check("pinned road 320 m away is now matched under the wider default", a.name_basis == "geosadak_segment")

    # Pinned ~700 m from the road -> still outside the 500m default, falls back.
    [a] = build([road_report(5, 16.7000, 74.2054)], [road])
    check("pinned road 700 m away is still not matched", a.name_basis == "pmgsy_work")

    # No segments loaded at all -> identical to the old behaviour.
    [a] = build([road_report(4, 16.7000, 74.2010)], None)
    check("no GeoSadak data: old naming unchanged", a.name_basis == "pmgsy_work")

    # Unnamed road -> its DRRP code is the name.
    unnamed = dict(road, road_name=None)
    [a] = build([road_report(5, 16.7000, 74.2010)], [unnamed])
    check("unnamed GeoSadak road falls back to its DRRP code", a.name == "VR 18")

    # Two complaint spots ~550 m apart on the same road -> distinct names.
    two = build([road_report(6, 16.6980, 74.2005), road_report(7, 16.7030, 74.2005)], [road])
    names = sorted(x.name for x in two)
    check("two spots on one road: two assets", len(two) == 2)
    check("two spots on one road: second is numbered",
          names == ["Girgaon Link Road", "Girgaon Link Road #2"])

    # Water next to the road never picks up road geometry.
    water = dict(road_report(8, 16.7000, 74.2010), issue_category="water")
    [a] = build([water], [road])
    check("water asset near a road gets no road shape", "road_geometry" not in json.loads(a.evidence))


def test_school_condition_index_and_evidence() -> None:
    from .realdata import load_school_condition_index, school_condition_evidence

    # 1. school_condition_evidence edge cases
    raw, text = school_condition_evidence(1, {})
    check("school condition evidence: empty index returns (None, None)", raw is None and text is None)

    raw, text = school_condition_evidence(999, {1: {"facility_id": 1}})
    check("school condition evidence: missing facility returns (None, None)", raw is None and text is None)

    # 2. Formatted evidence string verification
    sample_row = {
        "id": 10,
        "facility_id": 42,
        "udise_code": "27341212203",
        "year_desc": "2024-25",
        "teachers_regular": 5,
        "teachers_contract": 0,
        "teachers_part_time": 0,
        "classrooms_total": 5,
        "classrooms_good": 4,
        "classrooms_minor_repair": 1,
        "classrooms_major_repair": 0,
        "toilet_boys_functional": 1,
        "toilet_girls_functional": 1,
        "drinking_water": True,
        "electricity": True,
        "boundary_wall_status": "7-Partial",
        "total_grant": 25000.0,
        "total_expenditure": 25000.0,
        "raw_json": "{}",
        "fetched_at": None,
    }
    index = {42: sample_row}
    r_out, t_out = school_condition_evidence(42, index)
    check("school condition evidence: returns row dict", r_out is not None and r_out["facility_id"] == 42)
    expected_text = "5 teachers (2024-25); 4 of 5 classrooms good, 1 needs minor repair; grant ₹25,000, spent ₹25,000."
    check("school condition evidence: sentence format matches specification", t_out == expected_text)

    # 3. load_school_condition_index with mock DB
    class MockConditionRow:
        def __init__(self, id, facility_id, udise_code, fetch_failed):
            self.id = id
            self.facility_id = facility_id
            self.udise_code = udise_code
            self.fetch_failed = fetch_failed
            self.year_desc = "2024-25"
            self.teachers_regular = 3
            self.teachers_contract = 0
            self.teachers_part_time = 0
            self.classrooms_total = 3
            self.classrooms_good = 3
            self.classrooms_minor_repair = 0
            self.classrooms_major_repair = 0
            self.toilet_boys_functional = 1
            self.toilet_girls_functional = 1
            self.drinking_water = True
            self.electricity = True
            self.boundary_wall_status = "All Good"
            self.total_grant = 10000.0
            self.total_expenditure = 8000.0
            self.raw_json = "{}"
            self.fetched_at = None

    class MockQuery:
        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            # Row 1 is valid, Row 2 has facility_id=None, Row 3 has fetch_failed=True (mock query filter would omit it, but test index logic)
            return [
                MockConditionRow(1, 101, "273401", False),
                MockConditionRow(2, None, "273402", False),
            ]

    class MockDB:
        def query(self, model):
            return MockQuery()

    loaded = load_school_condition_index(MockDB())
    check("load_school_condition_index: loads valid rows", 101 in loaded)
    check("load_school_condition_index: ignores None facility_id", None not in loaded and len(loaded) == 1)
    check("load_school_condition_index: parses teachers", loaded[101]["teachers_regular"] == 3)


def test_water_testing_evidence():
    index = {100: {"samples_tested": 30, "villages_not_tested": 0}}
    row, text = realdata.water_testing_evidence(100, index)
    check("water testing evidence format", text == "30 samples tested this year, 0 villages untested nearby", f"got {text}")
    r2, t2 = realdata.water_testing_evidence(999, index)
    check("water testing evidence absent", r2 is None and t2 is None)

    # Catchment aggregation: a water cluster's villages_near() list, not a
    # single gazetteer_id -- most JJM rows are finer-grained than the
    # gazetteer, so only some catchment villages are expected to match.
    villages = [{"gazetteer_id": 100}, {"gazetteer_id": 101}, {"gazetteer_id": 999}]
    wide_index = {100: {"samples_tested": 30}, 101: {"samples_tested": 12}}
    ev, sentence = realdata.water_testing_catchment_evidence(villages, wide_index)
    check("water testing catchment: counts matched vs total", ev == {
        "villages_tested_this_year": 2, "villages_in_catchment": 3, "samples_tested_total": 42,
    }, f"got {ev}")
    check("water testing catchment: sentence names the partial match",
          sentence == "2 of 3 catchment villages tested this year (JJM WQMIS, current cycle): 42 samples total",
          f"got {sentence}")
    ev_none, sentence_none = realdata.water_testing_catchment_evidence(villages, {})
    check("water testing catchment: no data is (None, None), never zero", ev_none is None and sentence_none is None)


def test_hazard_near():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)

    # hazard_near takes pre-loaded plain-dict lists (same pattern as every
    # other real_* lookup in this file) -- never a live db session per call.
    check("hazard_near: no lat/lon returns (False, {})", realdata.hazard_near(None, None, [], []) == (False, {}))

    # 1. Nearby river reading
    rr = {"lat": 16.7, "lon": 74.2, "observed_at": now, "name": "Test River", "value": 123.4, "datatype_code": "MSD"}
    is_haz, ev = realdata.hazard_near(16.7, 74.2, [rr], [])
    check("hazard_near detects nearby river warning", is_haz and "cwc_river_warnings" in ev)

    # 2. Distant river reading
    rr_far = {"lat": 28.0, "lon": 77.0, "observed_at": now, "name": "Far River", "value": 10.0, "datatype_code": "MSD"}
    is_haz2, ev2 = realdata.hazard_near(16.7, 74.2, [rr_far], [])
    check("hazard_near ignores distant river reading", not is_haz2 and not ev2)

    # 3. A stale reading outside the window is ignored
    rr_old = {"lat": 16.7, "lon": 74.2, "observed_at": now - timedelta(hours=200), "name": "Old", "value": 1.0, "datatype_code": "MSD"}
    is_haz_old, ev_old = realdata.hazard_near(16.7, 74.2, [rr_old], [])
    check("hazard_near ignores a reading older than the window", not is_haz_old and not ev_old)

    # 4. SACHET alert in Kolhapur bounds
    alert = {"districts": "Kolhapur", "effective": now - timedelta(days=1), "expires": now + timedelta(days=1), "event": "Heavy Rain"}
    is_haz3, ev3 = realdata.hazard_near(16.7, 74.2, [], [alert])
    check("hazard_near detects active SACHET alert in bounds", is_haz3 and ev3.get("sachet_alerts") == 1)

    # 5. An expired SACHET alert is ignored
    expired = {"districts": "Kolhapur", "effective": now - timedelta(days=5), "expires": now - timedelta(days=1), "event": "Heavy Rain"}
    is_haz4, ev4 = realdata.hazard_near(16.7, 74.2, [], [expired])
    check("hazard_near ignores an expired SACHET alert", not is_haz4 and not ev4)

    # 6. load_river_readings_index / load_hazard_alerts_index against a mock DB
    # SQLite drops tzinfo on round-trip -- every DateTime column comes back
    # naive even though it was written as UTC. Mock rows use naive values
    # here on purpose, so this test actually exercises that normalization
    # instead of masking it with already-aware datetimes.
    naive_now = now.replace(tzinfo=None)

    class MockRiverReadingRow:
        def __init__(self):
            self.name, self.lat, self.lon = "Test River", 16.7, 74.2
            self.value, self.datatype_code, self.observed_at = 123.4, "MSD", naive_now

    class MockHazardAlertRow:
        def __init__(self):
            self.districts, self.event = "Kolhapur", "Heavy Rain"
            self.effective = naive_now - timedelta(days=1)
            self.expires = naive_now + timedelta(days=1)

    class MockHazardQuery:
        def __init__(self, items):
            self.items = items
        def all(self):
            return self.items

    class MockHazardDB:
        def query(self, model):
            from models import RiverReading, HazardAlert
            if model is RiverReading:
                return MockHazardQuery([MockRiverReadingRow()])
            if model is HazardAlert:
                return MockHazardQuery([MockHazardAlertRow()])
            return MockHazardQuery([])

    loaded_readings = realdata.load_river_readings_index(MockHazardDB())
    loaded_alerts = realdata.load_hazard_alerts_index(MockHazardDB())
    check("load_river_readings_index: returns plain dicts", loaded_readings == [
        {"name": "Test River", "lat": 16.7, "lon": 74.2, "value": 123.4, "datatype_code": "MSD", "observed_at": now}
    ], f"got {loaded_readings}")
    check("load_hazard_alerts_index: returns plain dicts", loaded_alerts[0]["districts"] == "Kolhapur" and loaded_alerts[0]["event"] == "Heavy Rain")
    check("load_river_readings_index: None db returns []", realdata.load_river_readings_index(None) == [])
    check("load_hazard_alerts_index: None db returns []", realdata.load_hazard_alerts_index(None) == [])


def test_jjm_scheme_index_and_catchment_evidence():
    class MockSchemeRow:
        def __init__(self, gazetteer_id, scheme_id, status, est, spent):
            self.gazetteer_id = gazetteer_id
            self.scheme_id = scheme_id
            self.scheme_name = f"Scheme {scheme_id}"
            self.scheme_type = "PWS"
            self.scheme_category = "Single village scheme"
            self.work_order_date = "10/04/2008"
            self.estimated_cost_lakh = est
            self.reported_expenditure_lakh = spent
            self.status = status

    class MockSchemeQuery:
        def __init__(self, items):
            self.items = items
        def all(self):
            return self.items

    class MockSchemeDB:
        def query(self, model):
            return MockSchemeQuery([
                MockSchemeRow(101, "S1", "Completed", 10.0, 10.0),
                MockSchemeRow(101, "S2", "Ongoing", 20.0, 5.0),
                MockSchemeRow(202, "S3", "Not Started", 8.0, 0.0),
            ])

    index = realdata.load_jjm_scheme_index(MockSchemeDB())
    check("load_jjm_scheme_index: groups multiple schemes under one village", len(index[101]) == 2)
    check("load_jjm_scheme_index: None db returns {}", realdata.load_jjm_scheme_index(None) == {})

    ev, text = realdata.jjm_scheme_catchment_evidence([], {})
    check("jjm_scheme_catchment_evidence: empty index returns (None, None)", ev is None and text is None)

    villages = [{"gazetteer_id": 101}, {"gazetteer_id": 999}]
    ev2, text2 = realdata.jjm_scheme_catchment_evidence(villages, index)
    check("jjm_scheme_catchment_evidence: finds both real schemes for the matched village", ev2["schemes_found"] == 2)
    check("jjm_scheme_catchment_evidence: counts the ongoing one as undelivered", ev2["undelivered_schemes"] == 1)
    check(
        "jjm_scheme_catchment_evidence: unspent estimate is real cost minus real expenditure",
        math.isclose(ev2["unspent_estimate_lakh"], 15.0),
        f"{ev2['unspent_estimate_lakh']}",
    )
    check("jjm_scheme_catchment_evidence: sentence names the unspent amount", "unspent" in text2)

    all_done = realdata.jjm_scheme_catchment_evidence([{"gazetteer_id": 555}], {555: [
        {"scheme_id": "S4", "status": "Completed", "estimated_cost_lakh": 5.0, "reported_expenditure_lakh": 5.0}
    ]})
    check("jjm_scheme_catchment_evidence: an all-completed catchment reports no unspent money", "completed" in all_done[1])


def test_investment_only_counts_road_reports_as_demand():
    """
    Real bug, found live 2026-09-15: `works` in build_village_investment is
    PMGSY road works exclusively, but `nearby_reports` counted complaints of
    every category -- so a village with only water/health complaints near an
    undelivered road work was misclassified as `funded_undelivered`, implying
    those complaints were about the road. Fixed in investment.py:163-170.
    """
    from models import CitizenRequest, GovernmentProject, Gazetteer, VillagePanchayatFinance
    from intelligence.investment import build_village_investment

    class MockQuery:
        def __init__(self, items):
            self.items = items
        def filter(self, *a, **k):
            return self
        def order_by(self, *a, **k):
            return self
        def all(self):
            return self.items

    class MockWork:
        matched_gazetteer_id = 1
        work_status = "In Progress"
        sanctioned_cost_lakh = 50.0
        sanctioned_year = 2020

    class MockReport:
        def __init__(self, category):
            self.latitude = 16.7
            self.longitude = 74.2
            self.issue_category = category

    class MockVillage:
        id = 1
        name = "Testpur"
        district = "Kolhapur"
        latitude = 16.7
        longitude = 74.2

    class MockDB:
        def query(self, model):
            if model is GovernmentProject:
                return MockQuery([MockWork()])
            if model is CitizenRequest:
                return MockQuery([MockReport("water"), MockReport("health")])
            if model is Gazetteer:
                return MockQuery([MockVillage()])
            if model is VillagePanchayatFinance:
                return MockQuery([])
            return MockQuery([])

    rows, _ = build_village_investment(MockDB())
    check(
        "investment: non-road reports near an undelivered road work do not count as demand",
        rows[0].nearby_reports == 0,
        f"got {rows[0].nearby_reports}",
    )
    check(
        "investment: such a village is not misclassified as funded_undelivered",
        rows[0].classify() != "funded_undelivered",
        f"got {rows[0].classify()}",
    )
    check(
        "investment: it correctly classifies as funded_not_demanded instead",
        rows[0].classify() == "funded_not_demanded",
        f"got {rows[0].classify()}",
    )


def test_school_investment_uses_unspent_grant_and_real_deficiency():
    """
    A school is only "funded but undelivered" when real UDISE+ money is
    unspent AND a real physical deficiency is still on record -- receiving
    a grant at all is not enough, most schools do.
    """
    from models import CitizenRequest, PublicFacility, SchoolCondition
    from intelligence.investment import build_school_investment

    class MockQuery:
        def __init__(self, items):
            self.items = items
        def filter(self, *a, **k):
            return self
        def order_by(self, *a, **k):
            return self
        def all(self):
            return self.items

    class MockFacility:
        def __init__(self, id, category="education"):
            self.id = id
            self.category = category
            self.name = f"School {id}"
            self.village = "Testpur"
            self.district = "Kolhapur"
            self.latitude = 16.7
            self.longitude = 74.2

    class MockCondition:
        def __init__(self, facility_id, grant, spent, major_repair, total):
            self.id = facility_id
            self.facility_id = facility_id
            self.fetch_failed = False
            self.udise_code = str(facility_id)
            self.year_desc = "2024-25"
            self.teachers_regular = None
            self.teachers_contract = None
            self.teachers_part_time = None
            self.classrooms_total = total
            self.classrooms_good = None
            self.classrooms_minor_repair = None
            self.classrooms_major_repair = major_repair
            self.toilet_boys_functional = 1
            self.toilet_girls_functional = 1
            self.drinking_water = True
            self.electricity = True
            self.boundary_wall_status = None
            self.total_grant = grant
            self.total_expenditure = spent
            self.raw_json = None
            self.fetched_at = None

    class MockReport:
        def __init__(self, facility_id):
            self.issue_category = "education"
            self.latitude = 16.7
            self.longitude = 74.2
            self.facility_id = facility_id

    class MockDB:
        def query(self, model):
            if model is PublicFacility:
                return MockQuery([MockFacility(1), MockFacility(2)])
            if model is CitizenRequest:
                return MockQuery([MockReport(1), MockReport(2)])
            if model is SchoolCondition:
                return MockQuery([
                    # School 1: real unspent money AND a real broken classroom.
                    MockCondition(1, grant=25000.0, spent=5000.0, major_repair=3, total=5),
                    # School 2: fully spent grant, nothing broken.
                    MockCondition(2, grant=25000.0, spent=25000.0, major_repair=0, total=5),
                ])
            return MockQuery([])

    rows = build_school_investment(MockDB())
    by_id = {r.facility_id: r for r in rows}
    check(
        "school investment: unspent grant computed as grant minus expenditure",
        by_id[1].unspent_grant_rupees == 20000.0,
        f"got {by_id[1].unspent_grant_rupees}",
    )
    check(
        "school investment: unspent money + real deficiency classifies as funded_undelivered",
        by_id[1].classify() == "funded_undelivered",
        f"got {by_id[1].classify()}",
    )
    check(
        "school investment: a fully-spent grant with nothing broken is not funded_undelivered",
        by_id[2].classify() != "funded_undelivered",
        f"got {by_id[2].classify()}",
    )


def test_budget_optimizer_knapsack_is_exact_not_greedy():
    """
    A case a greedy (highest-value-first) approach gets wrong but exact 0/1
    knapsack gets right: one big item that alone looks attractive, versus
    two smaller items that together beat it within the same budget.
    """
    from intelligence.budget_optimizer import OptimizerCandidate, allocate

    # priority_score is deliberately lopsided relative to population, so
    # switching the value dimension picks a genuinely different set --
    # summed priority favours "big" alone (200 > 30+25), summed population
    # favours the two smaller items together (13,000 > 10,000).
    big = OptimizerCandidate(
        asset_id=1, name="Big Road", asset_type="road", village="V1", district="Kolhapur",
        cost_lakh=100.0, population_affected=10_000, priority_score=200.0,
    )
    small_a = OptimizerCandidate(
        asset_id=2, name="Small Water A", asset_type="water", village="V2", district="Kolhapur",
        cost_lakh=60.0, population_affected=7_000, priority_score=30.0,
    )
    small_b = OptimizerCandidate(
        asset_id=3, name="Small Water B", asset_type="water", village="V3", district="Kolhapur",
        cost_lakh=40.0, population_affected=6_000, priority_score=25.0,
    )

    result = allocate([big, small_a, small_b], budget_lakh=100.0, value="population")
    chosen_ids = {c.asset_id for c in result["chosen"]}
    check(
        "knapsack picks the two smaller items (13,000 reached) over the one big item (10,000)",
        chosen_ids == {2, 3},
        f"got {chosen_ids}, population_reached={result['population_reached']}",
    )
    check("knapsack never exceeds the budget", result["total_cost_lakh"] <= 100.0)
    check(
        "knapsack reports the real remaining budget",
        math.isclose(result["remaining_budget_lakh"], 100.0 - result["total_cost_lakh"]),
    )

    priority_result = allocate([big, small_a, small_b], budget_lakh=100.0, value="priority")
    check(
        "switching value dimension to priority can change what's chosen",
        {c.asset_id for c in priority_result["chosen"]} == {1},
        f"got {[c.asset_id for c in priority_result['chosen']]}",
    )

    empty = allocate([], budget_lakh=50.0, value="population")
    check("an empty candidate list allocates nothing, not an error", empty["chosen"] == [] and empty["total_cost_lakh"] == 0)


def test_budget_optimizer_excludes_assets_with_no_real_cost():
    """
    Health/education and any road/water asset without a real recorded cost
    must never enter the optimizer -- no guessed cost, ever.
    """
    import json
    from models import Asset
    from intelligence.budget_optimizer import load_candidates

    class MockAsset:
        def __init__(self, id, asset_type, evidence, priority_score=50.0):
            self.id = id
            self.name = f"Asset {id}"
            self.asset_type = asset_type
            self.village = "Testpur"
            self.district = "Kolhapur"
            self.priority_score = priority_score
            self.evidence = json.dumps(evidence)

    class MockQuery:
        def __init__(self, items):
            self.items = items
        def filter(self, *a, **k):
            return self
        def all(self):
            return self.items

    class MockDB:
        def query(self, model):
            assert model is Asset
            return MockQuery([
                MockAsset(1, "road", {"infra_deficit": {"undelivered_sanctioned_cost_lakh": 50.0}, "population": {"people_affected": 1000}}),
                MockAsset(2, "road", {"infra_deficit": {}, "population": {"people_affected": 2000}}),
                MockAsset(3, "water", {"jjm_schemes": {"undelivered_schemes": 1, "unspent_estimate_lakh": 20.0}, "population": {"people_affected": 500}}),
                MockAsset(4, "water", {"jjm_schemes": {"undelivered_schemes": 0, "unspent_estimate_lakh": 0.0}, "population": {"people_affected": 500}}),
            ])

    candidates = load_candidates(MockDB())
    ids = {c.asset_id for c in candidates}
    check(
        "only the road and water assets with a real positive cost are included",
        ids == {1, 3},
        f"got {ids}",
    )


def test_gpdp_district_evidence():
    # 1. Edge cases: missing district or empty index
    row, text = realdata.gpdp_district_evidence("Solapur", {})
    check("gpdp district evidence: empty index returns (None, None)", row is None and text is None)

    sample_index = {
        "kolhapur": {
            "id": 1,
            "district": "Kolhapur",
            "district_code": 438,
            "state_code": 27,
            "plan_year": "2026-27",
            "total_panchayats": 1025,
            "panchayats_with_plan": 1025,
            "approved_activities": 64418,
            "gram_sabhas_conducted": 1025,
            "estimated_outlay_lakh": 34082.32,
            "popular_activities_json": "[]",
            "underpicked_activities_json": "[]",
            "recent_activities_json": "[]",
            "data_as_of": "14 Sep 2026 21:09",
            "fetched_at": None,
        }
    }
    row_miss, text_miss = realdata.gpdp_district_evidence("Nashik", sample_index)
    check("gpdp district evidence: missing district returns (None, None)", row_miss is None and text_miss is None)

    # 2. Case-insensitive lookup and sentence formatting
    row_k, text_k = realdata.gpdp_district_evidence("KOLHAPUR", sample_index)
    check("gpdp district evidence: returns row dict", row_k is not None and row_k["district_code"] == 438)
    expected_sentence = "Kolhapur district, FY 2026-27: 1,025 panchayats, all with a registered plan; 64,418 approved activities; ₹34,082.32 lakh estimated outlay (data as of 14 Sep 2026 21:09)."
    check("gpdp district evidence: sentence format matches specification", text_k == expected_sentence)

    # 3. Partial panchayat plan registered
    partial_index = {
        "nashik": {
            "id": 2,
            "district": "Nashik",
            "district_code": 443,
            "state_code": 27,
            "plan_year": "2025-26",
            "total_panchayats": 1375,
            "panchayats_with_plan": 1374,
            "approved_activities": 89515,
            "gram_sabhas_conducted": 1375,
            "estimated_outlay_lakh": 50094.53,
            "data_as_of": "14 Sep 2026 21:09",
            "fetched_at": None,
        }
    }
    _, text_partial = realdata.gpdp_district_evidence("nashik", partial_index)
    check("gpdp district evidence: partial panchayats format", "1,374 of 1,375 panchayats with a registered plan" in (text_partial or ""))

    # 4. load_gpdp_district_summary_index with mock DB
    class MockSummaryRow:
        def __init__(self, id, district, district_code, plan_year, fetch_failed):
            self.id = id
            self.district = district
            self.district_code = district_code
            self.state_code = 27
            self.plan_year = plan_year
            self.total_panchayats = 1000
            self.panchayats_with_plan = 1000
            self.approved_activities = 50000
            self.gram_sabhas_conducted = 1000
            self.estimated_outlay_lakh = 25000.0
            self.popular_activities_json = None
            self.underpicked_activities_json = None
            self.recent_activities_json = None
            self.data_as_of = "14 Sep 2026 21:09"
            self.fetch_failed = fetch_failed
            self.fetched_at = None

    class MockSummaryQuery:
        def filter(self, *args, **kwargs):
            return self
        def order_by(self, *args, **kwargs):
            return self
        def all(self):
            return [
                MockSummaryRow(1, "Kolhapur", 438, "2025-26", False),
                MockSummaryRow(2, "Kolhapur", 438, "2026-27", False),
            ]

    class MockSummaryDB:
        def query(self, model):
            return MockSummaryQuery()

    loaded = realdata.load_gpdp_district_summary_index(MockSummaryDB())
    check("load_gpdp_district_summary_index: loads district", "kolhapur" in loaded)
    check("load_gpdp_district_summary_index: uses latest plan year", loaded["kolhapur"]["plan_year"] == "2026-27")


def test_village_finance_evidence():
    # 1. Missing gazetteer_id or empty index
    row, text = realdata.village_finance_evidence(9999, {})
    check("village finance evidence: empty index returns (None, None)", row is None and text is None)

    sample_index = {
        504: {
            "id": 1,
            "gazetteer_id": 504,
            "village_name": "Ite",
            "district": "Kolhapur",
            "fin_year": "2025-2026",
            "scheme_code": "3287",
            "untied_opening_balance": 286897.0,
            "untied_receipts": 64059.0,
            "untied_payments": 32030.0,
            "untied_closing_balance": 318926.0,
            "tied_opening_balance": 228157.0,
            "tied_receipts": 112086.0,
            "tied_payments": 10000.0,
            "tied_closing_balance": 330243.0,
            "fetched_at": None,
        }
    }
    row_miss, text_miss = realdata.village_finance_evidence(999, sample_index)
    check("village finance evidence: missing id returns (None, None)", row_miss is None and text_miss is None)

    # 2. Formatted sentence
    row_ok, text_ok = realdata.village_finance_evidence(504, sample_index)
    check("village finance evidence: returns row dict", row_ok is not None and row_ok["village_name"] == "Ite")
    expected_sentence = "FY 2025-2026: untied grant ₹64,059 received, ₹32,030 spent (50% utilised); tied grant ₹112,086 received, ₹10,000 spent."
    check("village finance evidence: sentence format matches specification", text_ok == expected_sentence)

    # 3. Mock DB test for load_village_finance_index
    class MockFinanceRow:
        def __init__(self, id, gaz_id, fin_year):
            self.id = id
            self.gazetteer_id = gaz_id
            self.village_name = "Test Village"
            self.district = "Kolhapur"
            self.fin_year = fin_year
            self.scheme_code = "3287"
            self.untied_opening_balance = 100000.0
            self.untied_receipts = 50000.0
            self.untied_payments = 25000.0
            self.untied_closing_balance = 125000.0
            self.tied_opening_balance = 100000.0
            self.tied_receipts = 50000.0
            self.tied_payments = 25000.0
            self.tied_closing_balance = 125000.0
            self.fetched_at = None

    class MockFinanceQuery:
        def filter(self, *args, **kwargs):
            return self
        def order_by(self, *args, **kwargs):
            return self
        def all(self):
            return [
                MockFinanceRow(1, 101, "2024-2025"),
                MockFinanceRow(2, 101, "2025-2026"),
            ]

    class MockFinanceDB:
        def query(self, model):
            return MockFinanceQuery()

    loaded = realdata.load_village_finance_index(MockFinanceDB())
    check("load_village_finance_index: loads matched gazetteer id", 101 in loaded)
    check("load_village_finance_index: uses latest fin_year", loaded[101]["fin_year"] == "2025-2026")


def test_village_mgnrega_evidence():
    # 1. Missing gazetteer_id or empty index
    row, text = realdata.village_mgnrega_evidence(9999, {})
    check("village mgnrega evidence: empty index returns (None, None)", row is None and text is None)

    sample_index = {
        46: {
            "id": 1,
            "gazetteer_id": 46,
            "village_name": "Aamashi",
            "district": "Kolhapur",
            "block": "KARVIR",
            "fin_year": "2026-2027",
            "total_expenditure_lakh": 5.37,
            "wages_lakh": 1.24,
            "material_lakh": 4.12,
            "wages_percent": 23.2,
            "material_percent": 76.8,
            "fetched_at": None,
        }
    }
    row_miss, text_miss = realdata.village_mgnrega_evidence(999, sample_index)
    check("village mgnrega evidence: missing id returns (None, None)", row_miss is None and text_miss is None)

    # 2. Formatted sentence
    row_ok, text_ok = realdata.village_mgnrega_evidence(46, sample_index)
    check("village mgnrega evidence: returns row dict", row_ok is not None and row_ok["village_name"] == "Aamashi")
    expected_sentence = "FY 2026-2027: MGNREGA expenditure ₹5.37 lakh (wages ₹1.24 lakh [23%], material ₹4.12 lakh [77%])."
    check("village mgnrega evidence: sentence format matches specification", text_ok == expected_sentence)

    # 3. Mock DB test for load_village_mgnrega_index
    class MockMgnregaRow:
        def __init__(self, id, gaz_id, fin_year):
            self.id = id
            self.gazetteer_id = gaz_id
            self.village_name = "Test Village"
            self.district = "Kolhapur"
            self.block = "KARVIR"
            self.fin_year = fin_year
            self.total_expenditure_lakh = 5.0
            self.wages_lakh = 3.0
            self.material_lakh = 2.0
            self.wages_percent = 60.0
            self.material_percent = 40.0
            self.fetched_at = None

    class MockMgnregaQuery:
        def filter(self, *args, **kwargs):
            return self
        def order_by(self, *args, **kwargs):
            return self
        def all(self):
            return [
                MockMgnregaRow(1, 101, "2025-2026"),
                MockMgnregaRow(2, 101, "2026-2027"),
            ]

    class MockMgnregaDB:
        def query(self, model):
            return MockMgnregaQuery()

    loaded = realdata.load_village_mgnrega_index(MockMgnregaDB())
    check("load_village_mgnrega_index: loads matched gazetteer id", 101 in loaded)
    check("load_village_mgnrega_index: uses latest fin_year", loaded[101]["fin_year"] == "2026-2027")


def test_school_condition_deficit() -> None:
    # 1. No record for this facility -> caller must fall back, never assume 0.
    value, ev = realdata.school_condition_deficit(999, {})
    check("school condition deficit: no record returns (None, {})", value is None and ev == {})

    # 2. Real signals present -> worst one wins, same pattern as road/water.
    index = {
        42: {
            "classrooms_total": 5, "classrooms_major_repair": 2,
            "electricity": True, "drinking_water": True,
            "toilet_boys_functional": 1, "toilet_girls_functional": 1,
            "year_desc": "2024-25",
        },
    }
    value, ev = realdata.school_condition_deficit(42, index)
    check("school condition deficit: major-repair share computed", math.isclose(value, 0.4), f"got {value}")
    check("school condition deficit: evidence names the source", ev.get("source") == "udise_2024_25_this_school")

    # 3. A missing amenity outweighs a small classroom-repair share.
    index[43] = {
        "classrooms_total": 10, "classrooms_major_repair": 1,
        "electricity": False, "drinking_water": True,
        "toilet_boys_functional": 1, "toilet_girls_functional": 1,
    }
    value, ev = realdata.school_condition_deficit(43, index)
    check("school condition deficit: no electricity saturates to 1.0", value == 1.0, f"got {value}")
    check("school condition deficit: no_electricity flagged", ev.get("no_electricity") is True)

    # 4. Nothing wrong recorded -> a real, honest zero, not a missing record.
    index[44] = {
        "classrooms_total": 4, "classrooms_major_repair": 0,
        "electricity": True, "drinking_water": True,
        "toilet_boys_functional": 1, "toilet_girls_functional": 1,
    }
    value, ev = realdata.school_condition_deficit(44, index)
    check("school condition deficit: no problems recorded is a real 0.0", value == 0.0)


def test_udise_overrides_census_for_a_specific_school() -> None:
    """
    A specific school's own UDISE+ condition must take over from the
    village-wide Census signal when we have it -- and must NOT apply to a
    school we have no UDISE+ record for, which must keep the village signal.
    """
    import json

    gazetteer = [
        {"id": 1, "name": "Girgaon", "district": "Kolhapur", "block": "Karvir", "population": 4000, "lat": 16.700, "lon": 74.200},
    ]
    # Census says this village's schools are fine (no middle/secondary gap),
    # so the village-wide signal alone would report 0 deficit here.
    amenity_index = [{
        "gazetteer_id": 1, "name": "Girgaon", "district": "Kolhapur", "block": "Karvir",
        "population": 4000, "lat": 16.700, "lon": 74.200, "has_real_data": True,
        "school_middle": 1, "school_secondary": 1,
    }]
    facilities_by_category = {
        "education": [
            {"id": 10, "name": "Z.P. Primary School", "category": "education", "lat": 16.700, "lon": 74.200, "source": "udise", "external_id": "U10"},
            {"id": 11, "name": "No-Data School", "category": "education", "lat": 16.700, "lon": 74.200, "source": "udise", "external_id": "U11"},
        ],
    }
    school_condition_index = {
        10: {
            "classrooms_total": 4, "classrooms_major_repair": 3,
            "electricity": True, "drinking_water": True,
            "toilet_boys_functional": 1, "toilet_girls_functional": 1,
        },
    }
    r1 = [{
        "id": 1, "issue_category": "education", "facility_id": 10, "village": "Girgaon",
        "district": "Kolhapur", "block": "Karvir", "latitude": 16.700, "longitude": 74.200,
        "raw_text": "Roof leaking", "severity": "high", "confidence": 0.9,
    }]
    r2 = [{
        "id": 2, "issue_category": "education", "facility_id": 11, "village": "Girgaon",
        "district": "Kolhapur", "block": "Karvir", "latitude": 16.700, "longitude": 74.200,
        "raw_text": "Roof leaking", "severity": "high", "confidence": 0.9,
    }]
    assets_with_udise = _build_assets(
        r1, facilities_by_category=facilities_by_category, works_by_village={},
        gazetteer=gazetteer, amenity_index=amenity_index, works_index=[], gw_stations=[],
        school_condition_index=school_condition_index,
    )
    ev_with = json.loads(assets_with_udise[0].evidence)
    check(
        "school with real UDISE+ data: infra_deficit reflects its own major-repair share",
        math.isclose(ev_with["infra_deficit"]["share_classrooms_major_repair"], 0.75),
        f"got {ev_with['infra_deficit']}",
    )
    check(
        "school with real UDISE+ data: evidence names UDISE as the source, not Census",
        ev_with["infra_deficit"].get("source") == "udise_2024_25_this_school",
    )

    assets_without_udise = _build_assets(
        r2, facilities_by_category=facilities_by_category, works_by_village={},
        gazetteer=gazetteer, amenity_index=amenity_index, works_index=[], gw_stations=[],
        school_condition_index=school_condition_index,
    )
    ev_without = json.loads(assets_without_udise[0].evidence)
    check(
        "school with no UDISE+ record: falls back to the village-wide Census signal",
        "share_classrooms_major_repair" not in ev_without["infra_deficit"],
        f"got {ev_without['infra_deficit']}",
    )


def test_amenity_lookup_uses_the_categorys_own_catchment_radius() -> None:
    """
    Real bug, found live 2026-09-15: infra_deficit/vulnerability/equity all
    looked up nearby villages within a flat 5km, regardless of category --
    even though population_in_catchment already correctly used each
    category's own, wider radius (health: 8km). A health asset with the
    nearest Census-bearing village at 6km was counting that village's
    people for "population affected" while treating it as invisible for
    every real-data term, silently falling through to proxies. Verified
    against the real database: 13 of 177 health assets (7%, vs 0-1%
    elsewhere) hit this before the fix -- health has the widest catchment,
    so the widest blind spot.
    """
    import json

    # 1 degree of latitude is ~111km; 0.054 degrees north is ~6km -- inside
    # health's 8km catchment, outside water's 3km, and outside the old flat
    # 5km amenity radius that applied to every category alike.
    village = {
        "id": 1, "name": "Amenity Village", "district": "Kolhapur", "block": "Karvir",
        "population": 4000, "lat": 16.700, "lon": 74.200,
    }
    gazetteer = [village]
    amenity_index = [{
        "gazetteer_id": 1, "name": "Amenity Village", "district": "Kolhapur", "block": "Karvir",
        "population": 4000, "lat": 16.700, "lon": 74.200, "has_real_data": True,
        "school_middle": 0, "school_secondary": 0,
    }]
    far_lat, far_lon = 16.700 + 6.0 / 111.0, 74.200

    def report(rid, category):
        return {
            "id": rid, "issue_category": category, "village": "Amenity Village",
            "district": "Kolhapur", "block": "Karvir", "latitude": far_lat, "longitude": far_lon,
            "raw_text": "problem here", "severity": "medium", "confidence": 0.9,
        }

    health_assets = _build_assets(
        [report(1, "health")], facilities_by_category={}, works_by_village={},
        gazetteer=gazetteer, amenity_index=amenity_index, works_index=[], gw_stations=[],
    )
    ev_health = json.loads(health_assets[0].evidence)
    check(
        "health asset (8km catchment): 6km-away Census village is now found",
        bool(ev_health.get("infra_deficit")) or ev_health.get("data_basis", {}).get("infra_deficit") != "proxy_reported_severity",
        f"got {ev_health.get('data_basis')}",
    )

    water_assets = _build_assets(
        [report(2, "water")], facilities_by_category={}, works_by_village={},
        gazetteer=gazetteer, amenity_index=amenity_index, works_index=[], gw_stations=[],
    )
    ev_water = json.loads(water_assets[0].evidence)
    check(
        "water asset (3km catchment): the same 6km-away village correctly stays out of range",
        ev_water.get("data_basis", {}).get("infra_deficit") == "proxy_reported_severity",
        f"got {ev_water.get('data_basis')}",
    )


def test_village_investment_unspent_grant() -> None:
    """
    intelligence/investment.py had zero test coverage before this -- the
    module's own docstring says it was "complete and correct" only because
    someone checked it by hand. This covers the one thing added to it: real
    PRIASoft money, independent of the module's existing PMGSY-roads-only
    fields.
    """
    from intelligence.investment import VillageInvestment

    no_finance = VillageInvestment(
        gazetteer_id=1, village="Girgaon", district="Kolhapur",
        latitude=16.7, longitude=74.2,
    )
    check(
        "unspent_grant_rupees: no PRIASoft record is None, never a guessed 0",
        no_finance.unspent_grant_rupees is None,
    )

    with_finance = VillageInvestment(
        gazetteer_id=2, village="Jadhewadi", district="Kolhapur",
        latitude=16.7, longitude=74.2,
        finance={
            "fin_year": "2025-2026",
            "untied_receipts": 327509.15, "untied_payments": 227355.0,
            "tied_receipts": 273960.0, "tied_payments": 340234.0,
        },
    )
    # untied unspent: 327509.15 - 227355.0 = 100154.15
    # tied unspent (overspent, real and allowed to go negative): 273960 - 340234 = -66274.0
    check(
        "unspent_grant_rupees: real untied+tied balance, can go negative",
        math.isclose(with_finance.unspent_grant_rupees, 33880.15),
        f"got {with_finance.unspent_grant_rupees}",
    )


def test_burst_ratio() -> None:
    from intelligence.recompute import burst_ratio
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Under DBSCAN_MIN_SAMPLES (2 distinct reporters) -> 0.0
    r_two = [
        {"raw_text": "Road broken A", "created_at": now - timedelta(hours=1)},
        {"raw_text": "Road broken B", "created_at": now - timedelta(hours=2)},
    ]
    check("burst_ratio: fewer than 3 distinct reporters returns 0.0", burst_ratio(r_two, now=now) == 0.0)

    # 2. Bot duplicate text: 10 reports with identical text -> only 1 distinct reporter -> 0.0
    r_bot = [
        {"raw_text": "Spam bot message", "created_at": now - timedelta(hours=i)}
        for i in range(10)
    ]
    check("burst_ratio: identical bot text dedupes to 1 reporter and returns 0.0", burst_ratio(r_bot, now=now) == 0.0)

    # 3. Steady/uniform arrivals: 10 reports spread across 720 hours (1 month) with 1 in the 72h window
    r_steady = [
        {"raw_text": "Report recent", "created_at": now - timedelta(hours=36)}
    ] + [
        {"raw_text": f"Report hist {i}", "created_at": now - timedelta(hours=100 + i * 77.5)}
        for i in range(9)
    ]
    # first report is at now - (100 + 8*77.5) = now - 720h. lifetime = 720h.
    # recent (last 72h) has 1 report. recent_rate = 1 / 72.
    # baseline_rate = 10 / 720 = 1 / 72. ratio = 1.0
    check("burst_ratio: steady arrival rate returns 1.0", math.isclose(burst_ratio(r_steady, now=now), 1.0, rel_tol=0.01))

    # 4. Sudden burst: 10 reports, 7 of which arrived in the last 10 hours, while the cluster spans 720 hours
    r_burst = [
        {"raw_text": f"Old report {i}", "created_at": now - timedelta(hours=700 + i * 10)}
        for i in range(3)
    ] + [
        {"raw_text": f"Burst report {i}", "created_at": now - timedelta(hours=i + 1)}
        for i in range(7)
    ]
    ratio = burst_ratio(r_burst, now=now)
    check("burst_ratio: sudden recent surge returns ratio >> 1.0", ratio > 5.0, f"ratio was {ratio}")


def test_velocity_term() -> None:
    from intelligence.scoring import velocity_term

    check("velocity_term: ratio <= 1.0 returns 0.0", velocity_term(0.5) == 0.0)
    check("velocity_term: ratio == 1.0 returns 0.0", velocity_term(1.0) == 0.0)

    v_mid = velocity_term(5.0)
    check("velocity_term: ratio 5.0 returns fractional velocity in (0, 1)", 0.0 < v_mid < 1.0)

    v_ceil = velocity_term(config.VELOCITY_RATIO_CEILING)
    check("velocity_term: ratio at ceiling returns 1.0", math.isclose(v_ceil, 1.0))

    v_above = velocity_term(100.0)
    check("velocity_term: ratio above ceiling is clamped to 1.0", v_above == 1.0)


def test_record_freshness() -> None:
    from intelligence.scoring import record_freshness

    check("record_freshness: None (unknown age) returns 1.0", record_freshness(None) == 1.0)
    check("record_freshness: 0 years (current) returns 1.0", record_freshness(0.0) == 1.0)
    check(
        "record_freshness: 10 years (one half-life) returns 0.5",
        math.isclose(record_freshness(10.0), 0.5),
    )
    check(
        "record_freshness: 20 years (two half-lives) returns 0.25",
        math.isclose(record_freshness(20.0), 0.25),
    )
    check(
        "record_freshness: Census 2011 (15 years) returns ~0.3536",
        math.isclose(record_freshness(15.0), 0.5 ** 1.5, rel_tol=0.001),
    )


def test_vintage_years_resolution() -> None:
    from intelligence.recompute import resolve_infra_vintage_years, resolve_vulnerability_vintage_years

    # 1. UDISE+ school condition
    udise_ev = {"source": "udise_2024_25_this_school", "year": "2024-25"}
    check("vintage resolution: UDISE+ resolves to 2.0 years in 2026", resolve_infra_vintage_years("education", udise_ev, 2026) == 2.0)

    # 2. JJM tap coverage
    jjm_ev = {"source": "jal_jeevan_mission_current"}
    check("vintage resolution: JJM resolves to 2.0 years in 2026", resolve_infra_vintage_years("water", jjm_ev, 2026) == 2.0)

    # 3. PMGSY road with undelivered works
    pmgsy_ev = {"oldest_undelivered_sanction_year": 2022}
    check("vintage resolution: PMGSY with sanction year resolves to real age", resolve_infra_vintage_years("road", pmgsy_ev, 2026) == 4.0)

    # 4. Census 2011 fallbacks
    check("vintage resolution: road without sanction year falls back to Census 2011 (15y)", resolve_infra_vintage_years("road", {}, 2026) == 15.0)
    check("vintage resolution: water census fallback is Census 2011 (15y)", resolve_infra_vintage_years("water", {"source": "census_2011_may_overstate"}, 2026) == 15.0)
    check("vintage resolution: health falls back to Census 2011 (15y)", resolve_infra_vintage_years("health", {}, 2026) == 15.0)
    check("vintage resolution: education without UDISE falls back to Census 2011 (15y)", resolve_infra_vintage_years("education", {}, 2026) == 15.0)

    # 5. Vulnerability
    vuln_ev = {"period": "census_2011_structural_baseline"}
    check("vintage resolution: vulnerability is Census 2011 (15y)", resolve_vulnerability_vintage_years(vuln_ev, 2026) == 15.0)


def test_stale_record_trust_blend() -> None:
    from intelligence.scoring import urgency_points

    # 1. Fresh record (vintage_years = 0.0) -> trust = 1.0 even under severe burst
    fresh_res = score_cluster(
        **_baseline_kwargs(),
        real_infra_deficit=0.1,
        real_vulnerability=0.2,
        infra_deficit_vintage_years=0.0,
        velocity=1.0,
        high_severity_share=1.0,
    )
    unburst_fresh_res = score_cluster(
        **_baseline_kwargs(),
        real_infra_deficit=0.1,
        real_vulnerability=0.2,
        infra_deficit_vintage_years=0.0,
        velocity=0.0,
        high_severity_share=0.0,
    )
    check(
        "trust blend: fresh record stays trusted (>=0.95) under severe burst",
        fresh_res["data_basis"]["infra_deficit"] == "government_records",
    )
    check(
        "trust blend: fresh record infra value is not discounted",
        fresh_res["breakdown"]["infra_deficit"] == unburst_fresh_res["breakdown"]["infra_deficit"],
    )

    # 2. Stale record (15 years) but NO burst (velocity = 0.0) -> trust = 1.0
    no_burst_res = score_cluster(
        **_baseline_kwargs(),
        real_infra_deficit=0.0,
        infra_deficit_vintage_years=15.0,
        velocity=0.0,
        high_severity_share=1.0,
    )
    check(
        "trust blend: uncontradicted stale record retains government_records basis",
        no_burst_res["data_basis"]["infra_deficit"] == "government_records",
    )
    check(
        "trust blend: uncontradicted stale record infra points stay 0.0",
        no_burst_res["breakdown"]["infra_deficit"] == 0.0,
    )

    # 3. Stale record (15 years) with low severity reports (high_severity_share = 0.0) -> trust = 1.0
    low_sev_res = score_cluster(
        **_baseline_kwargs(),
        real_infra_deficit=0.0,
        infra_deficit_vintage_years=15.0,
        velocity=1.0,
        high_severity_share=0.0,
    )
    check(
        "trust blend: low-severity burst does not discount government record",
        low_sev_res["data_basis"]["infra_deficit"] == "government_records",
    )

    # 4. Stale record (15 years) WITH high-velocity, high-severity burst -> trust discounted, blended
    blended_res = score_cluster(
        **_baseline_kwargs(severities=["high"] * 10),
        real_infra_deficit=0.0,
        infra_deficit_vintage_years=15.0,
        velocity=0.8,
        high_severity_share=1.0,
    )
    check(
        "trust blend: contradicted stale record labels data_basis as blended",
        blended_res["data_basis"]["infra_deficit"].startswith("blended:"),
        f"got {blended_res['data_basis']['infra_deficit']}",
    )
    check(
        "trust blend: contradicted stale record receives positive infra points instead of 0.0",
        blended_res["breakdown"]["infra_deficit"] > 5.0,
        f"got {blended_res['breakdown']['infra_deficit']}",
    )

    # 5. Vulnerability blend behaves identically
    blended_vuln = score_cluster(
        **_baseline_kwargs(),
        real_vulnerability=0.0,
        vulnerability_vintage_years=15.0,
        velocity=0.8,
        high_severity_share=1.0,
    )
    check(
        "trust blend: vulnerability also blends when contradicted",
        blended_vuln["data_basis"]["vulnerability"].startswith("blended:"),
        f"got {blended_vuln['data_basis']['vulnerability']}",
    )
    check(
        "trust blend: vulnerability recovers points under contradiction",
        blended_vuln["breakdown"]["vulnerability"] > 0.0,
    )

    # 6. Guardrail: Urgency points and Gap Score Max Points are untouched
    check("urgency guardrail: urgency_points is unchanged", urgency_points("road") == config.URGENCY_POINTS)
    check("urgency guardrail: URGENCY_POINTS is 3.0", config.URGENCY_POINTS == 3.0)
    check("gap score guardrail: GAP_SCORE_MAX_POINTS is 81.0", config.GAP_SCORE_MAX_POINTS == 81.0)
    # 6. Guardrail: Urgency points and Gap Score Max Points (Feature #7)
    check("urgency guardrail: normal road without emergency is 0.0", urgency_points("road") == 0.0)
    check("urgency guardrail: URGENCY_POINTS is 15.0", config.URGENCY_POINTS == 15.0)
    check("gap score guardrail: GAP_SCORE_MAX_POINTS is 69.0", config.GAP_SCORE_MAX_POINTS == 69.0)


def test_emergency_detection_and_grading() -> None:
    from intelligence.emergency import detect_report_emergency

    # 1. Bridge collapse (EN, HI, MR)
    g, lbl, _ = detect_report_emergency("Bridge over the river collapsed near Bhor", "road")
    check("emergency detection: EN bridge collapsed", g == 1.0 and lbl == "collapse")

    g, lbl, _ = detect_report_emergency("पूल वाहून गेला आहे", "road")
    check("emergency detection: MR bridge washed away", g == 1.0 and lbl == "collapse")

    g, lbl, _ = detect_report_emergency("pul gir gaya near river", "road")
    check("emergency detection: HI latin bridge collapsed", g == 1.0 and lbl == "collapse")

    g, lbl, _ = detect_report_emergency("पूल ढह गया है", "road")
    check("emergency detection: HI devanagari bridge collapsed", g == 1.0 and lbl == "collapse")

    # 2. Building collapse (EN, HI, MR)
    g, lbl, _ = detect_report_emergency("School classroom roof has collapsed and fallen", "education")
    check("emergency detection: EN school roof collapsed", g == 1.0 and lbl == "collapse")

    g, lbl, _ = detect_report_emergency("school ki chhat gir gayi hai", "education")
    check("emergency detection: HI school roof fell down", g == 1.0 and lbl == "collapse")

    g, lbl, _ = detect_report_emergency("दवाखान्याची भिंत पडली आहे", "health")
    check("emergency detection: MR clinic wall collapsed", g == 1.0 and lbl == "collapse")

    # 3. Partial damage (EN, HI, MR)
    g, lbl, _ = detect_report_emergency("Bridge over the river near Bhor is damaged", "road")
    check("emergency detection: EN bridge damaged", math.isclose(g, 2.0 / 3.0) and lbl == "partial")

    g, lbl, _ = detect_report_emergency("पूल खचला आहे", "road")
    check("emergency detection: MR bridge sinking/damaged", math.isclose(g, 2.0 / 3.0) and lbl == "partial")

    g, lbl, _ = detect_report_emergency("hospital building damaged and unsafe", "health")
    check("emergency detection: EN hospital damaged", math.isclose(g, 2.0 / 3.0) and lbl == "partial")

    # 4. Cracks (EN, HI, MR)
    g, lbl, _ = detect_report_emergency("Bridge deck has deep cracks", "road")
    check("emergency detection: EN bridge crack", math.isclose(g, 1.0 / 3.0) and lbl == "crack")

    g, lbl, _ = detect_report_emergency("दीवार में गंभीर दरारें हैं", "education")
    check("emergency detection: HI wall crack", math.isclose(g, 1.0 / 3.0) and lbl == "crack")

    g, lbl, _ = detect_report_emergency("भिंतीला मोठे तडे गेले आहेत", "health")
    check("emergency detection: MR wall crack", math.isclose(g, 1.0 / 3.0) and lbl == "crack")

    # 5. Non-emergencies must return 0.0 (potholes, missing staff, water supply)
    g, lbl, _ = detect_report_emergency("sadak bahut kharab hai near Ajra", "road")
    check("emergency detection: pothole/bad road is 0.0", g == 0.0 and lbl is None)

    g, lbl, _ = detect_report_emergency("bahut bada gaddha hai gaon mein", "road")
    check("emergency detection: gaddha is 0.0", g == 0.0 and lbl is None)

    g, lbl, _ = detect_report_emergency("sadak toot gayi hai", "road")
    check("emergency detection: road without bridge is 0.0", g == 0.0 and lbl is None)

    g, lbl, _ = detect_report_emergency("school mein shikshak nahi hai near Ajra", "education")
    check("emergency detection: missing teacher is 0.0", g == 0.0 and lbl is None)

    g, lbl, _ = detect_report_emergency("doctor nahi hai health center mein", "health")
    check("emergency detection: missing doctor is 0.0", g == 0.0 and lbl is None)

    g, lbl, _ = detect_report_emergency("water supply has been cut off near village", "water")
    check("emergency detection: water supply cut is 0.0", g == 0.0 and lbl is None)

    g, lbl, _ = detect_report_emergency("school ki chhat kharab hai", "education")
    check("emergency detection: ordinary school repair (chhat kharab) is 0.0", g == 0.0 and lbl is None)


def test_emergency_signal_fusion() -> None:
    from intelligence.emergency import evaluate_emergency_signals

    # 1. No emergency claimed -> urgency 0.0 even with burst and hazard alerts
    m_pothole = [{"raw_text": "bada gaddha hai", "severity": "high"}]
    res_none = evaluate_emergency_signals(
        m_pothole, "road", velocity=1.0, hazard_flag=True, hazard_evidence={"sachet_alerts": 2}
    )
    check("signal fusion: no emergency claimed yields urgency 0.0", res_none["urgency"] == 0.0)
    check("signal fusion: no emergency grade is 0.0", res_none["grade"] == 0.0)

    # 2. Single wording alone -> confidence <= 0.50 (none decisive alone)
    m_single = [{"raw_text": "Bridge over the river collapsed", "severity": "high"}]
    res_single = evaluate_emergency_signals(m_single, "road", velocity=0.0, hazard_flag=False)
    check("signal fusion: single wording alone has confidence <= 0.50", res_single["confidence"] == config.EMERGENCY_CONF_WORDING_SINGLE)
    check("signal fusion: single wording urgency scaled by confidence", res_single["urgency"] == round(config.URGENCY_POINTS * 1.0 * config.EMERGENCY_CONF_WORDING_SINGLE, 2))

    # 3. Corroborated wording from 2 distinct reporters -> confidence 0.50
    m_corrob = [
        {"raw_text": "Bridge over the river collapsed near Bhor", "severity": "high"},
        {"raw_text": "Pul toot gaya hai and collapsed", "severity": "high"},
    ]
    res_corrob = evaluate_emergency_signals(m_corrob, "road", velocity=0.0, hazard_flag=False)
    check("signal fusion: corroborated wording confidence is 0.50", res_corrob["confidence"] == config.EMERGENCY_CONF_WORDING_CORROBORATED)

    # 4. Wording + active SACHET alert -> combined confidence (0.50 + 0.20 = 0.70)
    res_w_sachet = evaluate_emergency_signals(
        m_corrob, "road", velocity=0.0, hazard_flag=True, hazard_evidence={"sachet_alerts": 1}
    )
    check("signal fusion: wording + SACHET combines to 0.70", math.isclose(res_w_sachet["confidence"], 0.70, abs_tol=0.01))

    # 5. Full corroboration: wording (0.50) + burst velocity (0.30) + SACHET alert (0.20) -> 1.0
    res_full = evaluate_emergency_signals(
        m_corrob, "road", velocity=1.0, hazard_flag=True, hazard_evidence={"sachet_alerts": 1}
    )
    check("signal fusion: full corroboration saturates to 1.0", res_full["confidence"] == 1.0)
    check("signal fusion: confirmed collapse gets full 15.0 urgency points", res_full["urgency"] == 15.0)

    # 6. Partial damage with full confidence -> 10.0 points (15 * 2/3)
    m_partial = [
        {"raw_text": "Bridge over the river is damaged and cracked", "severity": "high"},
        {"raw_text": "पूल खचला आहे", "severity": "high"},
    ]
    res_part = evaluate_emergency_signals(
        m_partial, "road", velocity=1.0, hazard_flag=True, hazard_evidence={"sachet_alerts": 1}
    )
    check("signal fusion: confirmed partial damage gets 10.0 urgency points", math.isclose(res_part["urgency"], 10.0, abs_tol=0.05))

    # 7. Crack with full confidence -> 5.0 points (15 * 1/3)
    m_crack = [
        {"raw_text": "Bridge deck has severe cracks", "severity": "high"},
        {"raw_text": "पूलाला मोठे तडे गेले आहेत", "severity": "high"},
    ]
    res_crack = evaluate_emergency_signals(
        m_crack, "road", velocity=1.0, hazard_flag=True, hazard_evidence={"sachet_alerts": 1}
    )
    check("signal fusion: confirmed crack gets 5.0 urgency points", math.isclose(res_crack["urgency"], 5.0, abs_tol=0.05))

    # 8. Photo model signal interface (scoped as optional / not implemented when None)
    check("signal fusion: photo damage model default status is not_implemented", res_full["evidence"]["signals"]["photo_damage_model"]["status"] == "not_implemented")


def test_emergency_urgency_scoring_caps() -> None:
    from intelligence.scoring import urgency_points

    # 1. Cap checks
    check("urgency caps: URGENCY_POINTS is exactly 15.0", config.URGENCY_POINTS == 15.0)
    check("urgency caps: GAP_SCORE_MAX_POINTS is exactly 69.0", config.GAP_SCORE_MAX_POINTS == 69.0)

    # 2. Maximum possible theoretical sum lands on 100.0
    total_ceiling = (
        config.GAP_SCORE_MAX_POINTS
        + config.EQUITY_BOOST_POINTS
        + config.STRATEGIC_POINTS
        + config.URGENCY_POINTS
        + config.FEASIBILITY_POINTS
    )
    check("urgency caps: theoretical maximum total equals 100.0", math.isclose(total_ceiling, 100.0))

    # 3. urgency_points function behavior
    check("urgency_points: default / zero grade is 0.0", urgency_points() == 0.0)
    check("urgency_points: string argument (legacy road) without emergency is 0.0", urgency_points("road") == 0.0)
    check("urgency_points: confirmed collapse is 15.0", urgency_points(1.0, 1.0) == 15.0)
    check("urgency_points: confirmed partial damage is 10.0", urgency_points(2.0 / 3.0, 1.0) == 10.0)
    check("urgency_points: confirmed crack is 5.0", urgency_points(1.0 / 3.0, 1.0) == 5.0)
    check("urgency_points: collapse with 0.4 confidence is 6.0", urgency_points(1.0, 0.4) == 6.0)


def main() -> None:
    print("\nP3 Intelligence Engine -- test suite")
    print("-" * 65)
    for test in [
        test_demand_is_capped,
        test_population_is_log_scaled,
        test_infra_deficit_tracks_severity,
        test_vulnerability_favours_small_settlements,
        test_real_vulnerability_no_longer_double_counts_distance,
        test_feasibility_is_continuous_and_blends_road_connectivity,
        test_real_town_distance_and_road_connectivity,
        test_confidence_gate_damps_but_never_zeroes,
        test_breakdown_sums_to_score,
        test_breakdown_has_the_contract_keys,
        test_score_stays_within_scale,
        test_equity_can_flip_the_ranking,
        test_haversine_is_sane,
        test_gate_blocks_distant_pairs,
        test_semantic_distance_separates_meanings,
        test_catchment_population_respects_radius,
        test_budget_points_are_capped,
        test_weights_sum_to_one,
        test_apply_precise_coords,
        test_precise_coords_split_work_groups,
        test_nwdp_groundwater_lookup,
        test_asset_identity_five_rules,
        test_citizen_selected_beats_nearby_pin,
        test_demo_seeded_facility_is_not_labelled_citizen_picked,
        test_asset_breakdown_sums_to_priority_score,
        test_village_score_equals_max_asset_score,
        test_hospital_reports_from_two_villages_one_asset,
        test_candidate_list_sorted_and_capped,
        test_road_pins_clustering_distance,
        test_pmgsy_road_segment_lookup,
        test_road_asset_matches_geosadak_only_with_a_real_pin,
        test_school_condition_index_and_evidence,
        test_water_testing_evidence,
        test_hazard_near,
        test_gpdp_district_evidence,
        test_village_finance_evidence,
        test_village_mgnrega_evidence,
        test_school_condition_deficit,
        test_udise_overrides_census_for_a_specific_school,
        test_village_investment_unspent_grant,
        test_amenity_lookup_uses_the_categorys_own_catchment_radius,
        test_jjm_scheme_index_and_catchment_evidence,
        test_investment_only_counts_road_reports_as_demand,
        test_school_investment_uses_unspent_grant_and_real_deficiency,
        test_budget_optimizer_knapsack_is_exact_not_greedy,
        test_budget_optimizer_excludes_assets_with_no_real_cost,
        test_burst_ratio,
        test_velocity_term,
        test_record_freshness,
        test_vintage_years_resolution,
        test_stale_record_trust_blend,
        test_emergency_detection_and_grading,
        test_emergency_signal_fusion,
        test_emergency_urgency_scoring_caps,
    ]:
        test()

    print("-" * 65)
    total = _passed + len(_failed)
    print(f"  Result: {_passed}/{total} passed, {len(_failed)} failed")
    if _failed:
        for name in _failed:
            print(f"    failed: {name}")
        sys.exit(1)


if __name__ == "__main__":
    main()

