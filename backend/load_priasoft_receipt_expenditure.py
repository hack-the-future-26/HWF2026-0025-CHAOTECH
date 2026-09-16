"""
Loads village-panchayat-level receipts and expenditure from PRIASoft (RecExpReportNew.do)
for Kolhapur and Nashik districts.

WHY THIS LOADER EXISTS
----------------------
Pulls genuine ledger accounts (tied and untied 15th Finance Commission grants:
opening balance, receipts, payments/expenditure, closing balance) directly from
PRIASoft at the individual Village Panchayat level.

SOURCE
------
Portal: eGramSwaraj / PRIASoft (Ministry of Panchayati Raj)
Endpoint: https://egramswaraj.gov.in/recExpVpNew.do?finYear={year}&schemeCode=3287&stateCode=27&districtCode={dist_code}

Run:
    python backend/load_priasoft_receipt_expenditure.py [--dry-run]
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from rapidfuzz import fuzz, process

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from database import SessionLocal, engine  # noqa: E402
from models import Base, Gazetteer, VillagePanchayatFinance  # noqa: E402

STATE_CODE = 27  # Maharashtra
SCHEME_CODE = "3287"  # XV Finance Commission
DISTRICTS = {"Kolhapur": 438, "Nashik": 443}
FIN_YEARS = ["2024-2025", "2025-2026"]
MATCH_THRESHOLD = 88

BASE_URL = "https://egramswaraj.gov.in/recExpVpNew.do?finYear={year}&schemeCode={scheme}&stateCode={state}&districtCode={district}"
HEADERS = {
    "User-Agent": "AwaazIQ-Research/1.0 (Civic Data Prioritisation; Contact: research@awaaziq.org)",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://egramswaraj.gov.in/RecExpReportNew.do?scheme_uid=3287",
}


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def fetch_village_finance(year: str, dist_code: int, max_retries: int = 3) -> list[dict]:
    url = BASE_URL.format(year=year, scheme=SCHEME_CODE, state=STATE_CODE, district=dist_code)
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                if resp.status == 200:
                    text = resp.read().decode("utf-8", errors="replace")
                    data = json.loads(text)
                    if isinstance(data, list):
                        return data
        except Exception as e:
            if attempt < max_retries:
                time.sleep(attempt * 2.0)
            else:
                print(f"  [ERROR] Failed to fetch {url}: {e}")
    return []


def load_priasoft_finance(dry_run: bool = False) -> dict:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    total_fetched = 0
    total_matched = 0
    total_unmatched = 0
    saved_count = 0
    sample_records = []

    print("\n" + "=" * 70)
    print("STARTING PRIASOFT VILLAGE PANCHAYAT FINANCE LOADER")
    print(f"Districts: {list(DISTRICTS.keys())} | Years: {FIN_YEARS} | Scheme: XV FC (3287)")
    print("=" * 70)

    try:
        for dist_name, dist_code in DISTRICTS.items():
            print(f"\n--- Processing {dist_name} (LGD code {dist_code}) ---")

            # Load Gazetteer records for fuzzy matching
            our_villages = db.query(Gazetteer).filter(Gazetteer.district == dist_name).all()
            our_names = {v.id: v.name for v in our_villages}
            print(f"  Loaded {len(our_villages)} villages from Gazetteer for {dist_name}")

            for fin_year in FIN_YEARS:
                print(f"\n  Fetching FY {fin_year} data...")
                rows = fetch_village_finance(fin_year, dist_code)
                print(f"  Received {len(rows)} village panchayat rows from PRIASoft")
                total_fetched += len(rows)

                dist_matched = 0
                dist_unmatched = 0

                for r in rows:
                    v_name = r.get("bpName") or r.get("zpName")
                    if not v_name:
                        continue

                    v_clean = v_name.strip()
                    gaz_id = None

                    # Fuzzy match to Gazetteer
                    if our_names:
                        match = process.extractOne(v_clean, our_names, scorer=fuzz.WRatio)
                        if match and match[1] >= MATCH_THRESHOLD:
                            gaz_id = match[2]
                            dist_matched += 1
                        else:
                            dist_unmatched += 1

                    u_ob = _safe_float(r.get("untiedOb"))
                    u_rec = _safe_float(r.get("untiedReceiptDirect", 0.0) + r.get("untiedReceiptAuto", 0.0))
                    u_pay = _safe_float(r.get("untiedPaymentAmount"))
                    u_ub = _safe_float(r.get("untiedUb"))

                    t_ob = _safe_float(r.get("tiedOb"))
                    t_rec = _safe_float(r.get("tiedReceiptDirect", 0.0) + r.get("tiedReceiptAuto", 0.0))
                    t_pay = _safe_float(r.get("tiedPaymentAmount"))
                    t_ub = _safe_float(r.get("tiedUb"))

                    raw_str = json.dumps(r, ensure_ascii=False)
                    now_utc = datetime.now(timezone.utc)

                    if not dry_run:
                        # Check existing row
                        existing = (
                            db.query(VillagePanchayatFinance)
                            .filter_by(
                                district=dist_name,
                                village_name=v_clean,
                                fin_year=fin_year,
                                scheme_code=SCHEME_CODE,
                            )
                            .first()
                        )

                        if existing:
                            existing.gazetteer_id = gaz_id
                            existing.untied_opening_balance = u_ob
                            existing.untied_receipts = u_rec
                            existing.untied_payments = u_pay
                            existing.untied_closing_balance = u_ub
                            existing.tied_opening_balance = t_ob
                            existing.tied_receipts = t_rec
                            existing.tied_payments = t_pay
                            existing.tied_closing_balance = t_ub
                            existing.raw_json = raw_str
                            existing.fetched_at = now_utc
                        else:
                            row_obj = VillagePanchayatFinance(
                                gazetteer_id=gaz_id,
                                village_name=v_clean,
                                district=dist_name,
                                fin_year=fin_year,
                                scheme_code=SCHEME_CODE,
                                untied_opening_balance=u_ob,
                                untied_receipts=u_rec,
                                untied_payments=u_pay,
                                untied_closing_balance=u_ub,
                                tied_opening_balance=t_ob,
                                tied_receipts=t_rec,
                                tied_payments=t_pay,
                                tied_closing_balance=t_ub,
                                raw_json=raw_str,
                                fetched_at=now_utc,
                            )
                            db.add(row_obj)
                        saved_count += 1

                    if gaz_id and len(sample_records) < 5:
                        sample_records.append({
                            "village": v_clean,
                            "district": dist_name,
                            "gazetteer_id": gaz_id,
                            "fin_year": fin_year,
                            "untied_received": u_rec,
                            "untied_spent": u_pay,
                            "tied_received": t_rec,
                            "tied_spent": t_pay,
                        })

                if not dry_run:
                    db.commit()

                total_matched += dist_matched
                total_unmatched += dist_unmatched
                print(f"  Matched to gazetteer: {dist_matched}/{len(rows)} ({dist_matched / len(rows) * 100:.1f}%)")
                time.sleep(1.0)

        print("\n" + "=" * 70)
        print("PRIASOFT FINANCE LOADER FINISHED")
        print("=" * 70)
        print(f"Total rows processed: {total_fetched}")
        print(f"Matched to Gazetteer: {total_matched} ({total_matched / (total_fetched or 1) * 100:.1f}%)")
        print(f"Unmatched (audited):  {total_unmatched}")
        if not dry_run:
            print(f"Saved to database:    {saved_count}")
        print("\nSample matched village records:")
        for s in sample_records:
            print(f"  {s['village']} ({s['district']}, gazetteer_id={s['gazetteer_id']}, FY {s['fin_year']}): "
                  f"Untied rec=Rs. {s['untied_received']:,.0f}, spent=Rs. {s['untied_spent']:,.0f} | "
                  f"Tied rec=Rs. {s['tied_received']:,.0f}, spent=Rs. {s['tied_spent']:,.0f}")
        print("=" * 70)

        return {
            "total_fetched": total_fetched,
            "total_matched": total_matched,
            "total_unmatched": total_unmatched,
            "saved_count": saved_count,
            "samples": sample_records,
        }

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Load PRIASoft village panchayat receipts and expenditure.")
    parser.add_argument("--dry-run", action="store_true", help="Do not save to database")
    args = parser.parse_args()

    load_priasoft_finance(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
