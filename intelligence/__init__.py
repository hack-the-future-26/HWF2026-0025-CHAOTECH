"""
P3 -- Intelligence Engine.

Turns citizen_request rows into ranked, explainable demand_cluster +
priority_score rows. See WEIGHTS.md for every weight, offset, and documented
simplification.

Public surface:
    recompute(db)                            -- full pass (Steps 1-8, 10)
    recompute_with_budget(db, delta, district) -- what-if re-ranking (Step 9)
    score_cluster(...)                       -- the two-stage formula itself
"""

from .recompute import recompute
from .scoring import score_cluster
from .whatif import recompute_with_budget

__all__ = ["recompute", "recompute_with_budget", "score_cluster"]
