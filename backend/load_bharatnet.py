"""
Loads REAL, CURRENT digital-connectivity data from BharatNet / BBNL into
`village_amenities.conn_bharatnet_*`.

WHY THIS IS THE MOST IMPORTANT LOADER FOR FAIRNESS
--------------------------------------------------
The equity term is the load-bearing part of this whole system: it is what lets
a quiet, cut-off village outrank a loud, well-connected one. It works by asking
"how hard is it for people HERE to report a problem at all?"

Before this loader, that question was answered by a hardcoded list of ten block
names somebody guessed were remote (intelligence/config.py). The obvious fix --
Census 2011 mobile coverage -- turns out not to work either: after loading the
Census we measured it, and only 9 of 942 villages in these two districts lack
mobile coverage. A field where 99% of rows share one value cannot rank anything.

BharatNet fixes both problems. It is 2022 data (11 years fresher), it covers
gram panchayats individually, and it actually varies: across Maharashtra,
12,012 GPs are UP and 8,116 are DOWN.

WHAT IT MEASURES -- AND WHAT IT DOES NOT
----------------------------------------
BharatNet is the national rural optical-fibre programme. `Opn Status` is the
operational state of the fibre connection to the GRAM PANCHAYAT office:

    UP                  fibre working
    DOWN                fibre installed but not working
    #N/A / UNKNOWN ...  no current status recorded

This is institutional digital infrastructure, NOT household mobile coverage.
A village whose GP fibre is DOWN is genuinely digitally underserved, but the
two are not the same measurement, and any dashboard text must say which one it
is showing. Use it alongside the Census field, not as a drop-in replacement.

MATCHING
--------
Gram panchayat names and village names differ (a GP covers several villages),
so this matches SPATIALLY, not by name: each village takes the status of the
nearest GP fibre node within MAX_MATCH_KM. Spatial matching is the honest
choice here -- a name match between two different administrative levels would
look precise while being arbitrary.

SOURCE
------
https://storage.googleapis.com/bbnl_data/parsed.zip  (public, no login)
  parsed/active_gp_status.csv  -- State/District/Block/GP + Opn Status
  parsed/GP_locations.csv      -- the same GPs with LAT/LONG
See REAL_DATA_RESEARCH.md §2.5.
"""

import csv
import io
import math
import sys
import zipfile

import requests

from database import SessionLocal
from models import Gazetteer, VillageAmenities

BBNL_URL = "https://storage.googleapis.com/bbnl_data/parsed.zip"
HEADERS = {"User-Agent": "ComplainBox-Hackathon/1.0 (bharatnet loader)"}

STATUS_FILE = "parsed/active_gp_status.csv"
LOCATION_FILE = "parsed/GP_locations.csv"

DISTRICTS = {"Kolhapur": "KOLHAPUR", "Nashik": "NASHIK"}

# Beyond this, the nearest fibre node says nothing useful about the village.
# Left deliberately generous: GPs are sparse, and a NULL is more honest than
# a status borrowed from 40km away.
MAX_MATCH_KM = 15.0

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Same formula the intelligence package uses, kept local to avoid a
    backend -> intelligence import (the dependency runs the other way)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def read_csv_from_zip(archive: zipfile.ZipFile, name: str) -> list[dict]:
    with archive.open(name) as handle:
        text = io.TextIOWrapper(handle, encoding="utf-8", errors="replace")
        return list(csv.DictReader(text))


def normalise(value: str | None) -> str:
    return (value or "").strip().upper()


def build_gp_index(archive: zipfile.ZipFile) -> dict[str, list[dict]]:
    """
    {our district name: [{gp, block, status, lat, lon}, ...]}

    Status and coordinates live in two different files keyed by
    (district, block, GP name), so they are joined on that triple.
    """
    statuses = read_csv_from_zip(archive, STATUS_FILE)
    locations = read_csv_from_zip(archive, LOCATION_FILE)
    print(f"  status rows: {len(statuses):,}   location rows: {len(locations):,}")

    wanted = set(DISTRICTS.values())

    status_by_key: dict[tuple[str, str, str], str] = {}
    for row in statuses:
        district = normalise(row.get("District Name"))
        if district not in wanted:
            continue
        key = (district, normalise(row.get("Block Name")), normalise(row.get("GP/ ONT Name")))
        status_by_key[key] = (row.get("Opn Status") or "").strip()

    index: dict[str, list[dict]] = {name: [] for name in DISTRICTS}
    reverse = {v: k for k, v in DISTRICTS.items()}
    unlocated = 0

    for row in locations:
        district = normalise(row.get("DISTRICT"))
        if district not in wanted:
            continue
        try:
            lat = float(row.get("LAT") or "")
            lon = float(row.get("LONG") or "")
        except ValueError:
            unlocated += 1
            continue
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            unlocated += 1
            continue

        key = (district, normalise(row.get("BLOCK")), normalise(row.get("GP_NAME")))
        index[reverse[district]].append(
            {
                "gp": (row.get("GP_NAME") or "").strip(),
                "block": (row.get("BLOCK") or "").strip(),
                "status": status_by_key.get(key),
                "lat": lat,
                "lon": lon,
            }
        )

    if unlocated:
        print(f"  GP rows skipped for unusable coordinates: {unlocated}")
    return index


def main() -> int:
    print(f"Downloading BharatNet dataset (~8MB) from {BBNL_URL} ...")
    try:
        response = requests.get(BBNL_URL, headers=HEADERS, timeout=300)
        response.raise_for_status()
        archive = zipfile.ZipFile(io.BytesIO(response.content))
    except Exception as exc:
        print(f"FAILED to download/open BharatNet data: {exc}")
        return 1

    index = build_gp_index(archive)
    for district, nodes in index.items():
        with_status = sum(1 for n in nodes if n["status"])
        print(f"  {district}: {len(nodes)} located GPs ({with_status} with a status)")

    db = SessionLocal()
    try:
        total_set = 0
        total_far = 0

        for district, nodes in index.items():
            if not nodes:
                print(f"\n{district}: no GP nodes -- skipped")
                continue

            pairs = (
                db.query(Gazetteer, VillageAmenities)
                .join(VillageAmenities, VillageAmenities.gazetteer_id == Gazetteer.id)
                .filter(Gazetteer.district == district)
                .all()
            )
            print(f"\n{district}: {len(pairs)} villages with amenity rows")

            set_here = 0
            too_far = 0
            status_counts: dict[str, int] = {}

            for gaz, amenities in pairs:
                if gaz.latitude is None or gaz.longitude is None:
                    continue

                nearest = None
                nearest_km = None
                for node in nodes:
                    km = haversine_km(gaz.latitude, gaz.longitude, node["lat"], node["lon"])
                    if nearest_km is None or km < nearest_km:
                        nearest, nearest_km = node, km

                if nearest is None or nearest_km > MAX_MATCH_KM:
                    too_far += 1
                    continue

                status = nearest["status"] or "NO_STATUS_RECORDED"
                amenities.conn_bharatnet_status = status
                amenities.conn_bharatnet_gp = nearest["gp"]
                amenities.conn_bharatnet_distance_km = round(nearest_km, 3)
                status_counts[status] = status_counts.get(status, 0) + 1
                set_here += 1

            db.commit()
            print(f"  matched to a fibre node: {set_here}   none within {MAX_MATCH_KM}km: {too_far}")
            print(f"  status spread: {status_counts}")
            total_set += set_here
            total_far += too_far

        print("\n=== Summary ===")
        print(f"  villages given a BharatNet status: {total_set}")
        print(f"  villages with no node in range   : {total_far}")
        print(
            "  REMINDER: this is fibre to the gram-panchayat office, not\n"
            "            household mobile coverage. Label it accordingly."
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
