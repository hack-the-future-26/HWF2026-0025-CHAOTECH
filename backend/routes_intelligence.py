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
    from intelligence.investment import (
        build_school_investment,
        build_village_investment,
        summarise,
        summarise_schools,
    )

    rows, unpinned = build_village_investment(db)
    if district:
        rows = [r for r in rows if (r.district or "").lower() == district.lower()]
    buckets = summarise(rows)

    school_rows = build_school_investment(db)
    if district:
        school_rows = [r for r in school_rows if (r.district or "").lower() == district.lower()]
    school_buckets = summarise_schools(school_rows)

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
            # Real PRIASoft receipts/expenditure for this village, whole-panchayat
            # -- not tied to PMGSY roads like the fields above. None when this
            # village has no PRIASoft record, never a guessed 0.
            "finance_fin_year": (v.finance or {}).get("fin_year"),
            "unspent_grant_rupees": v.unspent_grant_rupees,
        }

    def serialise_school(s):
        return {
            "facility_id": s.facility_id,
            "name": s.name,
            "village": s.village,
            "district": s.district,
            "latitude": s.latitude,
            "longitude": s.longitude,
            "nearby_reports": s.nearby_reports,
            "total_grant": s.total_grant,
            "total_expenditure": s.total_expenditure,
            "unspent_grant_rupees": s.unspent_grant_rupees,
            "infra_deficit": s.infra_deficit,
        }

    return {
        "district": district,
        "demand_side": "synthetic -- funded_not_demanded is a mechanism demo, not a finding",
        "counts": {name: len(items) for name, items in buckets.items()},
        "unpinned_works": len(unpinned),
        "funded_undelivered": [serialise(v) for v in buckets["funded_undelivered"][:limit]],
        "demanded_unfunded": [serialise(v) for v in buckets["demanded_unfunded"][:limit]],
        "funded_not_demanded": [serialise(v) for v in buckets["funded_not_demanded"][:limit]],
        # Schools: a structurally different real signal from the road/water
        # rows above (real UDISE+ unspent grant + real recorded deficiency,
        # not a PMGSY work status), kept in its own section rather than
        # merged into the same buckets so the two "funded" definitions are
        # never confused for the same thing.
        "school_mismatches": {
            "counts": {name: len(items) for name, items in school_buckets.items()},
            "funded_undelivered": [serialise_school(s) for s in school_buckets["funded_undelivered"][:limit]],
            "demanded_unfunded": [serialise_school(s) for s in school_buckets["demanded_unfunded"][:limit]],
            "funded_not_demanded": [serialise_school(s) for s in school_buckets["funded_not_demanded"][:limit]],
        },
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
