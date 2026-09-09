"""
P3 Step 4 -- population affected.

Sums real Census population (gazetteer.population, loaded by
load_demographic_data.py) for every settlement inside the cluster's catchment
radius, where the radius depends on the issue category: a stranded road cuts
off a wider area than one ward's dry tap.

This is the "who is actually affected" number, and it is deliberately NOT
"the population of the village that complained" -- research report SS17.1
makes that distinction explicit, and it is the term that lets a cluster with
fewer reports outrank a louder one.
"""

from __future__ import annotations

from . import config
from .clustering import haversine_km


def catchment_radius_km(issue_category: str | None) -> float:
    return config.CATCHMENT_RADIUS_KM.get(
        issue_category or "", config.DEFAULT_CATCHMENT_RADIUS_KM
    )


def population_in_catchment(
    centroid_lat: float,
    centroid_lon: float,
    issue_category: str | None,
    gazetteer: list[dict],
) -> int:
    """
    Total Census population within the category's catchment radius.

    Settlements with no population figure contribute nothing rather than a
    guess -- 948 of 1,042 gazetteer rows carry a real Census number, and
    inventing values for the remainder would quietly fabricate the single
    most load-bearing input to the Gap Score.
    """
    radius = catchment_radius_km(issue_category)
    total = 0
    for place in gazetteer:
        population = place.get("population")
        if not population:
            continue
        if place.get("lat") is None or place.get("lon") is None:
            continue
        if haversine_km(centroid_lat, centroid_lon, place["lat"], place["lon"]) <= radius:
            total += int(population)
    return total


def nearest_hq_distance_km(
    centroid_lat: float,
    centroid_lon: float,
    gazetteer: list[dict],
) -> float | None:
    """
    Distance to the nearest taluka headquarters, used by the feasibility term
    in scoring.py as a rough proxy for how reachable a site is.
    """
    distances = [
        haversine_km(centroid_lat, centroid_lon, place["lat"], place["lon"])
        for place in gazetteer
        if place.get("admin_level") == "block"
        and place.get("lat") is not None
        and place.get("lon") is not None
    ]
    return min(distances) if distances else None
