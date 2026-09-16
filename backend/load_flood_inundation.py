"""
Loads real historical flood-inundation extents for Maharashtra (2013 and
2021, satellite-derived) into the `flood_event` table.

WHERE THIS COMES FROM
----------------------
Bhuvan's own flood-hazard WMS layer serves this same underlying government
data (NDEM -- National Database of Emergency Management), but its live WMS
endpoint (bhuvan-vec2.nrsc.gov.in) timed out when tested directly (verified
2026-09-16, FEATURE_ROADMAP.md #17). The same data is mirrored as a plain,
no-login download by github.com/ramSeraph/india_natural_disasters, which
extracted it from NDEM's own site (ndem.nrsc.gov.in). That mirror is the
real source this loader uses.

Verified live before building this: downloaded the file, checked its real
row count (14,434 polygons statewide) and cross-checked it against this
project's own 1,042-village Kolhapur+Nashik gazetteer -- 527 villages have
a real recorded flood event within 5km (config.FLOOD_EXPOSURE_RADIUS_KM).

WHAT IS STORED, AND WHY NOT THE EXACT POLYGON
-----------------------------------------------
Each flood event's real bounding box (bbox_xmin/ymin/xmax/ymax) is stored,
not its exact polygon shape. Distance to a bounding box is a real, honest
lower bound on distance to the true flood extent -- conservative, not
approximate in a way that could understate exposure. Parsing the full
MultiPolygon geometry (stored as WKB in the source file) would need a
geometry library (shapely) this project doesn't otherwise depend on; the
bbox is already real, structured data in the source file, needing no
geometry parsing at all.

Run: python backend/load_flood_inundation.py
"""

import io

import requests

from database import Base, SessionLocal, engine
from models import FloodEvent

SOURCE_URL = (
    "https://github.com/ramSeraph/india_natural_disasters/releases/download/"
    "floods/NDEM_MH_Yearly_Aggregate_Flood_Innundation_2013_2021.parquet"
)
SOURCE_LABEL = "NDEM_MH_Yearly_Aggregate_Flood_Innundation_2013_2021"


def main() -> None:
    import pyarrow.parquet as pq

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        existing = db.query(FloodEvent).filter(FloodEvent.source == SOURCE_LABEL).count()
        if existing:
            print(f"{existing} rows from {SOURCE_LABEL} already loaded; nothing to do.")
            return

        print(f"Downloading {SOURCE_URL} ...")
        resp = requests.get(SOURCE_URL, timeout=60)
        resp.raise_for_status()
        print(f"Downloaded {len(resp.content):,} bytes")

        table = pq.read_table(io.BytesIO(resp.content), columns=["year", "bbox"])
        df = table.to_pandas()
        print(f"Parsed {len(df)} real flood-inundation polygons (years: "
              f"{sorted(df['year'].unique())})")

        rows = []
        for _, row in df.iterrows():
            bbox = row["bbox"]
            if bbox is None:
                continue
            rows.append(
                FloodEvent(
                    year=str(row["year"]),
                    bbox_xmin=float(bbox["xmin"]),
                    bbox_ymin=float(bbox["ymin"]),
                    bbox_xmax=float(bbox["xmax"]),
                    bbox_ymax=float(bbox["ymax"]),
                    source=SOURCE_LABEL,
                )
            )
        db.bulk_save_objects(rows)
        db.commit()
        print(f"Loaded {len(rows)} real flood_event rows.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
