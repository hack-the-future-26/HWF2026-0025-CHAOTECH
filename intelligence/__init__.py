"""
P3 -- Intelligence Engine.

Turns citizen_request rows into ranked, explainable demand_cluster +
priority_score rows. See WEIGHTS.md for every weight, offset, and documented
simplification.

Public surface:
    recompute(db)                            -- full pass (Steps 1-8, 10)
    score_cluster(...)                       -- the two-stage formula itself

Step 9's what-if re-ranking (recompute_with_budget) was removed 2026-09-15,
replaced by intelligence/budget_optimizer.py -- a real 0/1 knapsack
allocation over actual project costs, not a flat re-scoring simulation.
"""

from .recompute import recompute
from .scoring import score_cluster

__all__ = ["recompute", "score_cluster"]
