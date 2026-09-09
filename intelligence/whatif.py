"""
P3 Step 9 -- what-if re-ranking.

recompute_with_budget(budget_delta, district) re-scores the existing clusters
as if `district` had received `budget_delta` extra rupees, and returns the
re-sorted list. It does NOT re-cluster: clustering describes what is broken
and does not change because money moved.

Deliberately crude, and labelled as such in WEIGHTS.md: extra budget raises
the `strategic` term for clusters in that district, on the reasoning that
more money makes more things fundable there. This is not a capital-budgeting
optimiser -- research report SS16.3 puts real constrained optimisation
(knapsack under a hard budget cap) at Phase 3+, once trustworthy per-project
cost data exists. Showing a simple, honest re-ranking beats showing a
sophisticated one built on cost numbers nobody has.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "backend"))

from models import CitizenRequest, DemandCluster, Gazetteer  # noqa: E402

from . import config  # noqa: E402
from .population import nearest_hq_distance_km  # noqa: E402
from .scoring import score_cluster  # noqa: E402


def budget_to_points(budget_delta: float) -> float:
    """
    Convert a rupee delta into strategic points, capped.

    The cap matters: without it a large enough number would swamp every other
    term and the ranking would become "whoever got the most money", which is
    the opposite of what this system is for.
    """
    crores = budget_delta / config.RUPEES_PER_CRORE
    points = crores * config.WHATIF_POINTS_PER_CRORE
    return max(-config.WHATIF_MAX_POINTS, min(config.WHATIF_MAX_POINTS, points))


def recompute_with_budget(db, budget_delta: float, district: str) -> list[dict]:
    """
    Re-score every cluster under a hypothetical budget change for one
    district, and return them ranked highest-first.

    Each returned row carries both the new score and the original one, so the
    dashboard can show what moved and by how much.
    """
    extra_points = budget_to_points(budget_delta)

    gazetteer = [
        {
            "name": row.name,
            "admin_level": row.admin_level,
            "population": row.population,
            "lat": row.latitude,
            "lon": row.longitude,
        }
        for row in db.query(Gazetteer).all()
    ]

    severities_by_cluster: dict[int, list[str | None]] = {}
    for row in db.query(CitizenRequest).filter(CitizenRequest.cluster_id.isnot(None)).all():
        severities_by_cluster.setdefault(row.cluster_id, []).append(row.severity)

    results = []
    for cluster in db.query(DemandCluster).all():
        applies = bool(district) and cluster.district == district

        baseline = score_cluster(
            unique_reporters=cluster.unique_reporters or 0,
            population_affected=cluster.population_affected or 0,
            settlement_count=cluster.settlement_count or 0,
            severities=severities_by_cluster.get(cluster.id, []),
            average_confidence=cluster.avg_confidence or 0.0,
            issue_category=cluster.issue_category,
            block=cluster.block,
            distance_to_hq_km=nearest_hq_distance_km(
                cluster.centroid_lat, cluster.centroid_lon, gazetteer
            ),
        )

        adjusted = score_cluster(
            unique_reporters=cluster.unique_reporters or 0,
            population_affected=cluster.population_affected or 0,
            settlement_count=cluster.settlement_count or 0,
            severities=severities_by_cluster.get(cluster.id, []),
            average_confidence=cluster.avg_confidence or 0.0,
            issue_category=cluster.issue_category,
            block=cluster.block,
            distance_to_hq_km=nearest_hq_distance_km(
                cluster.centroid_lat, cluster.centroid_lon, gazetteer
            ),
            extra_strategic_points=extra_points if applies else 0.0,
        )

        results.append(
            {
                "id": cluster.id,
                "issue_category": cluster.issue_category,
                "district": cluster.district,
                "block": cluster.block,
                "centroid_lat": cluster.centroid_lat,
                "centroid_lon": cluster.centroid_lon,
                "report_count": cluster.report_count,
                "unique_reporters": cluster.unique_reporters,
                "population_affected": cluster.population_affected,
                "priority_score": adjusted["priority_score"],
                "baseline_priority_score": baseline["priority_score"],
                "score_delta": round(
                    adjusted["priority_score"] - baseline["priority_score"], 2
                ),
                "affected_by_budget_change": applies,
                "breakdown": adjusted["breakdown"],
            }
        )

    results.sort(key=lambda row: row["priority_score"], reverse=True)
    for rank, row in enumerate(results, start=1):
        row["rank"] = rank
    return results
