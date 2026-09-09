"""
Loads REAL, individually-named schools into `public_facility`.

WHY THIS LOADER EXISTS
----------------------
`village_amenities` (Census 2011) records that a village HAS two primary
schools. It never records WHICH ones. So a cluster could say "education
problem in Dabhadi, 6 reports" and stop -- while the Zilla Parishad needs a
name to put on a work order.

This loader supplies the missing half: every school in the pilot districts,
with its official UDISE code, its name, and its coordinates. A work group can
then be labelled `Z.P.SCHOOL DABHADI` rather than `Dabhadi`.

SOURCE
------
https://github.com/datameet/udise_schools -- DataMeet's mirror of the
Ministry of Education's UDISE+ school directory, published as GeoJSON, one
file per district, ~1000 schools per page.

Each feature carries schcd (the UDISE code, which is the school's national
identifier), schname, school_cat, management, vilname, dtname and a point
geometry. Verified against the live files before this was written; see
REAL_DATA_RESEARCH.md.

Chosen over the alternatives, all of which were tried first:
  * OpenStreetMap (Overpass) -- returned 2 schools within 6 km of Bidri.
    Rural POI coverage is far too sparse to name an asset.
  * udiseplus.gov.in "Know Your School" -- a SPA; no documented public API.
  * data.gov.in -- carries UDISE *aggregates* (enrolment by category), not
    the school directory.

WHY ONLY TWO DISTRICTS
----------------------
The full archive is 78 MB and ~1.5 million schools. The pilot covers Kolhapur
and Nashik, so only those two districts are fetched -- 10 files, ~9,400
schools. DISTRICTS below maps our district names to UDISE's own district file
codes, which were confirmed by reading dtname out of each file rather than
assumed from any published code list.

Run:  python load_udise_schools.py
Idempotent: clears source='udise' rows first, so re-running refreshes.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import SessionLocal, engine  # noqa: E402
from models import Base, PublicFacility  # noqa: E402

RAW = (
    "https://raw.githubusercontent.com/datameet/udise_schools/master/raw/"
    "ud_dt_n_{code}_page_{page:02d}.geojson"
)

# district name -> (UDISE district file code, number of pages). Both codes
# were verified by fetching each 27xx file and reading its dtname, not taken
# from a code list that might disagree with this dataset's own numbering.
DISTRICTS = {
    "Kolhapur": ("2734", 4),
    "Nashik": ("2720", 6),
}

SOURCE = "udise"


def fetch(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
        print(f"  ! {url.rsplit('/', 1)[-1]}: {type(err).__name__}")
        return None


def clean(value) -> str | None:
    """UDISE pads unknown text fields with a single space rather than null."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    removed = (
        db.query(PublicFacility)
        .filter(PublicFacility.source == SOURCE)
        .delete(synchronize_session=False)
    )
    if removed:
        print(f"cleared {removed} existing UDISE rows")

    total = 0
    for district, (code, pages) in DISTRICTS.items():
        kept = 0
        for page in range(1, pages + 1):
            data = fetch(RAW.format(code=code, page=page))
            if not data:
                continue

            for feature in data.get("features", []):
                props = feature.get("properties") or {}
                coords = (feature.get("geometry") or {}).get("coordinates") or []
                if len(coords) != 2:
                    continue
                lon, lat = coords
                # A school with no usable point cannot name anything, and a
                # 0,0 coordinate would silently match the first work group
                # the distance sort ever sees.
                if not lat or not lon:
                    continue

                db.add(
                    PublicFacility(
                        source=SOURCE,
                        external_id=clean(props.get("schcd")),
                        name=clean(props.get("schname")),
                        category="education",
                        sub_type=clean(props.get("school_cat")),
                        management=clean(props.get("management")),
                        village=clean(props.get("vilname")),
                        block=None,          # UDISE carries no block/taluka
                        district=district,   # our spelling, not UDISE's
                        latitude=lat,
                        longitude=lon,
                    )
                )
                kept += 1

        print(f"{district:10s} {kept:5d} schools")
        total += kept

    db.commit()
    db.close()
    print(f"\nloaded {total} named schools into public_facility")


if __name__ == "__main__":
    main()
