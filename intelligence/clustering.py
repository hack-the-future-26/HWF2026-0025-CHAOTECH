"""
P3 Steps 2-3 -- duplicate/corroboration gating, then DBSCAN clustering.

Step 2 (gating): candidate pairs are restricted to the same issue_category
and within GATE_RADIUS_KM of each other BEFORE any embedding comparison. The
build plan calls this "both cheaper and more correct than comparing
everything to everything" -- cheaper because the matrix stays small, more
correct because two identical sentences about two different districts are not
the same problem.

Step 3 (clustering): DBSCAN over a combined geographic + semantic distance,
run separately per category. DBSCAN is the right family here because the
number of real-world problems is not known in advance (unlike k-means, which
would demand we guess it) and because clusters are of wildly different sizes
-- one failing culvert versus a whole block's water scarcity.
"""

from __future__ import annotations

import math

import numpy as np
from sklearn.cluster import DBSCAN

from . import config
from .embeddings import cosine_similarity_matrix, embed_texts

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def geographic_distance_matrix(coords: list[tuple[float, float]]) -> np.ndarray:
    """Pairwise haversine distances, in km."""
    n = len(coords)
    matrix = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        lat1, lon1 = coords[i]
        for j in range(i + 1, n):
            lat2, lon2 = coords[j]
            d = haversine_km(lat1, lon1, lat2, lon2)
            matrix[i, j] = matrix[j, i] = d
    return matrix


def build_distance_matrix(
    coords: list[tuple[float, float]],
    vectors: np.ndarray,
) -> np.ndarray:
    """
    Combined distance: physical km, plus a semantic penalty for reports that
    do not mean the same thing, plus an effectively-infinite penalty for pairs
    the Step 2 gate rejected.

        distance = geo_km + SEMANTIC_WEIGHT_KM * (1 - cosine_similarity)

    Reports far apart in meaning are pushed beyond eps even when they sit on
    top of each other geographically, so "the school roof leaks" never merges
    into "the borewell is dry" just because both came from one village.
    """
    geo = geographic_distance_matrix(coords)
    semantic = 1.0 - cosine_similarity_matrix(vectors)

    distance = geo + config.SEMANTIC_WEIGHT_KM * semantic

    # Step 2 gate: anything beyond the gate radius is not a candidate pair.
    distance[geo > config.GATE_RADIUS_KM] = config.GATE_PENALTY_KM
    np.fill_diagonal(distance, 0.0)
    return distance


def cluster_reports(reports: list[dict]) -> dict[int, int]:
    """
    Cluster one category's reports.

    Args:
        reports: dicts with at least id, raw_text, latitude, longitude.

    Returns:
        {report_id: cluster_label}. Label -1 means DBSCAN called it noise --
        a lone report with nothing corroborating it. Those deliberately do
        not become clusters: one unverified report is not yet a demand signal
        (research report SS10.4, Evidence Fusion).
    """
    if len(reports) < config.DBSCAN_MIN_SAMPLES:
        return {r["id"]: -1 for r in reports}

    coords = [(r["latitude"], r["longitude"]) for r in reports]
    vectors = embed_texts([r["raw_text"] or "" for r in reports])
    distance = build_distance_matrix(coords, vectors)

    labels = DBSCAN(
        eps=config.DBSCAN_EPS_KM,
        min_samples=config.DBSCAN_MIN_SAMPLES,
        metric="precomputed",
    ).fit_predict(distance)

    return {r["id"]: int(label) for r, label in zip(reports, labels)}


def cluster_all(reports: list[dict]) -> dict[int, tuple[str, int]]:
    """
    Cluster every report, one DBSCAN run per issue category.

    Returns {report_id: (issue_category, local_cluster_label)} for reports
    that landed in a real cluster. Noise points and reports missing a
    category or coordinates are omitted entirely.
    """
    by_category: dict[str, list[dict]] = {}
    for report in reports:
        category = report.get("issue_category")
        if not category:
            continue
        if report.get("latitude") is None or report.get("longitude") is None:
            continue
        by_category.setdefault(category, []).append(report)

    assignments: dict[int, tuple[str, int]] = {}
    for category, group in by_category.items():
        for report_id, label in cluster_reports(group).items():
            if label >= 0:
                assignments[report_id] = (category, label)
    return assignments
