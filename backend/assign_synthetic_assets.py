"""
Deterministic demo locations for synthetic reports (clearly labelled).

Assigns realistic GPS pins and facility links to seeded synthetic reports
(is_synthetic = 1) so the village and asset priority view has concrete
locations to cluster and display. Real citizen reports (is_synthetic = 0)
are NEVER touched.

Education & Health:
- Education reports snap to a school within 2 km of the report's village centroid.
- Health reports snap to one of the 3 nearest hospitals within 8 km.
- Links facility_id, places pin within 30 m of facility coordinates,
  and sets pin_source = "synthetic_seed".

Road & Water:
- 2-3 hotspot points generated within 1 km of the village centre.
- Reports placed within 80 m of a hotspot so they merge into realistic assets.
- Sets pin_source = "synthetic_seed".

CLI Flags:
  --dry-run: Print planned assignments without modifying the database.
  --undo: Reset precise_lat, precise_lon, facility_id, and pin_source to NULL
          for all rows where pin_source = 'synthetic_seed'.
"""

import argparse
import math
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_BACKEND_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from database import SessionLocal
from models import CitizenRequest, Gazetteer, PublicFacility
from intelligence.clustering import haversine_km


def offset_coords(lat: float, lon: float, dist_m: float, angle_rad: float) -> Tuple[float, float]:
    """Calculate new coordinates given distance in meters and bearing/angle in radians."""
    dlat = (dist_m * math.sin(angle_rad)) / 111_000.0
    cos_lat = math.cos(math.radians(lat))
    dlon = (dist_m * math.cos(angle_rad)) / (111_000.0 * (cos_lat if abs(cos_lat) > 1e-6 else 1.0))
    return round(lat + dlat, 7), round(lon + dlon, 7)


def undo_synthetic_assets(db) -> int:
    """Reset all synthetic seed assignments back to NULL."""
    rows = db.query(CitizenRequest).filter(CitizenRequest.pin_source == "synthetic_seed").all()
    count = len(rows)
    for r in rows:
        r.precise_lat = None
        r.precise_lon = None
        r.facility_id = None
        r.pin_source = None
    db.commit()
    return count


def assign_synthetic_assets(db, dry_run: bool = False) -> Dict[str, int]:
    """Assign deterministic demo pins to synthetic reports."""
    # Strict filter: ONLY synthetic reports with no precise coordinates
    query = (
        db.query(CitizenRequest)
        .filter(CitizenRequest.is_synthetic == 1)
        .filter(CitizenRequest.precise_lat.is_(None))
    )
    reports = query.all()

    # Preload public facilities
    facilities = (
        db.query(PublicFacility)
        .filter(PublicFacility.latitude.isnot(None))
        .filter(PublicFacility.longitude.isnot(None))
        .all()
    )
    schools = [f for f in facilities if f.category == "education"]
    hospitals = [f for f in facilities if f.category == "health"]

    counts = {
        "total_synthetic": len(reports),
        "education_assigned": 0,
        "health_assigned": 0,
        "road_assigned": 0,
        "water_assigned": 0,
        "untouched": 0,
    }

    # Group road and water reports by village to build shared hotspots
    hotspots_by_village: Dict[Tuple[str, str, str, str], List[Tuple[float, float]]] = {}

    for r in reports:
        v_lat = r.latitude
        v_lon = r.longitude
        if v_lat is None or v_lon is None:
            counts["untouched"] += 1
            continue

        rng = random.Random(r.id)

        if r.issue_category == "education":
            # School in or within 2 km of village centroid
            cands = [
                s for s in schools
                if haversine_km(v_lat, v_lon, s.latitude, s.longitude) <= 2.0
            ]
            cands.sort(key=lambda s: (haversine_km(v_lat, v_lon, s.latitude, s.longitude), s.id))
            if not cands:
                counts["untouched"] += 1
                continue

            picked = rng.choice(cands)
            jitter_m = rng.uniform(0.0, 30.0)
            angle = rng.uniform(0.0, 2 * math.pi)
            p_lat, p_lon = offset_coords(picked.latitude, picked.longitude, jitter_m, angle)

            if not dry_run:
                r.facility_id = picked.id
                r.precise_lat = p_lat
                r.precise_lon = p_lon
                r.pin_source = "synthetic_seed"
            counts["education_assigned"] += 1

        elif r.issue_category == "health":
            # Hospital among 3 nearest within 8 km
            cands = [
                h for h in hospitals
                if haversine_km(v_lat, v_lon, h.latitude, h.longitude) <= 8.0
            ]
            cands.sort(key=lambda h: (haversine_km(v_lat, v_lon, h.latitude, h.longitude), h.id))
            if not cands:
                counts["untouched"] += 1
                continue

            top3 = cands[:3]
            picked = rng.choice(top3)
            jitter_m = rng.uniform(0.0, 30.0)
            angle = rng.uniform(0.0, 2 * math.pi)
            p_lat, p_lon = offset_coords(picked.latitude, picked.longitude, jitter_m, angle)

            if not dry_run:
                r.facility_id = picked.id
                r.precise_lat = p_lat
                r.precise_lon = p_lon
                r.pin_source = "synthetic_seed"
            counts["health_assigned"] += 1

        elif r.issue_category in ("road", "water"):
            # 2-3 hotspots per (village, category) within 1 km
            key = (r.district or "", r.block or "", r.village or "", r.issue_category)
            if key not in hotspots_by_village:
                v_seed = f"hotspot:{key[0]}:{key[1]}:{key[2]}:{key[3]}"
                v_rng = random.Random(v_seed)
                k = v_rng.choice([2, 3])
                h_list = []
                for _ in range(k):
                    dist_m = v_rng.uniform(100.0, 900.0)
                    h_angle = v_rng.uniform(0.0, 2 * math.pi)
                    h_list.append(offset_coords(v_lat, v_lon, dist_m, h_angle))
                hotspots_by_village[key] = h_list

            hotspots = hotspots_by_village[key]
            picked_hotspot = rng.choice(hotspots)
            jitter_m = rng.uniform(0.0, 80.0)
            angle = rng.uniform(0.0, 2 * math.pi)
            p_lat, p_lon = offset_coords(picked_hotspot[0], picked_hotspot[1], jitter_m, angle)

            if not dry_run:
                r.precise_lat = p_lat
                r.precise_lon = p_lon
                r.pin_source = "synthetic_seed"
            if r.issue_category == "road":
                counts["road_assigned"] += 1
            else:
                counts["water_assigned"] += 1

        else:
            counts["untouched"] += 1

    if not dry_run:
        db.commit()

    return counts


def main():
    parser = argparse.ArgumentParser(description="Assign deterministic demo locations to synthetic reports.")
    parser.add_argument("--dry-run", action="store_true", help="Print assignment stats without writing to DB.")
    parser.add_argument("--undo", action="store_true", help="Revert all synthetic seed assignments.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.undo:
            count = undo_synthetic_assets(db)
            print(f"Undo completed: reverted {count} synthetic reports with pin_source='synthetic_seed'.")
            return

        print(f"Running assign_synthetic_assets (dry_run={args.dry_run})...")
        counts = assign_synthetic_assets(db, dry_run=args.dry_run)
        print("\nAssignment Summary:")
        print(f"  Total synthetic inspected: {counts['total_synthetic']}")
        print(f"  Education assigned:        {counts['education_assigned']}")
        print(f"  Health assigned:           {counts['health_assigned']}")
        print(f"  Road assigned:             {counts['road_assigned']}")
        print(f"  Water assigned:            {counts['water_assigned']}")
        print(f"  Untouched (no match):      {counts['untouched']}")
        total_assigned = (
            counts["education_assigned"]
            + counts["health_assigned"]
            + counts["road_assigned"]
            + counts["water_assigned"]
        )
        print(f"  Total assigned:            {total_assigned}")
    finally:
        db.close()


if __name__ == "__main__":
    main()

