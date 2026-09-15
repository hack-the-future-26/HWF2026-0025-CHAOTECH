"""
Loads REAL PMGSY GeoSadak road geometry into `pmgsy_road_segment`.

WHY THIS LOADER EXISTS
----------------------
Previously, road work groups only resolved to either "Road near <village>"
or an undelivered PMGSY work matched by text to the village name.
PMGSY works (`GovernmentProject`) carry funding, cost, and sanction info,
but no line geometry.

This loader provides physical line geometry from PMGSY GeoSadak's Road_DRRP
(District Rural Roads Plan) layer. It enables real spatial matching of citizen
reports to named physical road segments by spatial line proximity, rather than
fuzzy village-name matching.

SOURCE
------
https://github.com/datameet/pmgsy-geosadak, `data/Road_DRRP/Maharashtra.zip`
Originally Ministry of Rural Development, Government of India (PMGSY GeoSadak),
published under the Government Open Data License (GODL-India).
Master data for district and block mapping: `data/MasterData.xls`.

ATTRIBUTE CONFIRMATION (Verified on 2026-09-14)
----------------------------------------------
Inspected attribute table for Kolhapur (DISTRICT_I=306) and Nashik (DISTRICT_I=393):
- ER_ID: unique segment ID
- RoadName: 100% present in both districts (3,104 in Kolhapur, 4,959 in Nashik)
- DRRP_ROAD_: 100% present road code (e.g. "VR 18", "ODR-36")
- RoadCatego: 100% present (e.g. "RR(VR)", "RR(ODR)", "MDR", "SH", "NH")
- RoadOwner: 100% present (e.g. "RWD", "PWD", "MRRDA", "RD")
- Surface / condition fields: NONE present in Road_DRRP2.
Hence this layer serves strictly as an asset identity/location source, not
condition-scoring.

Run:  python load_pmgsy_geosadak.py [--dry-run]
Idempotent: clears existing segments for Kolhapur and Nashik before inserting.
"""

import json
import os
import sys
import zipfile
from pathlib import Path

# The backend directory
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from database import SessionLocal, engine  # noqa: E402
from models import Base, PMGSYRoadSegment  # noqa: E402

DATA_DIR = _BACKEND_DIR / "data" / "pmgsy"
ZIP_PATH = DATA_DIR / "Maharashtra.zip"
SHP_PATH = DATA_DIR / "Road_DRRP2.shp"
MASTER_DATA_PATH = DATA_DIR / "MasterData.xls"

DISTRICT_MAP = {
    306: "Kolhapur",
    393: "Nashik",
}


def load_block_mapping() -> dict[int, str]:
    """Load BLOCK_ID -> BLOCK_NAME mapping from MasterData.xls."""
    if not MASTER_DATA_PATH.exists():
        print(f"[pmgsy] MasterData.xls not found at {MASTER_DATA_PATH}, block names will be null")
        return {}

    try:
        import pandas as pd
        df = pd.read_excel(MASTER_DATA_PATH)
        mapping = {}
        for _, row in df.iterrows():
            bid = row.get("BLOCK_ID")
            bname = row.get("BLOCK_NAME")
            if pd.notna(bid) and pd.notna(bname):
                mapping[int(bid)] = str(bname).strip()
        return mapping
    except Exception as e:
        print(f"[pmgsy] Warning: could not parse MasterData.xls ({e})")
        return {}


def ensure_shapefile() -> bool:
    """Ensure Road_DRRP2.shp exists, extracting from Maharashtra.zip if needed."""
    if SHP_PATH.exists():
        return True

    if not ZIP_PATH.exists():
        print(f"[pmgsy] Error: neither {SHP_PATH} nor {ZIP_PATH} exists.")
        print("[pmgsy] Please ensure Maharashtra.zip is in backend/data/pmgsy/.")
        return False

    print(f"[pmgsy] Extracting {ZIP_PATH} to {DATA_DIR}...")
    with zipfile.ZipFile(ZIP_PATH, "r") as z:
        z.extractall(DATA_DIR)
    return SHP_PATH.exists()


def load_pmgsy_road_segments(dry_run: bool = False) -> int:
    import shapefile

    if not ensure_shapefile():
        return 0

    block_map = load_block_mapping()
    print(f"[pmgsy] Loaded {len(block_map)} block mappings from MasterData.xls")

    print(f"[pmgsy] Reading shapefile {SHP_PATH}...")
    sf = shapefile.Reader(str(SHP_PATH))
    records = sf.records()
    total_records = len(records)
    print(f"[pmgsy] Total shapefile records: {total_records}")

    segments_to_insert = []
    district_counts = {d: 0 for d in DISTRICT_MAP.values()}
    category_counts = {}

    for idx, r in enumerate(records):
        rec = r.as_dict()
        dist_id = rec.get("DISTRICT_I")
        if dist_id not in DISTRICT_MAP:
            continue

        district_name = DISTRICT_MAP[dist_id]
        shape = sf.shape(idx)

        # Coordinate extraction
        pts = shape.points
        if not pts:
            continue

        # Points are (lon, lat) in shapefile
        start_lon, start_lat = round(pts[0][0], 6), round(pts[0][1], 6)
        end_lon, end_lat = round(pts[-1][0], 6), round(pts[-1][1], 6)

        # Bounding box: [xmin, ymin, xmax, ymax] -> [min_lon, min_lat, max_lon, max_lat]
        bbox = shape.bbox
        min_lon, min_lat = round(bbox[0], 6), round(bbox[1], 6)
        max_lon, max_lat = round(bbox[2], 6), round(bbox[3], 6)

        # Sample or keep coordinates as [[lat, lon], ...]
        coords = [[round(p[1], 6), round(p[0], 6)] for p in pts]
        points_json = json.dumps(coords)

        er_id = rec.get("ER_ID")
        block_id = rec.get("BLOCK_ID")
        block_name = block_map.get(block_id) if block_id else None

        drrp_code = str(rec.get("DRRP_ROAD_")).strip() if rec.get("DRRP_ROAD_") else None
        road_name = str(rec.get("RoadName")).strip() if rec.get("RoadName") else None
        road_cat = str(rec.get("RoadCatego")).strip() if rec.get("RoadCatego") else None
        road_owner = str(rec.get("RoadOwner")).strip() if rec.get("RoadOwner") else None

        district_counts[district_name] += 1
        category_counts[road_cat] = category_counts.get(road_cat, 0) + 1

        segments_to_insert.append({
            "external_id": int(er_id) if er_id is not None else None,
            "state_id": int(rec.get("STATE_ID", 21)),
            "district_id": int(dist_id),
            "block_id": int(block_id) if block_id is not None else None,
            "district": district_name,
            "block": block_name,
            "drrp_road_code": drrp_code,
            "road_name": road_name,
            "road_category": road_cat,
            "road_owner": road_owner,
            "start_lat": start_lat,
            "start_lon": start_lon,
            "end_lat": end_lat,
            "end_lon": end_lon,
            "points_json": points_json,
            "point_count": len(pts),
            "min_lat": min_lat,
            "max_lat": max_lat,
            "min_lon": min_lon,
            "max_lon": max_lon,
        })

    print("\n[pmgsy] Breakdown by district:")
    for dist, count in district_counts.items():
        print(f"  {dist}: {count} segments")

    print("\n[pmgsy] Breakdown by road category:")
    for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count} segments")

    total_segments = len(segments_to_insert)
    print(f"\n[pmgsy] Total pilot road segments to store: {total_segments}")

    if dry_run:
        print("[pmgsy] Dry run complete. No database changes made.")
        return total_segments

    # Ensure table exists
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # Clear existing rows for pilot districts
        deleted = db.query(PMGSYRoadSegment).filter(
            PMGSYRoadSegment.district.in_(list(DISTRICT_MAP.values()))
        ).delete(synchronize_session=False)
        print(f"[pmgsy] Cleared {deleted} previous rows for {list(DISTRICT_MAP.values())}")

        # Batch insert for speed
        BATCH_SIZE = 1000
        for i in range(0, total_segments, BATCH_SIZE):
            batch = [PMGSYRoadSegment(**data) for data in segments_to_insert[i:i+BATCH_SIZE]]
            db.bulk_save_objects(batch)
            db.commit()
            print(f"[pmgsy] Inserted {min(i+BATCH_SIZE, total_segments)} of {total_segments} rows...")

        print("[pmgsy] Database commit successful.")
    except Exception as e:
        db.rollback()
        print(f"[pmgsy] Error inserting records: {e}")
        raise
    finally:
        db.close()

    return total_segments


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    load_pmgsy_road_segments(dry_run=dry_run)
