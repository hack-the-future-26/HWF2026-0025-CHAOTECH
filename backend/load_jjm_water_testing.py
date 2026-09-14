import argparse
import base64
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from rapidfuzz import fuzz, process
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from database import SessionLocal, engine
from models import Base, Gazetteer, WaterTesting

KEY = IV = b"8080808080808080"
def enc(v):
    return base64.b64encode(AES.new(KEY, AES.MODE_CBC, IV).encrypt(pad(str(v).encode(), 16))).decode()

BASE = "https://ejalshakti.gov.in/WQMIS"
JJM_STATE_CODE = 18
JJM_DISTRICTS = {"Kolhapur": 285, "Nashik": 277}

MATCH_THRESHOLD = 88
REQUEST_DELAY_SECONDS = 0.1
CACHE_PATH = Path(__file__).resolve().parent / "jjm_water_testing_cache.json"

BROWSER_HEADERS = {
    "User-Agent": "AwaazIQ/1.0 (+contact)",
}

def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")

def main() -> int:
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    
    page = session.get(f"{BASE}/Report/rpt_ftk_testing_village", timeout=30)
    token_match = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]*)"', page.text)
    if not token_match:
        print("Failed to get token")
        return 1
    token = token_match.group(1)

    def common(path, **data):
        return session.post(f"{BASE}/Common/{path}", data={"__RequestVerificationToken": token, **data},
                      headers={"X-Requested-With": "XMLHttpRequest"}, timeout=25).json()

    cache = load_cache()
    db = SessionLocal()
    total_written = 0

    try:
        for district, district_code in JJM_DISTRICTS.items():
            print(f"\n=== {district} (JJM district code {district_code}) ===")
            
            # Fetch blocks
            try:
                blocks_resp = common("Block_Bind_without_session", state_id=enc(JJM_STATE_CODE), District_id=enc(district_code))
                blocks = [b for b in blocks_resp if b.get('BlockName') and b['BlockName'] != '--Select--']
            except Exception as exc:
                print(f"  FAILED to fetch blocks: {exc}")
                continue
            
            print(f"  JJM blocks listed: {len(blocks)}")

            our_villages = db.query(Gazetteer).filter(Gazetteer.district == district).all()
            our_names = {v.id: v.name for v in our_villages}
            
            district_written = 0
            
            for block in blocks:
                block_id = block['JJM_BlockId']
                block_name = block['BlockName']
                
                try:
                    gps_resp = common("GetGramPanchayat_Bind_without_session", state_id=enc(JJM_STATE_CODE), District_id=enc(district_code), Block_id=enc(block_id))
                    gps = [g for g in gps_resp if g.get('PanchayatName') and g['PanchayatName'] != '--Select--']
                except Exception as exc:
                    print(f"  FAILED to fetch GPs for {block_name}: {exc}")
                    continue
                
                for gp in gps:
                    gp_id = gp['JJM_PanchayatId']
                    gp_name = gp['PanchayatName']
                    
                    cache_key = f"{district_code}:{block_id}:{gp_id}"
                    if cache_key in cache:
                        rows = cache[cache_key]
                    else:
                        try:
                            time.sleep(REQUEST_DELAY_SECONDS)
                            payload = {
                                "__RequestVerificationToken": token, "draw": 1, "start": 0, "length": 1000,
                                "finY": enc("2026-2027"), "stid": enc(JJM_STATE_CODE), "dtid": enc(district_code), "blid": enc(block_id),
                                "gpid": enc(gp_id), "villid": enc(0),
                                "type_hgjv": enc(0), "type_ftkLab": enc(0), "type_household": enc(0)
                            }
                            resp = session.post(f"{BASE}/Report/Get_rpt_ftk_testing_village", headers={"X-Requested-With": "XMLHttpRequest"}, data=payload)
                            rows = resp.json().get("data", [])
                            cache[cache_key] = rows
                            save_cache(cache)
                        except Exception as exc:
                            print(f"  FAILED to fetch rows for GP {gp_name}: {exc}")
                            continue
                            
                    for row in rows:
                        if not row.get("VillageName") or row["VillageName"] == "--Select--":
                            continue
                            
                        v_name = row["VillageName"]
                        
                        # Fuzzy match to gazetteer
                        gaz_id = None
                        if our_names:
                            result = process.extractOne(v_name, our_names, scorer=fuzz.WRatio)
                            if result and result[1] >= MATCH_THRESHOLD:
                                gaz_id = result[2]
                                
                        # Insert/Update
                        existing = db.query(WaterTesting).filter_by(
                            fin_year="2026-2027", 
                            district=district,
                            village_id=row["VillageId"]
                        ).first()
                        
                        if not existing:
                            existing = WaterTesting(
                                fin_year="2026-2027",
                                district=district,
                                village_id=row["VillageId"]
                            )
                            db.add(existing)
                            
                        existing.block = block_name
                        existing.gp_id = row["GramPanchayatId"]
                        existing.gp_name = row.get("PanchayatName") or gp_name
                        existing.village_name = v_name
                        existing.gazetteer_id = gaz_id
                        existing.samples_tested = row.get("Nos_of_Sample_tested", 0)
                        existing.ph = row.get("pH", 0)
                        existing.frc = row.get("FRC", 0)
                        existing.turbidity = row.get("Turbidity", 0)
                        existing.tds = row.get("TDS", 0)
                        existing.hardness = row.get("TotalHardness", 0)
                        existing.villages_not_tested = row.get("Nos_of_Villages_FTK_testing_not_carried", 0)
                        existing.fetched_at = datetime.now(timezone.utc)
                        
                        district_written += 1
                        
            db.commit()
            total_written += district_written
            print(f"  Villages populated for {district}: {district_written}")

        print("\n=== Summary ===")
        print(f"  villages with current JJM water testing data: {total_written}")
        return 0
    finally:
        db.close()

if __name__ == "__main__":
    sys.exit(main())
