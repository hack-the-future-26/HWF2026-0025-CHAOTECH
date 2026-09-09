"""
Loads the REAL per-village infrastructure, connectivity and deprivation data
from the Census 2011 District Census Handbook (Village Amenities schedule)
into the `village_amenities` table.

WHY THIS EXISTS
---------------
`load_demographic_data.py` already fetches these exact CSVs but reads a single
column out of them (`Total.Population.of.Village`). The same files carry 280+
columns describing what infrastructure each village actually has. Until this
loader ran, the Priority Engine's `infra_deficit` term was derived from the
severity a *citizen asserted* -- it measured what people said, not what exists.
This is the loader that replaces that guess with the government's own record.

See REAL_DATA_RESEARCH.md §2.3 for the full column inventory and §1 for how
each field feeds a scoring term.

ENCODING (verified against the live files, not assumed)
------------------------------------------------------
    Status columns : "1" = available, "2" = not available, "NA"/"" = unknown
    Numbers columns: integer count, "NA" = unknown
    Distance/hours : integer

Unknown is stored as NULL, never as 0. "Not recorded" and "not present" are
different facts, and collapsing them would silently invent deficits -- exactly
the failure mode research report §16.1 warns about.

DELIBERATE OMISSION
-------------------
The source also carries Scheduled Caste / Scheduled Tribe population counts.
They are NOT loaded, by explicit decision: the vulnerability term is built from
facility deprivation and isolation only. See REAL_DATA_RESEARCH.md §2.3.

CURRENCY WARNING
----------------
This is 2011 data. A road built in 2016 still reads as absent here. The
`pmgsy_road_category` column (populated by load_pmgsy_infrastructure.py from
the 2022 PMGSY network) is the freshness cross-check; where the two disagree,
that disagreement is itself signal and must surface rather than be hidden.
"""

import csv
import io
import sys

import requests
from rapidfuzz import fuzz, process

from database import SessionLocal
from models import Gazetteer, VillageAmenities

HEADERS = {"User-Agent": "ComplainBox-Hackathon/1.0 (village amenities loader)"}

# Same source as load_demographic_data.py -- a GitHub mirror of the District
# Census Handbook. Kept identical so both loaders match against the same rows.
CENSUS_CSV_URLS = {
    "Kolhapur": (
        "https://raw.githubusercontent.com/bnamita/Village_Mapping_v2/master/"
        "data/csvdata/census_split_by_district/530_Kolhapur.csv"
    ),
    "Nashik": (
        "https://raw.githubusercontent.com/bnamita/Village_Mapping_v2/master/"
        "data/csvdata/census_split_by_district/516_Nasik.csv"
    ),
}

# Same threshold as load_demographic_data.py, so a village that got a
# population there gets its amenities here -- the two must not disagree about
# which census row a gazetteer village is.
MATCH_THRESHOLD = 85

# Model field -> a distinctive substring of the census column name.
# Substrings rather than exact names because the two district files are not
# byte-identical in their headers, and the real names are unreadable
# ("Primary.Heallth.Sub.Centre..Numbers." -- the typo is in the source data).
STATUS_FIELDS = {
    "road_black_topped": "Black.Topped",
    "road_all_weather": "All.Weather.Road..Status",
    "road_gravel": "Gravel..kuchha",
    "road_national_highway": "National.Highway..Status",
    "road_state_highway": "State.Highway..Status",
    "water_tap_treated": "Tap.Water.Treated..Status",
    "water_tap_treated_all_year": "Tap.Water.Treated.Functioning.All.round",
    "water_tap_treated_summer": "Tap.Water.Treated.Functioning.in.Summer",
    "water_tap_untreated": "Tap.Water.Untreated..Status",
    "water_handpump": "Hand.Pump..Status",
    "water_well_covered": "Covered.Well..Status",
    "school_primary": "Govt.Primary.School..Status",
    "school_middle": "Govt.Middle..School..Status",
    "school_secondary": "Govt.Secondary.School..Status",
    "conn_mobile_coverage": "Mobile.Phone.Coverage",
    "conn_internet_csc": "Internet.Cafes",
    "conn_telephone": "Telephone..landlines",
    "conn_bus_public": "Public.Bus.Service",
    "drainage_none": "No..Drainage..Status",
}

COUNT_FIELDS = {
    "households": "Total...Households",
    "health_phc_count": "Primary.Health.Centre..Numbers.",
    "health_chc_count": "Community.Health.Centre..Numbers.",
    "health_subcentre_count": "Primary.Heallth.Sub.Centre..Numbers.",
    "health_doctors_sanctioned": "Primary.Health.Centre..Doctors.Total.Strength",
    "health_doctors_in_position": "Centre.Doctors.In.Position",
}

FLOAT_FIELDS = {
    "dist_subdistrict_hq_km": "Sub.District.Head.Quarter..Distance.in.km.",
    "dist_district_hq_km": "District.Head.Quarter...Distance.in.km.",
    "dist_nearest_town_km": "Nearest.Town.Distance.from.Village",
    "power_domestic_summer_hrs": "Power.Supply.For.Domestic.Use.Summer",
    "power_domestic_winter_hrs": "Power.Supply.For.Domestic.Use.Winter",
}

# Facility whose "nearest facility distance band" we want. The band lives in
# the "X.If.not.available..." column that FOLLOWS the facility's own columns,
# so it is resolved positionally rather than by name -- every one of those
# columns has the same 200-character name distinguished only by a numeric
# suffix, which is far more fragile to match on than position.
BAND_AFTER = {
    "school_primary_nearest_band": "Govt.Primary.School..Status",
    "health_nearest_band": "Primary.Heallth.Sub.Centre..Numbers.",
}

BAND_MARKER = "If.not.available"


def resolve_column(headers: list[str], needle: str) -> str | None:
    """First header containing `needle`. Returns None if absent."""
    for h in headers:
        if needle.lower() in h.lower():
            return h
    return None


def resolve_band_column(headers: list[str], anchor_needle: str) -> str | None:
    """
    The distance-band column belonging to a facility: the first
    "X.If.not.available..." column at or after that facility's own column.
    """
    anchor = resolve_column(headers, anchor_needle)
    if anchor is None:
        return None
    start = headers.index(anchor)
    for h in headers[start:]:
        if BAND_MARKER.lower() in h.lower():
            return h
    return None


def parse_status(raw: str | None) -> int | None:
    """Census status: "1" -> 1 (available), "2" -> 0 (absent), else None."""
    value = (raw or "").strip()
    if value == "1":
        return 1
    if value == "2":
        return 0
    return None


def parse_int(raw: str | None) -> int | None:
    value = (raw or "").strip()
    if not value or not value.lstrip("-").isdigit():
        return None
    return int(value)


def parse_float(raw: str | None) -> float | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_band(raw: str | None) -> str | None:
    """Distance band: a = <5km, b = 5-10km, c = >10km."""
    value = (raw or "").strip().lower()
    return value if value in {"a", "b", "c"} else None


def fetch_census_rows(district: str) -> tuple[list[dict], list[str]]:
    """Downloads one district's census CSV. Returns (rows, header names)."""
    response = requests.get(CENSUS_CSV_URLS[district], headers=HEADERS, timeout=60)
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.text))
    rows = list(reader)
    headers = list(rows[0].keys()) if rows else []
    return rows, headers


def build_amenity_payload(row: dict, columns: dict) -> dict:
    """Turns one census row into VillageAmenities kwargs."""
    payload: dict = {}

    for field, column in columns["status"].items():
        payload[field] = parse_status(row.get(column)) if column else None
    for field, column in columns["count"].items():
        payload[field] = parse_int(row.get(column)) if column else None
    for field, column in columns["float"].items():
        payload[field] = parse_float(row.get(column)) if column else None
    for field, column in columns["band"].items():
        payload[field] = parse_band(row.get(column)) if column else None

    return payload


def main() -> int:
    db = SessionLocal()
    total_matched = 0
    total_unmatched = 0

    try:
        for district in CENSUS_CSV_URLS:
            print(f"\n=== {district} ===")
            try:
                rows, headers = fetch_census_rows(district)
            except Exception as exc:
                print(f"  FAILED to fetch census data: {exc}")
                continue
            print(f"  census rows: {len(rows)}  ({len(headers)} columns)")

            columns = {
                "status": {f: resolve_column(headers, n) for f, n in STATUS_FIELDS.items()},
                "count": {f: resolve_column(headers, n) for f, n in COUNT_FIELDS.items()},
                "float": {f: resolve_column(headers, n) for f, n in FLOAT_FIELDS.items()},
                "band": {f: resolve_band_column(headers, n) for f, n in BAND_AFTER.items()},
            }

            missing = [
                f
                for group in columns.values()
                for f, col in group.items()
                if col is None
            ]
            if missing:
                print(f"  WARNING: no census column resolved for: {', '.join(missing)}")

            name_column = resolve_column(headers, "Village.Name")
            if name_column is None:
                print("  FAILED: no village-name column found")
                continue

            census_names = [(r.get(name_column) or "").strip() for r in rows]
            gazetteer_rows = db.query(Gazetteer).filter(Gazetteer.district == district).all()
            print(f"  gazetteer rows to match: {len(gazetteer_rows)}")

            matched = 0
            unmatched = 0

            for gaz in gazetteer_rows:
                result = process.extractOne(gaz.name, census_names, scorer=fuzz.WRatio)
                if result is None or result[1] < MATCH_THRESHOLD:
                    unmatched += 1
                    continue

                _match_name, score, index = result
                payload = build_amenity_payload(rows[index], columns)

                existing = (
                    db.query(VillageAmenities)
                    .filter(VillageAmenities.gazetteer_id == gaz.id)
                    .one_or_none()
                )
                if existing is None:
                    existing = VillageAmenities(gazetteer_id=gaz.id)
                    db.add(existing)

                existing.census_village_name = census_names[index]
                existing.match_score = float(score)
                existing.source = "census_2011"
                for field, value in payload.items():
                    setattr(existing, field, value)

                matched += 1

            db.commit()
            print(f"  matched: {matched}   unmatched: {unmatched}")
            total_matched += matched
            total_unmatched += unmatched

        print("\n=== Summary ===")
        print(f"  villages with real amenity data: {total_matched}")
        print(f"  villages with no confident match: {total_unmatched}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
