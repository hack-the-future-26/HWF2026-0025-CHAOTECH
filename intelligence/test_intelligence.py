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
    )
    check(
        "even a maximal cluster stays at or below 100",
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


def test_pmgsy_road_segment_asset_matching() -> None:
    import json

    gazetteer = [
        {"id": 1, "name": "Girgaon", "district": "Kolhapur", "block": "Karvir", "population": 4000, "lat": 16.700, "lon": 74.200},
    ]
    fake_segment_named = {
        "id": 101,
        "external_id": "SEG101",
        "district": "Kolhapur",
        "block": "Karvir",
        "drrp_road_code": "VR 18",
        "road_name": "Girgaon Link Road",
        "road_category": "VR",
        "road_owner": "ZP",
        "start_lat": 16.699,
        "start_lon": 74.200,
        "end_lat": 16.701,
        "end_lon": 74.200,
        "points": [[16.699, 74.200], [16.701, 74.200]],
        "point_count": 2,
        "min_lat": 16.699,
        "max_lat": 16.701,
        "min_lon": 74.200,
        "max_lon": 74.200,
    }
    fake_segment_code_only = {
        "id": 102,
        "external_id": "SEG102",
        "district": "Kolhapur",
        "block": "Karvir",
        "drrp_road_code": "VR 99",
        "road_name": None,
        "road_category": "VR",
        "road_owner": "ZP",
        "start_lat": 16.699,
        "start_lon": 74.200,
        "end_lat": 16.701,
        "end_lon": 74.200,
        "points": [[16.699, 74.200], [16.701, 74.200]],
        "point_count": 2,
        "min_lat": 16.699,
        "max_lat": 16.701,
        "min_lon": 74.200,
        "max_lon": 74.200,
    }
    works_by_village = {
        "Girgaon": [{"name": "Girgaon to Phata Road", "external_id": "P1", "status": "completed", "cost_lakh": 25.0, "year": 2022}]
    }

    # 1. Road group within 150m of fake_segment_named -> geosadak_segment with road_name
    r_near = [{
        "id": 1, "issue_category": "road", "village": "Girgaon", "district": "Kolhapur", "block": "Karvir",
        "latitude": 16.7001, "longitude": 74.2001, "raw_text": "Potholes on link road", "severity": "medium", "confidence": 0.9,
    }]
    assets_named = _build_assets(
        r_near,
        facilities_by_category={},
        works_by_village=works_by_village,
        gazetteer=gazetteer,
        amenity_index=[],
        works_index=[],
        gw_stations=[],
        road_segment_index=[fake_segment_named],
    )
    check("matched segment: name_basis is geosadak_segment", assets_named[0].name_basis == "geosadak_segment")
    check("matched segment: uses road_name", assets_named[0].name == "Girgaon Link Road")
    ev_named = json.loads(assets_named[0].evidence)
    check("matched segment: road_geometry in evidence", "road_geometry" in ev_named)
    check("matched segment: segment_id matches", ev_named["road_geometry"]["segment_id"] == 101)
    check("matched segment: points has 2 pairs", len(ev_named["road_geometry"]["points"]) == 2)
    check("matched segment: distance_m < 150", ev_named["road_geometry"]["distance_m"] < 150)

    # 2. Road_name is None -> fallback to drrp_road_code
    assets_code = _build_assets(
        r_near,
        facilities_by_category={},
        works_by_village=works_by_village,
        gazetteer=gazetteer,
        amenity_index=[],
        works_index=[],
        gw_stations=[],
        road_segment_index=[fake_segment_code_only],
    )
    check("code-only segment: name_basis is geosadak_segment", assets_code[0].name_basis == "geosadak_segment")
    check("code-only segment: falls back to drrp_road_code", assets_code[0].name == "VR 99")
    ev_code = json.loads(assets_code[0].evidence)
    check("code-only segment: road_geometry in evidence", "road_geometry" in ev_code)
    check("code-only segment: drrp_road_code in evidence", ev_code["road_geometry"]["drrp_road_code"] == "VR 99")

    # 3. Road group far from any segment (>150m) -> fallback to pmgsy_work
    r_far = [{
        "id": 2, "issue_category": "road", "village": "Girgaon", "district": "Kolhapur", "block": "Karvir",
        "latitude": 16.800, "longitude": 74.300, "raw_text": "Distant road potholes", "severity": "medium", "confidence": 0.9,
    }]
    assets_far_work = _build_assets(
        r_far,
        facilities_by_category={},
        works_by_village=works_by_village,
        gazetteer=gazetteer,
        amenity_index=[],
        works_index=[],
        gw_stations=[],
        road_segment_index=[fake_segment_named],
    )
    check("distant road: falls back to pmgsy_work", assets_far_work[0].name_basis == "pmgsy_work")
    check("distant road: names pmgsy work", assets_far_work[0].name == "Girgaon to Phata Road")
    ev_far_work = json.loads(assets_far_work[0].evidence)
    check("distant road: road_geometry absent in evidence", "road_geometry" not in ev_far_work)

    # 4. Distant road without pmgsy_work -> fallback to unnamed_pin
    assets_far_unnamed = _build_assets(
        r_far,
        facilities_by_category={},
        works_by_village={},
        gazetteer=gazetteer,
        amenity_index=[],
        works_index=[],
        gw_stations=[],
        road_segment_index=[fake_segment_named],
    )
    check("distant road no work: falls back to unnamed_pin", assets_far_unnamed[0].name_basis == "unnamed_pin")
    check("distant road no work: Road near Girgaon", assets_far_unnamed[0].name == "Road near Girgaon")
    ev_far_unnamed = json.loads(assets_far_unnamed[0].evidence)
    check("distant road no work: road_geometry absent", "road_geometry" not in ev_far_unnamed)

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


def main() -> None:
    print("\nP3 Intelligence Engine -- test suite")
    print("-" * 65)
    for test in [
        test_demand_is_capped,
        test_population_is_log_scaled,
        test_infra_deficit_tracks_severity,
        test_vulnerability_favours_small_settlements,
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
        test_pmgsy_road_segment_asset_matching,
        test_school_condition_index_and_evidence,
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
