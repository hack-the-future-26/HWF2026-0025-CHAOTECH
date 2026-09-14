"""
Loads current (2024-25) school condition, staffing and grant data from UDISE+ Know Your School portal.

WHY THIS LOADER EXISTS
----------------------
Previously, village education deficits relied on static 2011 Census proxies
(e.g., share of villages lacking a secondary school). This loader replaces those
coarse proxies with live, per-school 2024-25 UDISE+ records for all 9,352 schools
in Kolhapur and Nashik districts:
- Teacher staffing (regular, contractual, part-time)
- Physical infrastructure (total, good, minor repair, major repair classrooms)
- Basic amenities (functional boys/girls toilets, drinking water, electricity, boundary wall)
- School grants & expenditure (Composite School Grant under Samagra Shiksha)

DATA SOURCE
-----------
Portal: UDISE+ Know Your School (Ministry of Education, Government of India)
Endpoints:
- Report Card: https://kys.udiseplus.gov.in/web-app/api/school/report-card?udiseSchCode=<udise_code>
- Facilities:  https://kys.udiseplus.gov.in/web-app/api/school/facility?udiseSchCode=<udise_code>
Both endpoints return JSON directly when queried with an honest civic research User-Agent.

Run:
    python backend/load_udise_school_data.py [--limit N] [--dry-run]
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from database import SessionLocal, engine  # noqa: E402
from models import Base, PublicFacility, SchoolCondition  # noqa: E402

KYS_REPORT_CARD_URL = "https://kys.udiseplus.gov.in/web-app/api/school/report-card?udiseSchCode={}"
KYS_FACILITY_URL = "https://kys.udiseplus.gov.in/web-app/api/school/facility?udiseSchCode={}"

HEADERS = {
    "User-Agent": "AwaazIQ-Research/1.0 (Civic Data Prioritisation; Contact: research@awaaziq.org)",
    "Accept": "application/json",
    "Referer": "https://kys.udiseplus.gov.in/",
}

TARGET_YEAR = "2024-25"


def _safe_int(val) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _safe_float(val) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_bool(val) -> bool | None:
    if val is None or val == "":
        return None
    try:
        return int(val) == 1
    except (ValueError, TypeError):
        return None


def fetch_json(url: str, max_retries: int = 3, timeout: float = 12.0) -> dict | None:
    """Fetch JSON from a URL with retries and exponential backoff."""
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    body = resp.read().decode("utf-8", errors="replace")
                    parsed = json.loads(body)
                    if isinstance(parsed, dict) and parsed.get("status") is True:
                        return parsed.get("data")
                    elif isinstance(parsed, dict) and "data" in parsed:
                        return parsed.get("data")
                    return parsed
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                # Permanent failure for this code
                return None
            if attempt < max_retries:
                time.sleep(attempt * 1.5)
        except Exception:
            if attempt < max_retries:
                time.sleep(attempt * 1.5)
    return None


def inspect_first_school(rc_data: dict, fac_data: dict) -> None:
    """Print complete sorted field names and check for student enrolment count."""
    print("=" * 70)
    print("UDISE+ API SCHEMA INSPECTION (First successful response)")
    print("=" * 70)
    rc_keys = sorted(rc_data.keys())
    fac_keys = sorted(fac_data.keys())

    print(f"\n1. Report-Card Endpoint ({len(rc_keys)} fields):")
    print(", ".join(rc_keys))

    print(f"\n2. Facility Endpoint ({len(fac_keys)} fields):")
    print(", ".join(fac_keys))

    enrolment_keys = [
        k for k in list(rc_data.keys()) + list(fac_data.keys())
        if any(w in k.lower() for w in ["enrol", "stud", "pupil"])
    ]
    print("\n3. Student Enrolment Field Check:")
    if enrolment_keys:
        print(f"   Found candidate enrolment fields: {enrolment_keys}")
    else:
        print("   CONFIRMED: Neither endpoint returns student enrolment or pupil counts.")
        print("   (Fields totMale, totFemale, totalTeacher represent teacher headcounts).")
    print("=" * 70)
    print()


def load_udise_data(limit: int | None = None, dry_run: bool = False, batch_size: int = 50, delay_sec: float = 0.55) -> dict:
    """
    Fetch and store UDISE+ school condition data for education facilities.
    """
    start_time = time.time()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        # 1. Query target schools
        query = (
            db.query(PublicFacility.id, PublicFacility.external_id, PublicFacility.name, PublicFacility.village, PublicFacility.district)
            .filter(PublicFacility.source == "udise", PublicFacility.category == "education")
            .order_by(PublicFacility.id.asc())
        )
        all_schools = query.all()
        total_target = len(all_schools)
        print(f"Total UDISE education facilities in database: {total_target}")

        # 2. Check existing records for resumability
        existing_rows = db.query(SchoolCondition.udise_code, SchoolCondition.year_desc).filter(
            SchoolCondition.fetched_at.isnot(None)
        ).all()
        completed_codes = {r[0] for r in existing_rows if r[1] == TARGET_YEAR}
        print(f"Already completed for year {TARGET_YEAR}: {len(completed_codes)}")

        todo = [s for s in all_schools if s.external_id not in completed_codes]
        print(f"Pending to fetch: {len(todo)}")

        if limit is not None:
            todo = todo[:limit]
            print(f"Applying limit: processing next {len(todo)} schools")

        if dry_run:
            print("\n[DRY-RUN] No HTTP requests or database writes will be executed.")
            return {
                "total_target": total_target,
                "already_completed": len(completed_codes),
                "pending": len(todo),
                "attempted": 0,
                "succeeded": 0,
                "failed": 0,
                "elapsed_seconds": round(time.time() - start_time, 1),
            }

        first_school_inspected = False
        succeeded = 0
        failed = 0
        teachers_total = 0
        major_repair_count = 0
        total_grant_sum = 0.0

        print("\nStarting polite crawl (~1.5 requests/sec overall)...")
        print("-" * 70)

        for i, (facility_id, udise_code, sch_name, village, district) in enumerate(todo, start=1):
            rc_url = KYS_REPORT_CARD_URL.format(udise_code)
            fac_url = KYS_FACILITY_URL.format(udise_code)

            rc_data = fetch_json(rc_url)
            time.sleep(delay_sec / 2.0)
            fac_data = fetch_json(fac_url)
            time.sleep(delay_sec / 2.0)

            now_utc = datetime.now(timezone.utc)

            if rc_data or fac_data:
                rc = rc_data or {}
                fac = fac_data or {}

                if not first_school_inspected and rc and fac:
                    inspect_first_school(rc, fac)
                    first_school_inspected = True

                year_desc = rc.get("yearDesc") or TARGET_YEAR
                tch_reg = _safe_int(rc.get("tchReg"))
                tch_cont = _safe_int(rc.get("tchCont"))
                tch_part = _safe_int(rc.get("tchPart"))

                cls_tot = _safe_int(fac.get("clsrmsInst"))
                cls_gd = _safe_int(fac.get("clsrmsGd"))
                cls_min = _safe_int(fac.get("clsrmsMin"))
                cls_maj = _safe_int(fac.get("clsrmsMaj"))

                toilet_b_fun = _safe_int(fac.get("toiletbFun"))
                toilet_g_fun = _safe_int(fac.get("toiletgFun"))
                drinking_water = _safe_bool(fac.get("drinkWaterYn"))
                electricity = _safe_bool(fac.get("electricityYn"))
                bndry_wall = fac.get("bndrywallType")
                if bndry_wall:
                    bndry_wall = str(bndry_wall).strip()

                tot_grant = _safe_float(rc.get("totalGrant"))
                # Note: official UDISE+ spelling is 'totalExpediture' (missing 'n')
                tot_exp = _safe_float(rc.get("totalExpediture")) if rc.get("totalExpediture") is not None else _safe_float(rc.get("totalExpenditure"))

                raw_json = json.dumps({"report_card": rc, "facility": fac}, default=str)

                row = SchoolCondition(
                    facility_id=facility_id,
                    udise_code=udise_code,
                    year_desc=year_desc,
                    teachers_regular=tch_reg,
                    teachers_contract=tch_cont,
                    teachers_part_time=tch_part,
                    classrooms_total=cls_tot,
                    classrooms_good=cls_gd,
                    classrooms_minor_repair=cls_min,
                    classrooms_major_repair=cls_maj,
                    toilet_boys_functional=toilet_b_fun,
                    toilet_girls_functional=toilet_g_fun,
                    drinking_water=drinking_water,
                    electricity=electricity,
                    boundary_wall_status=bndry_wall,
                    total_grant=tot_grant,
                    total_expenditure=tot_exp,
                    raw_json=raw_json,
                    fetch_failed=False,
                    fetched_at=now_utc,
                )
                db.add(row)
                succeeded += 1

                t_sum = (tch_reg or 0) + (tch_cont or 0) + (tch_part or 0)
                teachers_total += t_sum
                if cls_maj and cls_maj > 0:
                    major_repair_count += 1
                if tot_grant:
                    total_grant_sum += tot_grant

            else:
                # Both endpoints failed after retries
                row = SchoolCondition(
                    facility_id=facility_id,
                    udise_code=udise_code,
                    year_desc=TARGET_YEAR,
                    fetch_failed=True,
                    fetched_at=now_utc,
                )
                db.add(row)
                failed += 1

            if i % batch_size == 0 or i == len(todo):
                db.commit()
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                print(f"[{i}/{len(todo)}] committed. OK: {succeeded}, Failed: {failed} ({rate:.2f} schools/sec)")

        db.commit()
        elapsed_total = time.time() - start_time
        avg_teachers = round(teachers_total / succeeded, 2) if succeeded > 0 else 0.0
        pct_major_repair = round((major_repair_count / succeeded) * 100.0, 1) if succeeded > 0 else 0.0

        summary = {
            "total_target": total_target,
            "already_completed": len(completed_codes),
            "attempted": len(todo),
            "succeeded": succeeded,
            "fetch_failed": failed,
            "avg_teachers": avg_teachers,
            "pct_major_repair": pct_major_repair,
            "total_grant_sum": round(total_grant_sum, 2),
            "elapsed_seconds": round(elapsed_total, 1),
        }

        print("\n" + "=" * 70)
        print("UDISE+ LOADER RUN SUMMARY")
        print("=" * 70)
        print(f"Schools attempted in this run:  {summary['attempted']}")
        print(f"Successfully fetched:           {summary['succeeded']}")
        print(f"Fetch failed:                   {summary['fetch_failed']}")
        print(f"Average teachers per school:    {summary['avg_teachers']}")
        print(f"Schools with major-repair rms:  {summary['pct_major_repair']}%")
        print(f"Total grant funding recorded:   Rs {summary['total_grant_sum']:,.2f}")
        print(f"Total time taken:               {summary['elapsed_seconds']:.1f} s")
        print("=" * 70)
        return summary

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Fetch and load UDISE+ school condition data.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of un-fetched schools to process")
    parser.add_argument("--dry-run", action="store_true", help="Print pending counts without fetching")
    parser.add_argument("--batch-size", type=int, default=50, help="Database commit batch size")
    args = parser.parse_args()

    load_udise_data(limit=args.limit, dry_run=args.dry_run, batch_size=args.batch_size)


if __name__ == "__main__":
    main()

