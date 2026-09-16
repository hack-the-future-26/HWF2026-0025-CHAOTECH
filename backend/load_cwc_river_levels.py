import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
import dateutil.parser

import requests
from database import SessionLocal, engine
from models import Base, RiverReading

B = "https://ffs.india-water.gov.in/iam/api"

BOUNDS = {
    "Kolhapur": (15.7, 17.1, 73.7, 74.7),
    "Nashik": (19.6, 20.9, 73.3, 75.0)
}

SEARCH_TERMS = ["KOLHAPUR", "KRISHNA", "PANCHGANGA", "NASHIK", "GODAVARI"]

def spec(field, op, value):
    return json.dumps({"where": {"expression": {"valueIsRelationField": False, "fieldName": field, "operator": op, "value": value}}})

def fetch_stations():
    stations = {}
    for term in SEARCH_TERMS:
        try:
            res = requests.get(f"{B}/layer-station/specification/sorted-page",
                headers={"Accept": "application/json", "class-name": "LayerStationDto"},
                params={"specification": spec("name", "like", term), "sort-criteria": "{}", "page-number": 0, "page-size": 100}).json()
            if isinstance(res, list):
                for s in res:
                    stations[s['stationCode']] = s
            time.sleep(0.1)
        except Exception as e:
            print(f"Error fetching stations for {term}: {e}")
    return list(stations.values())

def fetch_coords(station_code):
    try:
        res = requests.get(f"{B}/layer-station-geo/specification/sorted-page",
            headers={"Accept": "application/json", "class-name": "LayerStationGeoDto"},
            params={"specification": spec("stationCode", "eq", station_code), "sort-criteria": "{}", "page-number": 0, "page-size": 2}).json()
        if isinstance(res, list) and len(res) > 0:
            return res[0].get('lat'), res[0].get('lon')
    except Exception as e:
        pass
    return None, None

def fetch_readings(station_code):
    try:
        sort = json.dumps({"sortOrderDtos": [{"sortDirection": "DESC", "field": "id.dataTime"}]})
        res = requests.get(f"{B}/new-entry-data/specification/sorted-page",
            headers={"Accept": "application/json", "class-name": "NewEntryDataDto"},
            params={"specification": spec("id.stationCode", "eq", station_code), "sort-criteria": sort, "page-number": 0, "page-size": 1}).json()
        if isinstance(res, list) and len(res) > 0:
            return res[0]
    except Exception as e:
        pass
    return None

def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    print("Fetching candidate stations...")
    candidates = fetch_stations()
    print(f"Found {len(candidates)} candidates.")
    
    now = datetime.now(timezone.utc)
    one_week_ago = now - timedelta(days=7)
    
    valid_count = 0
    for st in candidates:
        code = st['stationCode']
        lat, lon = fetch_coords(code)
        time.sleep(0.1)
        if not lat or not lon:
            continue
            
        in_bounds = False
        for dist, b in BOUNDS.items():
            if b[0] <= lat <= b[1] and b[2] <= lon <= b[3]:
                in_bounds = True
                break
        
        if not in_bounds:
            continue
            
        reading = fetch_readings(code)
        time.sleep(0.1)
        if not reading:
            continue
            
        obs_time_str = reading['id']['dataTime']
        try:
            obs_time = dateutil.parser.parse(obs_time_str)
            if obs_time.tzinfo is None:
                obs_time = obs_time.replace(tzinfo=timezone.utc)
        except Exception:
            continue
            
        if obs_time < one_week_ago:
            continue
            
        # It's valid!
        existing = db.query(RiverReading).filter_by(station_code=code).first()
        if not existing:
            existing = RiverReading(station_code=code)
            db.add(existing)
            
        existing.name = st['name']
        existing.lat = lat
        existing.lon = lon
        existing.datatype_code = reading.get('id', {}).get('datatypeCode')
        existing.value = reading['dataValue']
        existing.observed_at = obs_time
        existing.fetched_at = now
        valid_count += 1
        
    db.commit()
    db.close()
    print(f"Stored {valid_count} active river stations in bounds.")

if __name__ == "__main__":
    main()
