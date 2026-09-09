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

from intelligence import config
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
