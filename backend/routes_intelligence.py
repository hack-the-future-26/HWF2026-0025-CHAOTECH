"""
P3 Step 10 -- the recompute trigger P2 exposes.

Kept separate from routes_dashboard.py because this is the only write-side
intelligence endpoint: everything else P4 touches is read-only.
"""

import sys
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

router = APIRouter()


@router.get("/investment-alignment")
def investment_alignment(
    district: str | None = Query(None),
    limit: int = Query(15, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """
    Citizen demand scored against real government investment records
    (§10.2 feature #2 / research report Capability E).

    The engine in intelligence/investment.py was complete and correct but
    nothing in the running system called it. This route is that missing wire:
    it joins gazetteer villages, the citizen reports near them and the PMGSY
    works pinned to them, and returns the three mismatch classes.

        funded_undelivered   money sanctioned, work not built, people still
                             complaining nearby
        demanded_unfunded    complaints exist, no sanctioned work found
        funded_not_demanded  a sanctioned work sits nearby with little demand
                             -- a MECHANISM DEMONSTRATION only while the demand
                             side is synthetic, never a finding

    Read-only. It does not touch scores or clusters.
    """
    from intelligence.investment import build_village_investment, summarise

    rows, unpinned = build_village_investment(db)
    if district:
        rows = [r for r in rows if (r.district or "").lower() == district.lower()]
    buckets = summarise(rows)

    def serialise(v):
        return {
            "gazetteer_id": v.gazetteer_id,
            "village": v.village,
            "district": v.district,
            "latitude": v.latitude,
            "longitude": v.longitude,
            "nearby_reports": v.nearby_reports,
            "total_works": len(v.works),
            "undelivered_works": len(v.undelivered_works),
            "undelivered_cost_lakh": round(v.sanctioned_cost_lakh, 2),
            "oldest_undelivered_year": v.oldest_undelivered_year,
        }

    return {
        "district": district,
        "demand_side": "synthetic -- funded_not_demanded is a mechanism demo, not a finding",
        "counts": {name: len(items) for name, items in buckets.items()},
        "unpinned_works": len(unpinned),
        "funded_undelivered": [serialise(v) for v in buckets["funded_undelivered"][:limit]],
        "demanded_unfunded": [serialise(v) for v in buckets["demanded_unfunded"][:limit]],
        "funded_not_demanded": [serialise(v) for v in buckets["funded_not_demanded"][:limit]],
    }


@router.post("/recompute-scores")
def recompute_scores(db: Session = Depends(get_db)):
    """
    Re-run the full P3 pass: embed, gate, cluster, score, rank.

    Safe to call repeatedly -- it clears the previous clustering and scores
    before writing new ones. Expect this to take tens of seconds on a
    thousand-plus reports: it loads a sentence-transformer and builds a
    distance matrix per category, which is why it is a triggered job rather
    than something that runs inline on every citizen report.
    """
    from intelligence.recompute import recompute

    summary = recompute(db, verbose=False)
    return {"status": "ok", **summary}
