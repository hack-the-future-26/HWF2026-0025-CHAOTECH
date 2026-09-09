"""
Data repair / enrichment pass. Idempotent -- safe to run repeatedly.

Fixes three concrete gaps found by auditing the live database:

1. Taluka (block) headquarters were missing from the gazetteer entirely.
   OpenStreetMap's Overpass query in load_gazetteer.py asks for
   place=village|town nodes, which does not return taluka names as such --
   so "Karvir", the village named in both the research report's worked
   example (SS1.3) and the build plan's Interface Contract (Section 2), was
   absent and could never be geocoded.

2. gazetteer.block was NULL on every row, so no block-level rollup or
   block-level equity lookup was possible.

   NOTE ON HONESTY: block assignment here is *nearest taluka headquarters
   within the same district*, a geometric approximation -- NOT official
   revenue-boundary data. It is good enough for clustering and equity
   lookups and is labelled as an approximation wherever it surfaces. Replace
   it with real taluka boundary polygons before any claim of administrative
   accuracy.

3. citizen_request.latitude/longitude were NULL on all existing rows because
   of the "lat"/"latitude" key mismatch (now fixed in
   routes_citizen_report.py and seed_synthetic_data.py). This backfills the
   already-stored rows so /map-data is not empty.
"""

import math
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from database import SessionLocal
from models import CitizenRequest, Gazetteer
from pipeline.main import process_report

# ---------------------------------------------------------------------------
# Real taluka (block) headquarters. Names and coordinates are real places;
# Karvir's coordinates match the build plan's own Interface Contract example.
# ---------------------------------------------------------------------------

TALUKA_HQ = {
    "Kolhapur": [
        ("Karvir", 16.6912, 74.2432),
        ("Panhala", 16.8100, 74.1100),
        ("Shahuwadi", 16.9333, 73.9333),
        ("Hatkanangale", 16.7667, 74.4667),
        ("Shirol", 16.7167, 74.6167),
        ("Kagal", 16.5833, 74.3167),
        ("Gadhinglaj", 16.2333, 74.3500),
        ("Bhudargad", 16.3833, 74.1167),
        ("Radhanagari", 16.4333, 73.9500),
        ("Gaganbawada", 16.7833, 73.9500),
        ("Ajra", 16.1167, 74.2167),
        ("Chandgad", 15.9667, 74.2667),
    ],
    "Nashik": [
        ("Nashik", 20.0059, 73.7910),
        ("Igatpuri", 19.6967, 73.5628),
        ("Dindori", 20.2000, 73.8333),
        ("Peth", 20.1833, 73.3833),
        ("Trimbakeshwar", 19.9319, 73.5286),
        ("Kalwan", 20.4667, 73.9500),
        ("Deola", 20.5833, 74.0333),
        ("Surgana", 20.6167, 73.6167),
        ("Baglan", 20.5833, 74.2000),
        ("Malegaon", 20.5579, 74.5288),
        ("Nandgaon", 20.3061, 74.6522),
        ("Chandwad", 20.3286, 74.2472),
        ("Niphad", 20.0833, 74.1000),
        ("Sinnar", 19.8500, 74.0000),
        ("Yeola", 20.0400, 74.4900),
    ],
}

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def add_missing_taluka_hqs(db) -> int:
    """Insert any taluka HQ not already present (matched by name+district)."""
    added = 0
    for district, hqs in TALUKA_HQ.items():
        existing = {
            name for (name,) in db.query(Gazetteer.name)
            .filter(Gazetteer.district == district).all()
        }
        for name, lat, lon in hqs:
            if name in existing:
                continue
            db.add(
                Gazetteer(
                    name=name,
                    admin_level="block",
                    district=district,
                    block=name,          # a taluka HQ sits in its own block
                    population=None,
                    latitude=lat,
                    longitude=lon,
                )
            )
            added += 1
    db.commit()
    return added


def assign_blocks(db) -> int:
    """
    Assign gazetteer.block by nearest taluka HQ within the same district.
    Geometric approximation -- see the module docstring.
    """
    updated = 0
    for district, hqs in TALUKA_HQ.items():
        rows = (
            db.query(Gazetteer)
            .filter(Gazetteer.district == district)
            .filter(Gazetteer.latitude.isnot(None))
            .all()
        )
        for row in rows:
            nearest = min(
                hqs,
                key=lambda hq: haversine_km(row.latitude, row.longitude, hq[1], hq[2]),
            )
            if row.block != nearest[0]:
                row.block = nearest[0]
                updated += 1
    db.commit()
    return updated


def backfill_coordinates_from_gazetteer(db) -> int:
    """
    Pass 1: rows that already resolved to a village name but lost their
    coordinates to the key-mismatch bug. Copy coordinates straight across.
    """
    lookup: dict[tuple[str, str | None], Gazetteer] = {}
    for g in db.query(Gazetteer).all():
        lookup.setdefault((g.name, g.district), g)
        lookup.setdefault((g.name, None), g)

    updated = 0
    rows = (
        db.query(CitizenRequest)
        .filter(CitizenRequest.latitude.is_(None))
        .filter(CitizenRequest.village.isnot(None))
        .all()
    )
    for row in rows:
        match = lookup.get((row.village, row.district)) or lookup.get((row.village, None))
        if match is None:
            continue
        row.latitude = match.latitude
        row.longitude = match.longitude
        if not row.block:
            row.block = match.block
        updated += 1
    db.commit()
    return updated


def regeocode_unresolved(db) -> int:
    """
    Pass 2: rows that never resolved to a village at all. Re-run the pipeline
    against the now-enriched gazetteer -- mentions of Karvir and other taluka
    names could not resolve before because those rows did not exist.
    """
    gazetteer_rows = [
        {
            "name": g.name,
            "district": g.district,
            "block": g.block,
            "lat": g.latitude,
            "lon": g.longitude,
        }
        for g in db.query(Gazetteer).all()
    ]

    updated = 0
    rows = (
        db.query(CitizenRequest)
        .filter(CitizenRequest.village.is_(None))
        .filter(CitizenRequest.raw_text.isnot(None))
        .all()
    )
    for row in rows:
        result = process_report({"text": row.raw_text}, gazetteer_rows)
        resolved = result.get("location_resolved")
        if not resolved:
            continue
        row.district = resolved.get("district") or row.district
        row.block = resolved.get("block") or row.block
        row.village = resolved.get("village")
        row.latitude = resolved.get("lat")
        row.longitude = resolved.get("lon")
        # Categories can also improve now, but only fill a genuine gap --
        # never overwrite a category the original run already decided on.
        if row.issue_category is None and result.get("issue_category"):
            row.issue_category = result["issue_category"]
        updated += 1
    db.commit()
    return updated


def report(db) -> None:
    total = db.query(CitizenRequest).count()
    with_coords = db.query(CitizenRequest).filter(CitizenRequest.latitude.isnot(None)).count()
    with_village = db.query(CitizenRequest).filter(CitizenRequest.village.isnot(None)).count()
    with_cat = db.query(CitizenRequest).filter(CitizenRequest.issue_category.isnot(None)).count()
    gaz = db.query(Gazetteer).count()
    gaz_blocks = db.query(Gazetteer).filter(Gazetteer.block.isnot(None)).count()
    karvir = db.query(Gazetteer).filter(Gazetteer.name == "Karvir").count()

    print("\n--- state after repair ---")
    print(f"  gazetteer rows              : {gaz}")
    print(f"  gazetteer rows with block   : {gaz_blocks}")
    print(f"  'Karvir' present            : {'yes' if karvir else 'NO'}")
    print(f"  citizen_request rows        : {total}")
    print(f"  ...with coordinates         : {with_coords} ({with_coords / total:.0%})" if total else "")
    print(f"  ...with a resolved village  : {with_village} ({with_village / total:.0%})" if total else "")
    print(f"  ...with an issue category   : {with_cat} ({with_cat / total:.0%})" if total else "")


def main() -> None:
    db = SessionLocal()
    try:
        print("1/4 adding missing taluka headquarters...")
        print(f"    added {add_missing_taluka_hqs(db)} rows")

        print("2/4 assigning blocks (nearest-HQ approximation)...")
        print(f"    updated {assign_blocks(db)} gazetteer rows")

        print("3/4 backfilling coordinates from gazetteer...")
        print(f"    updated {backfill_coordinates_from_gazetteer(db)} citizen_request rows")

        print("4/4 re-geocoding previously unresolved reports...")
        print(f"    resolved {regeocode_unresolved(db)} additional rows")

        report(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
