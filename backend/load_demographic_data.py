"""
Loads real Census 2011 village-level population figures for Kolhapur and
Nashik districts and matches them onto villages already sitting in the
gazetteer table (fuzzy name match, since gazetteer names come from
OpenStreetMap and won't spell everything identically to the census).

Data source: District Census Handbook (Census of India, 2011) village
amenities data, pre-split per district and hosted as CSV at
https://github.com/bnamita/Village_Mapping_v2 (data/csvdata/census_split_by_district/).

This is the same underlying dataset data.gov.in serves as "Village/Town-wise
Primary Census Abstract, 2011 - MAHARASHTRA" (resource id
2e03171c-d6df-436d-b40c-38e0078b0c66) -- but that API requires a personal,
free data.gov.in API key; the public demo key documented in data.gov.in's
own API docs was tested against this resource and rejected ("Key not
authorised"). Using this GitHub-hosted CSV mirror of the same handbook data
avoids that registration step, at the cost of trusting a third-party mirror
instead of the government API directly. If you'd rather use your own
data.gov.in key, swap fetch_census_villages() to hit
https://api.data.gov.in/resource/2e03171c-d6df-436d-b40c-38e0078b0c66
instead.

PMGSY (Pradhan Mantri Gram Sadak Yojana) road data: SKIPPED, per the
"don't get blocked" instruction. PMGSY's own data portal (omms.nic.in /
pmgsy.nic.in) doesn't expose a simple bulk CSV/API and would need real
scraping work to get right. Note: this same Census Handbook source *does*
include basic road-connectivity flags per village (Black Topped/Gravel/All
Weather Road status), which could serve as a rough infrastructure signal
later -- but the gazetteer table has no column to hold that today, so
adding it here would mean an undiscussed schema change. Left out.
"""

import csv
import io

import requests
from rapidfuzz import fuzz, process

from database import SessionLocal
from models import Gazetteer

HEADERS = {"User-Agent": "ComplainBox-Hackathon/1.0 (demographic data loader)"}

# Our gazetteer's district value -> the census CSV covering it. Note the
# source file is named "Nasik" (official census spelling) but its internal
# District.Name column reads "Nashik", matching our gazetteer exactly.
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

# Village names should match closely (same place, different transliteration
# at most) -- this is a tighter bar than geocode.py's free-text location
# matching, so a stricter threshold is appropriate here.
MATCH_THRESHOLD = 85


def fetch_census_villages(district: str) -> list[dict]:
    """Downloads and parses the census CSV for one district into a list of
    {"name": ..., "population": ...} dicts. Rows with a non-numeric
    population (e.g. "NA" for uninhabited entries) are skipped."""
    url = CENSUS_CSV_URLS[district]
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    reader = csv.DictReader(io.StringIO(resp.text))
    villages = []
    for row in reader:
        name = (row.get("Village.Name") or "").strip()
        pop_raw = (row.get("Total.Population.of.Village") or "").strip()
        if not name or not pop_raw.isdigit():
            continue
        villages.append({"name": name, "population": int(pop_raw)})
    return villages


def match_population(gazetteer_name: str, census_villages: list[dict]) -> int | None:
    if not census_villages:
        return None

    names = [v["name"] for v in census_villages]
    result = process.extractOne(gazetteer_name, names, scorer=fuzz.WRatio)
    if result is None:
        return None

    _match, score, idx = result
    if score < MATCH_THRESHOLD:
        return None

    return census_villages[idx]["population"]


def main():
    db = SessionLocal()

    matched = 0
    unmatched = 0

    for district in CENSUS_CSV_URLS:
        print(f"Fetching census data for {district}...")
        try:
            census_villages = fetch_census_villages(district)
        except Exception as exc:
            print(f"  failed to fetch census data for {district}: {exc}")
            census_villages = []

        print(f"  {len(census_villages)} villages found in census data")

        gazetteer_rows = (
            db.query(Gazetteer).filter(Gazetteer.district == district).all()
        )
        print(f"  {len(gazetteer_rows)} gazetteer rows to match")

        for row in gazetteer_rows:
            population = match_population(row.name, census_villages)
            if population is not None:
                row.population = population
                matched += 1
            else:
                unmatched += 1

        db.commit()
        print()

    total = matched + unmatched
    print("=== Summary ===")
    print(f"Total gazetteer rows processed: {total}")
    print(f"  matched to a real census population: {matched}")
    print(f"  still missing/estimated (no confident match): {unmatched}")

    db.close()


if __name__ == "__main__":
    main()
