"""
Capability E -- scoring citizen demand against real government investment data.

This is the module the whole research report is pointed at. Its §9 claim is
that no system in India or globally joins citizen-reported demand to
government investment records; §10.2 feature #2 names the three mismatches a
system that did so would surface. This computes them from real data:

    demanded_unfunded    complaints exist, no sanctioned work found nearby
    funded_undelivered   a sanctioned work sits nearby, still not built,
                         and people are still complaining
    funded_not_demanded  a sanctioned work sits nearby with little or no
                         citizen demand recorded

WHAT IS REAL HERE AND WHAT IS NOT
---------------------------------
The investment side is entirely real: `government_project` holds individually
sanctioned PMGSY road works with sanction year, cost and current execution
status, pulled from PMGSY's own public dashboard (REAL_DATA_RESEARCH.md §2.2b).

The demand side is currently SYNTHETIC -- the citizen reports are generated
(seed_synthetic_data.py). So:

  * `demanded_unfunded` and `funded_undelivered` are trustworthy in mechanism
    and their investment half is real, but which villages appear depends on
    where reports were seeded.
  * `funded_not_demanded` is the weakest of the three and must be labelled as
    a mechanism demonstration, never as a finding. "Nobody complained here"
    is only meaningful when the complaints are real; with seeded data it
    mostly reports where the seeder happened not to put anything.

Say this out loud rather than letting a demo imply otherwise.

MATCHING
--------
Works are pinned to villages by name (load_pmgsy_works.py) and villages carry
coordinates, so demand is associated with a work by distance from that
village. Only ~33% of works pin to a village we know -- our gazetteer holds
1,042 places while these districts contain thousands of habitations. Unpinned
works are still real and are reported separately rather than silently dropped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

EARTH_RADIUS_KM = 6371.0

# How near a complaint must be to count as demand for a given work. Roads are
# linear and a work serves a corridor, so this is deliberately generous --
# it matches the road catchment used elsewhere in the engine.
DEMAND_RADIUS_KM = 5.0

# At or below this many nearby reports, an area is treated as "not demanded".
# Not zero: a single stray report should not cancel the signal.
LOW_DEMAND_REPORTS = 1

# Statuses that mean "sanctioned, money attached, not delivered".
UNDELIVERED_STATUSES = {"Not Started", "In Progress", "Agreement Cancelled"}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass
class VillageInvestment:
    """One village's demand-versus-investment picture."""

    gazetteer_id: int
    village: str
    district: str
    latitude: float
    longitude: float

    nearby_reports: int = 0
    works: list = field(default_factory=list)

    @property
    def undelivered_works(self) -> list:
        return [w for w in self.works if w.work_status in UNDELIVERED_STATUSES]

    @property
    def sanctioned_cost_lakh(self) -> float:
        return sum(w.sanctioned_cost_lakh or 0.0 for w in self.undelivered_works)

    @property
    def oldest_undelivered_year(self) -> int | None:
        years = [w.sanctioned_year for w in self.undelivered_works if w.sanctioned_year]
        return min(years) if years else None

    def classify(self) -> str:
        has_demand = self.nearby_reports > LOW_DEMAND_REPORTS
        has_undelivered = bool(self.undelivered_works)

        if has_demand and has_undelivered:
            return "funded_undelivered"
        if has_demand and not self.works:
            return "demanded_unfunded"
        if not has_demand and has_undelivered:
            return "funded_not_demanded"
        return "no_mismatch"


def build_village_investment(db) -> list[VillageInvestment]:
    """
    Joins gazetteer villages, the citizen reports near them, and the
    government works pinned to them.

    Imported inside the function so this module stays importable without the
    backend package on the path (the intelligence package is run from the repo
    root, the backend models live under backend/).
    """
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parent.parent / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))

    from models import CitizenRequest, Gazetteer, GovernmentProject  # noqa: E402

    works_by_village: dict[int, list] = {}
    unpinned = []
    for work in db.query(GovernmentProject).all():
        if work.matched_gazetteer_id is None:
            unpinned.append(work)
            continue
        works_by_village.setdefault(work.matched_gazetteer_id, []).append(work)

    reports = [
        r
        for r in db.query(CitizenRequest).all()
        if r.latitude is not None and r.longitude is not None
    ]

    villages = [
        g
        for g in db.query(Gazetteer).all()
        if g.latitude is not None and g.longitude is not None
    ]

    results = []
    for village in villages:
        works = works_by_village.get(village.id, [])
        # Only villages that either have a work or sit near reports are
        # interesting; skip the rest to keep the output readable.
        nearby = sum(
            1
            for r in reports
            if haversine_km(village.latitude, village.longitude, r.latitude, r.longitude)
            <= DEMAND_RADIUS_KM
        )
        if not works and nearby == 0:
            continue
        results.append(
            VillageInvestment(
                gazetteer_id=village.id,
                village=village.name,
                district=village.district or "",
                latitude=village.latitude,
                longitude=village.longitude,
                nearby_reports=nearby,
                works=works,
            )
        )

    results.sort(key=lambda v: (-v.sanctioned_cost_lakh, -v.nearby_reports))
    return results, unpinned


def summarise(rows: list[VillageInvestment]) -> dict[str, list[VillageInvestment]]:
    buckets: dict[str, list[VillageInvestment]] = {
        "funded_undelivered": [],
        "demanded_unfunded": [],
        "funded_not_demanded": [],
        "no_mismatch": [],
    }
    for row in rows:
        buckets[row.classify()].append(row)
    return buckets
