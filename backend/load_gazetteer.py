"""
Populates the gazetteer table with real village/town data for Kolhapur and
Nashik districts (Maharashtra, India).

Primary source: OpenStreetMap Overpass API (free, no API key) — queries all
place=village / place=town nodes inside each district's administrative
boundary. If that fails or is rate-limited, falls back to a small
hand-curated list of real, well-known villages/towns per district so the
script never leaves you blocked.
"""

import requests

from database import SessionLocal
from models import Gazetteer

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DISTRICTS = ["Kolhapur", "Nashik"]
MIN_ACCEPTABLE_RESULTS = 5

# Real places, used only if the Overpass API call fails or returns too little.
FALLBACK_VILLAGES = {
    "Kolhapur": [
        ("Kolhapur", 16.7050, 74.2433),
        ("Ichalkaranji", 16.6910, 74.4600),
        ("Jaysingpur", 16.7833, 74.5667),
        ("Kagal", 16.5833, 74.3167),
        ("Gadhinglaj", 16.2333, 74.3500),
        ("Ajra", 16.1167, 74.2167),
        ("Hatkanangale", 16.7667, 74.4667),
        ("Shirol", 16.7167, 74.6167),
        ("Kurundwad", 16.7333, 74.6167),
        ("Panhala", 16.8100, 74.1100),
        ("Gaganbawada", 16.7833, 73.9500),
        ("Radhanagari", 16.4333, 73.9500),
        ("Chandgad", 15.9667, 74.2667),
        ("Gargoti", 16.3833, 74.1167),
        ("Malkapur", 16.9333, 73.9333),
        ("Murgud", 16.3000, 74.2167),
        ("Peth Vadgaon", 16.6167, 74.2833),
        ("Nesari", 16.1333, 74.2833),
        ("Kodoli", 16.6167, 74.1333),
        ("Vadgaon", 16.7500, 74.2000),
    ],
    "Nashik": [
        ("Nashik", 20.0059, 73.7910),
        ("Malegaon", 20.5579, 74.5288),
        ("Sinnar", 19.8500, 74.0000),
        ("Igatpuri", 19.6967, 73.5628),
        ("Yeola", 20.0400, 74.4900),
        ("Manmad", 20.2500, 74.4381),
        ("Nandgaon", 20.3061, 74.6522),
        ("Chandwad", 20.3286, 74.2472),
        ("Dindori", 20.2000, 73.8333),
        ("Kalwan", 20.4667, 73.9500),
        ("Deola", 20.5833, 74.0333),
        ("Satana", 20.5833, 74.2000),
        ("Peint", 20.1833, 73.3833),
        ("Trimbak", 19.9319, 73.5286),
        ("Niphad", 20.0833, 74.1000),
        ("Lasalgaon", 20.1500, 74.2333),
        ("Surgana", 20.6167, 73.6167),
        ("Ozar", 20.1000, 73.9167),
        ("Vani", 20.0500, 73.9333),
        ("Ghoti", 19.7667, 73.7667),
    ],
}


def fetch_from_overpass(district: str) -> list[dict]:
    query = f"""
    [out:json][timeout:25];
    area["name"="{district}"]["boundary"="administrative"]["admin_level"~"5|6"]->.a;
    node["place"~"^(village|town)$"](area.a);
    out body;
    """
    resp = requests.post(
        OVERPASS_URL,
        data={"data": query},
        headers={"User-Agent": "ComplainBox-Hackathon/1.0 (gazetteer loader)"},
        timeout=30,
    )
    resp.raise_for_status()
    elements = resp.json().get("elements", [])

    rows = []
    for el in elements:
        name = el.get("tags", {}).get("name")
        if not name or "lat" not in el or "lon" not in el:
            continue
        population = el.get("tags", {}).get("population")
        rows.append(
            {
                "name": name,
                "admin_level": "village",
                "district": district,
                "block": None,
                "population": int(population) if population and population.isdigit() else None,
                "latitude": el["lat"],
                "longitude": el["lon"],
            }
        )
    return rows


def fallback_rows(district: str) -> list[dict]:
    return [
        {
            "name": name,
            "admin_level": "village",
            "district": district,
            "block": None,
            "population": None,
            "latitude": lat,
            "longitude": lon,
        }
        for name, lat, lon in FALLBACK_VILLAGES[district]
    ]


def load_district(district: str) -> list[dict]:
    try:
        rows = fetch_from_overpass(district)
        if len(rows) < MIN_ACCEPTABLE_RESULTS:
            raise ValueError(f"only {len(rows)} results, too few")
        print(f"  {district}: {len(rows)} places from OpenStreetMap (Overpass API)")
        return rows
    except Exception as exc:
        print(f"  {district}: Overpass fetch failed ({exc}) — using curated fallback list")
        return fallback_rows(district)


def main():
    db = SessionLocal()
    total_inserted = 0

    print("Fetching gazetteer data...")
    for district in DISTRICTS:
        rows = load_district(district)

        # re-runnable: clear any previous rows for this district first
        db.query(Gazetteer).filter(Gazetteer.district == district).delete()

        db.add_all(Gazetteer(**row) for row in rows)
        db.commit()
        total_inserted += len(rows)

    print(f"\nInserted {total_inserted} rows into gazetteer.\n")

    print("Sample rows:")
    for row in db.query(Gazetteer).limit(5).all():
        print(
            f"  id={row.id} name={row.name!r} district={row.district} "
            f"lat={row.latitude} lon={row.longitude} population={row.population}"
        )

    db.close()


if __name__ == "__main__":
    main()
