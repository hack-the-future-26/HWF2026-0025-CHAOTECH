"""
Loads REAL, individually-named health facilities into `public_facility`.

WHY THIS LOADER EXISTS
----------------------
`village_amenities` (Census 2011) records that a village has one sub-centre
and no PHC. It never records WHICH sub-centre. So a health cluster could say
"health problem in Yeola, 7 reports" and stop -- while the District Health
Officer needs a facility to send anyone to.

This is the health counterpart of load_udise_schools.py, and it closes the
last sector gap in naming work groups. Before it, education resolved to a
named school and roads to a named PMGSY work, while health and water fell
back to "the village" -- and a citizen wanting to report a specific hospital
had nothing to point at.

SOURCE
------
https://github.com/yashveeeeeeer/india-geodata, GitHub release
`healthcare/facilities` -> INDIA_HEALTH_FACILITIES_NIC.geojson (~50 MB,
147,957 features nationally). Originally NIC HealthGIS (healthgis.in) under
the National Health Mission, published under the India Open Government
Licence.

Each feature carries name, type (SCE / PHC / CHC / THO / DHO), place,
district, state and a point geometry. Verified against the live file before
this was written; see REAL_DATA_RESEARCH.md.

Chosen over the alternatives, all of which were tried first:
  * DataMeet -- mirrors schools but has no health facility repository.
  * facility.abdm.gov.in -- redirects; no documented public bulk export.
  * OpenStreetMap -- the same sparse rural coverage that ruled it out for
    schools.

WATCH THE DISTRICT SPELLING
---------------------------
This file spells Nashik "Nasik". DISTRICTS below maps its spelling to ours,
because filtering on our own spelling silently returns zero rows for the
larger of the two pilot districts -- a failure that looks exactly like "no
data exists".

Run:  python load_health_facilities.py
Idempotent: clears source='nic_healthgis' rows first, so re-running refreshes.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import SessionLocal, engine  # noqa: E402
from models import Base, PublicFacility  # noqa: E402

RELEASE = (
    "https://github.com/yashveeeeeeer/india-geodata/releases/download/"
    "healthcare/facilities/INDIA_HEALTH_FACILITIES_NIC.geojson"
)

# The file's district spelling -> ours. Ours is what the gazetteer, the
# citizen reports and every other table use, so the mapping is applied on the
# way in and nothing downstream has to know the difference.
DISTRICTS = {
    "Kolhapur": "Kolhapur",
    "Nasik": "Nashik",
}

STATE = "MAHARASHTRA"
SOURCE = "nic_healthgis"

# NIC's abbreviations, expanded so the dashboard can show a citizen something
# they would recognise rather than a three-letter code.
FACILITY_TYPES = {
    "SCE": "Sub-centre",
    "PHC": "Primary Health Centre",
    "CHC": "Community Health Centre",
    "THO": "Taluka Health Office",
    "DHO": "District Health Office",
}

# Cached beside this file: the download is ~50 MB and the loader is expected
# to be re-run while tuning, so it is fetched once unless deleted.
CACHE = Path(__file__).resolve().parent / "data" / "india_health_facilities.geojson"


def fetch() -> dict | None:
    if CACHE.exists() and CACHE.stat().st_size > 1_000_000:
        print(f"using cached {CACHE.name} ({CACHE.stat().st_size / 1e6:.0f} MB)")
        return json.loads(CACHE.read_text(encoding="utf-8"))

    print(f"downloading {RELEASE.rsplit('/', 1)[-1]} (~50 MB)…")
    try:
        with urllib.request.urlopen(RELEASE, timeout=900) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
        print(f"  ! download failed: {type(err).__name__}: {err}")
        return None

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(raw, encoding="utf-8")
    return json.loads(raw)


def clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def main() -> None:
    data = fetch()
    if not data:
        print("nothing loaded")
        return

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    removed = (
        db.query(PublicFacility)
        .filter(PublicFacility.source == SOURCE)
        .delete(synchronize_session=False)
    )
    if removed:
        print(f"cleared {removed} existing NIC HealthGIS rows")

    counts: dict[str, int] = {}
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        if (props.get("state") or "").upper() != STATE:
            continue

        district = DISTRICTS.get(clean(props.get("district")) or "")
        if not district:
            continue

        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) != 2:
            continue
        lon, lat = coords
        # A facility with no usable point cannot name anything, and 0,0 would
        # sit off the coast of Africa and match the first work group the
        # distance sort ever sees.
        if not lat or not lon:
            continue

        code = clean(props.get("type"))
        db.add(
            PublicFacility(
                source=SOURCE,
                external_id=clean(props.get("source_id")),
                name=clean(props.get("name")),
                category="health",
                sub_type=FACILITY_TYPES.get(code or "", code),
                management="Government",
                # NIC's `place` is "village, TALUKA"; the village half is the
                # useful one and the taluka is already implied by the match.
                village=(clean(props.get("place")) or "").split(",")[0] or None,
                block=None,
                district=district,
                latitude=lat,
                longitude=lon,
            )
        )
        counts[district] = counts.get(district, 0) + 1

    db.commit()
    db.close()

    for district, n in sorted(counts.items()):
        print(f"{district:10s} {n:5d} health facilities")
    print(f"\nloaded {sum(counts.values())} named health facilities into public_facility")


if __name__ == "__main__":
    main()
