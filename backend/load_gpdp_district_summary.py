"""
Loads district-level GPDP (Gram Panchayat Development Plan) planning totals
from eGramSwaraj's public dashboard (index.do) for Kolhapur and Nashik.

WHY THIS LOADER EXISTS
----------------------
Pulls macro district-level investment context directly from the public
eGramSwaraj dashboard without requiring CAPTCHA or login.

SOURCE
------
Portal: eGramSwaraj (Ministry of Panchayati Raj, Government of India)
Endpoint: https://egramswaraj.gov.in/index.do?year={year}&stateCode=27&districtCode={code}&blockCode=0

Run:
    python backend/load_gpdp_district_summary.py [--dry-run]
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from database import SessionLocal, engine  # noqa: E402
from models import Base, GpdpDistrictSummary  # noqa: E402

STATE_CODE = 27  # Maharashtra
DISTRICTS = {"Kolhapur": 438, "Nashik": 443}
PLAN_YEARS = {"2025": "2025-26", "2026": "2026-27"}

BASE_URL = "https://egramswaraj.gov.in/index.do?year={year}&stateCode={state}&districtCode={district}&blockCode=0"
HEADERS = {
    "User-Agent": "AwaazIQ-Research/1.0 (Civic Data Prioritisation; Contact: research@awaaziq.org)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _parse_int(text: str | None) -> int | None:
    if not text:
        return None
    cleaned = re.sub(r"[^\d]", "", str(text))
    return int(cleaned) if cleaned else None


def _parse_float(text: str | None) -> float | None:
    if not text:
        return None
    cleaned = re.sub(r"[^\d.]", "", str(text))
    try:
        return float(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


def fetch_html(url: str, max_retries: int = 3, timeout: float = 25.0) -> str | None:
    """Fetch HTML with retries and exponential backoff."""
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                return None
            if attempt < max_retries:
                time.sleep(attempt * 2.0)
        except Exception:
            if attempt < max_retries:
                time.sleep(attempt * 2.0)
    return None


def parse_page(html: str, requested_district_code: int, print_h4_cards: bool = False) -> dict | None:
    """
    Parse stat cards, activity lists, and freshness string from index.do HTML.
    Returns parsed dict or None if district validation fails.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Sanity check: assert selectedDistrictCode matches requested code
    selected_dist = soup.find("input", id="selectedDistrictCode")
    selected_val = selected_dist.get("value") if selected_dist else None
    if not selected_val or str(selected_val).strip() != str(requested_district_code):
        print(f"  [ERROR] Selected district mismatch: expected {requested_district_code}, got '{selected_val}'")
        return None

    # Freshness date string
    data_as_of = None
    date_el = soup.find(string=re.compile(r"Data as on", re.I))
    if date_el:
        data_as_of = str(date_el).strip().lstrip("*").strip()

    # Stat cards
    h4_tags = soup.find_all("h4")
    if print_h4_cards:
        print("\n" + "=" * 70)
        print("FIRST PAGE FETCHED -- COMPLETE LIST OF <h4> LABELS FOUND:")
        print("=" * 70)
        for idx, h4 in enumerate(h4_tags, 1):
            parent = h4.parent
            cleaned = parent.get_text(" | ", strip=True).replace("\u20b9", "Rs.")
            print(f"  {idx}. <h4> '{h4.get_text(strip=True)}' --> '{cleaned}'")
        print("=" * 70 + "\n")

    stat_cards = {}
    for h4 in h4_tags:
        label = h4.get_text(strip=True)
        parent = h4.parent
        # The stat value is in a sibling or child element of the card
        val_elem = parent.find(class_=re.compile(r"count|number|amount|val", re.I))
        if not val_elem:
            for s in parent.find_all(recursive=False):
                if s != h4 and s.name in ["div", "span", "p", "h2", "h3"]:
                    val_elem = s
                    break
        text_val = val_elem.get_text(strip=True) if val_elem else parent.get_text(" ", strip=True)
        stat_cards[label] = text_val

    total_panchayats = _parse_int(stat_cards.get("Total Panchayats"))
    panchayats_with_plan = _parse_int(stat_cards.get("Total Panchayats Registered Plan"))
    approved_activities = _parse_int(stat_cards.get("Approved Activities"))
    gram_sabhas_conducted = _parse_int(stat_cards.get("Gram Sabhas Conducted"))
    estimated_outlay_lakh = _parse_float(stat_cards.get("Estimated Outlay"))

    # Popular activities
    popular_activities = []
    pop_header = soup.find(lambda t: t.name == "h3" and "popular" in t.get_text().lower())
    if pop_header and pop_header.find_parent("div", class_="panel-header"):
        container = pop_header.find_parent("div", class_="panel-header").parent
        for row in container.find_all("div", class_="activity-row"):
            parts = [p.strip() for p in row.get_text("\n", strip=True).split("\n") if p.strip()]
            if len(parts) >= 3:
                popular_activities.append({"rank": _parse_int(parts[0]), "name": parts[1], "count": _parse_int(parts[2])})
            elif len(parts) == 2:
                popular_activities.append({"rank": None, "name": parts[0], "count": _parse_int(parts[1])})

    # Under-picked activities
    underpicked_activities = []
    under_header = soup.find(lambda t: t.name == "h3" and "under" in t.get_text().lower())
    if under_header and under_header.find_parent("div", class_="panel-header"):
        container = under_header.find_parent("div", class_="panel-header").parent
        for row in container.find_all("div", class_="activity-row"):
            parts = [p.strip() for p in row.get_text("\n", strip=True).split("\n") if p.strip()]
            if len(parts) >= 3:
                underpicked_activities.append({"rank": _parse_int(parts[0]), "name": parts[1], "count": _parse_int(parts[2])})
            elif len(parts) == 2:
                underpicked_activities.append({"rank": None, "name": parts[0], "count": _parse_int(parts[1])})

    # Recent Approved activities
    recent_activities = []
    rec_header = soup.find(lambda t: t.name == "h3" and "recent approved" in t.get_text().lower())
    if rec_header and rec_header.find_parent("div", class_="panel-header"):
        container = rec_header.find_parent("div", class_="panel-header").parent
        for row in container.find_all("div", class_="table-row"):
            parts = [p.strip() for p in row.get_text("\n", strip=True).split("\n") if p.strip()]
            if len(parts) >= 3:
                recent_activities.append({"date": parts[0], "activity": parts[1], "location": parts[2]})
            elif len(parts) == 2:
                recent_activities.append({"date": parts[0], "activity": parts[1], "location": None})

    return {
        "total_panchayats": total_panchayats,
        "panchayats_with_plan": panchayats_with_plan,
        "approved_activities": approved_activities,
        "gram_sabhas_conducted": gram_sabhas_conducted,
        "estimated_outlay_lakh": estimated_outlay_lakh,
        "popular_activities_json": json.dumps(popular_activities, ensure_ascii=False) if popular_activities else None,
        "underpicked_activities_json": json.dumps(underpicked_activities, ensure_ascii=False) if underpicked_activities else None,
        "recent_activities_json": json.dumps(recent_activities, ensure_ascii=False) if recent_activities else None,
        "data_as_of": data_as_of,
    }


def load_gpdp_district_summary(dry_run: bool = False) -> dict:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    total_attempted = 0
    succeeded = 0
    failed = 0
    first_page_printed = False
    results_summary = []

    print("\n" + "=" * 70)
    print("STARTING GPDP DISTRICT SUMMARY LOADER")
    print(f"Districts: {list(DISTRICTS.keys())} | Plan Years: {list(PLAN_YEARS.values())}")
    print("=" * 70)

    try:
        for dist_name, dist_code in DISTRICTS.items():
            for year_param, plan_year in PLAN_YEARS.items():
                total_attempted += 1
                url = BASE_URL.format(year=year_param, state=STATE_CODE, district=dist_code)
                print(f"\nFetching {dist_name} (code {dist_code}) for FY {plan_year}...")
                print(f"  URL: {url}")

                html = fetch_html(url)
                if not html:
                    print(f"  [FAIL] Could not fetch HTML from {url}")
                    failed += 1
                    continue

                parsed = parse_page(html, dist_code, print_h4_cards=not first_page_printed)
                if not parsed:
                    failed += 1
                    continue

                first_page_printed = True

                print(f"  Panchayats: {parsed['total_panchayats']:,} | With plan: {parsed['panchayats_with_plan']:,}")
                print(f"  Approved activities: {parsed['approved_activities']:,}")
                print(f"  Estimated outlay: Rs. {parsed['estimated_outlay_lakh']:,.2f} Lakh")
                print(f"  Data freshness: {parsed['data_as_of']}")

                results_summary.append({
                    "district": dist_name,
                    "district_code": dist_code,
                    "plan_year": plan_year,
                    "panchayats": parsed["total_panchayats"],
                    "approved_activities": parsed["approved_activities"],
                    "outlay_lakh": parsed["estimated_outlay_lakh"],
                })

                if not dry_run:
                    # Upsert based on (district_code, plan_year)
                    existing = (
                        db.query(GpdpDistrictSummary)
                        .filter(
                            GpdpDistrictSummary.district_code == dist_code,
                            GpdpDistrictSummary.plan_year == plan_year,
                        )
                        .first()
                    )

                    now_utc = datetime.now(timezone.utc)
                    if existing:
                        existing.district = dist_name
                        existing.state_code = STATE_CODE
                        existing.total_panchayats = parsed["total_panchayats"]
                        existing.panchayats_with_plan = parsed["panchayats_with_plan"]
                        existing.approved_activities = parsed["approved_activities"]
                        existing.gram_sabhas_conducted = parsed["gram_sabhas_conducted"]
                        existing.estimated_outlay_lakh = parsed["estimated_outlay_lakh"]
                        existing.popular_activities_json = parsed["popular_activities_json"]
                        existing.underpicked_activities_json = parsed["underpicked_activities_json"]
                        existing.recent_activities_json = parsed["recent_activities_json"]
                        existing.data_as_of = parsed["data_as_of"]
                        existing.fetch_failed = False
                        existing.fetched_at = now_utc
                    else:
                        row = GpdpDistrictSummary(
                            district=dist_name,
                            district_code=dist_code,
                            state_code=STATE_CODE,
                            plan_year=plan_year,
                            total_panchayats=parsed["total_panchayats"],
                            panchayats_with_plan=parsed["panchayats_with_plan"],
                            approved_activities=parsed["approved_activities"],
                            gram_sabhas_conducted=parsed["gram_sabhas_conducted"],
                            estimated_outlay_lakh=parsed["estimated_outlay_lakh"],
                            popular_activities_json=parsed["popular_activities_json"],
                            underpicked_activities_json=parsed["underpicked_activities_json"],
                            recent_activities_json=parsed["recent_activities_json"],
                            data_as_of=parsed["data_as_of"],
                            fetch_failed=False,
                            fetched_at=now_utc,
                        )
                        db.add(row)

                    db.commit()
                    print(f"  [OK] Saved to database")

                succeeded += 1
                time.sleep(0.5)

        print("\n" + "=" * 70)
        print("GPDP DISTRICT SUMMARY LOADER FINISHED")
        print("=" * 70)
        print(f"Total attempted: {total_attempted}")
        print(f"Succeeded:       {succeeded}")
        print(f"Failed:          {failed}")
        print("\nPer-district results:")
        for r in results_summary:
            print(f"  {r['district']} (FY {r['plan_year']}): {r['panchayats']} panchayats, "
                  f"{r['approved_activities']:,} activities, Rs. {r['outlay_lakh']:,.2f} Lakh")
        print("=" * 70)

        return {
            "attempted": total_attempted,
            "succeeded": succeeded,
            "failed": failed,
            "rows": results_summary,
        }

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Fetch and load district-level GPDP planning totals.")
    parser.add_argument("--dry-run", action="store_true", help="Print parsed values without saving to database")
    args = parser.parse_args()

    load_gpdp_district_summary(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
