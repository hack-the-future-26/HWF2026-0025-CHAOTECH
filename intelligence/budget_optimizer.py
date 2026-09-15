"""
Smart Budget Allocation Optimizer -- BUDGET_ALLOCATION_OPTIMIZER_RESEARCH.md.

Answers a different question from whatif.py's re-ranking simulation:
whatif asks "if this district got more money, how would rankings shift".
This asks "given exactly this much money, which specific real projects
should be funded to reach the most people without going over budget" -- a
0/1 knapsack, not a re-rank.

SCOPE, ON PURPOSE: roads and water only. Health and education have no
honest per-project cost in this database yet (see the research doc's
table). Building this across every category would mean inventing costs
for most of it, which this project has refused to do everywhere else.
Kept as its own module rather than folded into whatif.py -- the two
answer different questions and neither replaces the other.

WHERE THE COST COMES FROM
--------------------------
Not re-derived from raw PMGSY/JJM tables -- reused directly from each
asset's own already-computed, already-real evidence, so this stays
consistent with what the dashboard already shows for that exact asset:

    road:  evidence["infra_deficit"]["undelivered_sanctioned_cost_lakh"]
    water: evidence["jjm_schemes"]["unspent_estimate_lakh"]
           (only counted when undelivered_schemes > 0 -- an all-completed
           catchment has nothing left to fund)

An asset with no real cost recorded is excluded from the optimizer
entirely, not given a guessed cost -- it stays visible in the ordinary
priority ranking, just outside this feature's scope.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# DP works in units of this many rupees-lakh per step. 1.0 = whole-lakh
# precision. Real costs here run from a few lakh to a few hundred, so whole
# lakhs is precise enough without making the DP table larger than it needs
# to be.
COST_PRECISION_LAKH = 1.0


@dataclass
class OptimizerCandidate:
    """One real, already-costed road or water asset."""

    asset_id: int
    name: str
    asset_type: str
    village: str | None
    district: str | None
    cost_lakh: float
    population_affected: int
    priority_score: float

    @property
    def cost_units(self) -> int:
        return max(1, round(self.cost_lakh / COST_PRECISION_LAKH))


def load_candidates(db, district: str | None = None) -> list[OptimizerCandidate]:
    """
    Every road/water asset with a real, positive, already-computed cost.
    Reads straight from the `asset` table's own evidence JSON -- the same
    numbers already shown on that asset's detail panel.
    """
    from models import Asset

    query = db.query(Asset).filter(Asset.asset_type.in_(["road", "water"]))
    candidates = []
    for a in query.all():
        if district and (a.district or "").lower() != district.lower():
            continue
        try:
            evidence = json.loads(a.evidence) if a.evidence else {}
        except (TypeError, ValueError):
            continue

        cost = None
        if a.asset_type == "road":
            cost = (evidence.get("infra_deficit") or {}).get("undelivered_sanctioned_cost_lakh")
        elif a.asset_type == "water":
            jjm = evidence.get("jjm_schemes") or {}
            if (jjm.get("undelivered_schemes") or 0) > 0:
                cost = jjm.get("unspent_estimate_lakh")

        if not cost or cost <= 0:
            continue

        population = (evidence.get("population") or {}).get("people_affected") or 0
        candidates.append(
            OptimizerCandidate(
                asset_id=a.id,
                name=a.name,
                asset_type=a.asset_type,
                village=a.village,
                district=a.district,
                cost_lakh=round(cost, 2),
                population_affected=population,
                priority_score=a.priority_score or 0.0,
            )
        )
    return candidates


def allocate(
    candidates: list[OptimizerCandidate],
    budget_lakh: float,
    value: str = "population",
) -> dict:
    """
    0/1 knapsack: choose the subset of candidates that fits within
    budget_lakh and maximises total value, exactly (not a greedy
    approximation) -- at a few hundred real candidates this is trivially
    small for exact dynamic programming.

    value: "population" maximises real people reached (the research doc's
    default); "priority" maximises summed priority_score instead, for a
    view weighted toward how bad each gap already is, not just headcount.
    """
    if value not in ("population", "priority"):
        raise ValueError(f"unknown value dimension: {value!r}")

    capacity = max(0, round(budget_lakh / COST_PRECISION_LAKH))
    n = len(candidates)

    def item_value(c: OptimizerCandidate) -> float:
        return c.population_affected if value == "population" else c.priority_score

    # Standard 0/1 knapsack DP. dp[b] = best value achievable with budget b,
    # after considering items processed so far; keep[i][b] records whether
    # item i was taken, for backtracking the actual chosen set.
    dp = [0.0] * (capacity + 1)
    keep = [[False] * (capacity + 1) for _ in range(n)]
    for i, c in enumerate(candidates):
        cost = c.cost_units
        v = item_value(c)
        for b in range(capacity, cost - 1, -1):
            alt = dp[b - cost] + v
            if alt > dp[b]:
                dp[b] = alt
                keep[i][b] = True

    chosen: list[OptimizerCandidate] = []
    b = capacity
    for i in range(n - 1, -1, -1):
        if keep[i][b]:
            chosen.append(candidates[i])
            b -= candidates[i].cost_units
    chosen.reverse()

    total_cost = round(sum(c.cost_lakh for c in chosen), 2)
    return {
        "budget_lakh": budget_lakh,
        "value_dimension": value,
        "chosen": chosen,
        "candidates_considered": n,
        "total_cost_lakh": total_cost,
        "remaining_budget_lakh": round(budget_lakh - total_cost, 2),
        "population_reached": sum(c.population_affected for c in chosen),
        "priority_weighted_benefit": (
            round(sum(c.priority_score for c in chosen) / len(chosen), 2) if chosen else 0.0
        ),
    }
