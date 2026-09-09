"""
Crawls CURRENT Jal Jeevan Mission household tap-connection data from the JJM
Citizen Corner into `village_amenities.jjm_*`.

WHY A CRAWLER AND NOT A DOWNLOAD
--------------------------------
The research report recorded JJM as a "browse-only drill-down MIS dashboard"
with "no confirmed bulk export" (§14) and scoped water data out of the MVP on
that basis. That turned out to be wrong -- the same mistake it made about
PMGSY. The Citizen Corner exposes a real JSON endpoint:

    POST VillageInformation.aspx/BindHabitationInfo
    {stcode, dtcode, cat, subcat, param, VillageId}
    -> [{HabitationName, TotalPop, Household, HouseholdConn,
         QualityStatus, QualityContamination}, ...]

`HouseholdConn / Household` is tap-connection coverage -- the exact quantity
JJM's own 55-lpcd Functional Household Tap Connection norm is assessed
against, and the only CURRENT (post-2019) water data in this project. Every
Census water column predates JJM entirely.

There is no bulk endpoint, so village IDs must be discovered first through the
page's ASP.NET postback chain (state -> district -> village list), then each
village queried individually. Hence: a crawler.

CODE SYSTEMS DIFFER -- DO NOT MIX THEM UP
-----------------------------------------
JJM uses its own codes, unrelated to PMGSY's:
    JJM   : Maharashtra 18, Kolhapur 285, Nashik 277
    PMGSY : Maharashtra 21, Kolhapur 306, Nashik 393

BEING A GOOD CITIZEN
--------------------
This is a public government service. Requests are rate-limited, only villages
that actually match our gazetteer are fetched (not all ~2,400), and progress is
cached to disk so a re-run resumes instead of re-hammering the server.

Usage:
    python load_jjm_water.py              # crawl all matched villages
    python load_jjm_water.py --limit 50   # bounded run
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
from rapidfuzz import fuzz, process

from database import SessionLocal
from models import Gazetteer, VillageAmenities

PAGE = "https://ejalshakti.gov.in/jjm/citizen_corner/villageinformation.aspx"
ENDPOINT = (
    "https://ejalshakti.gov.in/jjm/citizen_corner/"
    "VillageInformation.aspx/BindHabitationInfo"
)

JJM_STATE_CODE = "18"  # Maharashtra, in JJM's own numbering
JJM_DISTRICTS = {"Kolhapur": "285", "Nashik": "277"}

MATCH_THRESHOLD = 88
REQUEST_DELAY_SECONDS = 0.4
CACHE_PATH = Path(__file__).resolve().parent / "jjm_cache.json"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ComplainBox-Hackathon/1.0)",
    "Referer": PAGE,
}


def form_fields(html: str) -> tuple[str, str, str]:
    def grab(name: str) -> str:
        match = re.search(rf'id="{name}"[^>]*value="([^"]*)"', html)
        return match.group(1) if match else ""

    return grab("__VIEWSTATE"), grab("__EVENTVALIDATION"), grab("__VIEWSTATEGENERATOR")


def village_list(session: requests.Session, district_code: str) -> list[tuple[str, str]]:
    """
    Walks the postback chain to the village dropdown.
    Returns [(village_id, "Block / Panchayat / Village"), ...].
    """
    html = session.get(PAGE, timeout=90).text
    viewstate, validation, generator = form_fields(html)

    payload = {
        "__EVENTTARGET": "ddState",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": viewstate,
        "__VIEWSTATEGENERATOR": generator,
        "__EVENTVALIDATION": validation,
        "ddState": JJM_STATE_CODE,
    }
    html = session.post(PAGE, data=payload, timeout=120).text
    viewstate, validation, generator = form_fields(html)

    payload = {
        "__EVENTTARGET": "ddDistrict",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": viewstate,
        "__VIEWSTATEGENERATOR": generator,
        "__EVENTVALIDATION": validation,
        "ddState": JJM_STATE_CODE,
        "ddDistrict": district_code,
    }
    html = session.post(PAGE, data=payload, timeout=180).text

    block = re.search(r'id="ddList".*?</select>', html, re.S)
    if not block:
        return []

    options = re.findall(r'value="([^"]+)"[^>]*>([^<]+)<', block.group(0))
    villages = []
    for value, label in options:
        if value == "-1":
            continue
        # value is "block/panchayat/village"; the endpoint wants the last part
        village_id = value.split("/")[-1].strip()
        villages.append((village_id, label.strip()))
    return villages


def village_label_name(label: str) -> str:
    """'Ajara / Ardal / Ardal' -> 'Ardal' (the village, not block/panchayat)."""
    parts = [p.strip() for p in label.split("/")]
    return parts[-1] if parts else label


def fetch_habitations(session: requests.Session, district_code: str, village_id: str):
    body = {
        "stcode": JJM_STATE_CODE,
        "dtcode": district_code,
        "cat": "0",
        "subcat": "0",
        "param": "0",
        "VillageId": village_id,
    }
    response = session.post(
        ENDPOINT,
        data=json.dumps(body),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=90,
    )
    response.raise_for_status()
    return response.json().get("d") or []


def to_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def summarise(habitations: list[dict]) -> dict | None:
    """
    Rolls a village's habitations into one record.

    Coverage is computed over the summed households, not as a mean of
    per-habitation percentages -- otherwise a 10-household hamlet would weigh
    the same as a 900-household village.
    """
    households = 0
    with_tap = 0
    quality: list[str] = []

    for habitation in habitations:
        total = to_int(habitation.get("Household"))
        connected = to_int(habitation.get("HouseholdConn"))
        if total:
            households += total
        if connected:
            with_tap += connected
        status = (habitation.get("QualityStatus") or "").strip()
        if status and status.lower() != "none":
            quality.append(status)

    if households <= 0:
        return None

    return {
        "jjm_households": households,
        "jjm_households_with_tap": with_tap,
        "jjm_tap_coverage_pct": round(100.0 * with_tap / households, 2),
        "jjm_habitation_count": len(habitations),
        "jjm_quality_status": "; ".join(sorted(set(quality))) or None,
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="max villages to fetch per district (for a bounded run)")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)

    cache = load_cache()
    db = SessionLocal()
    total_written = 0

    try:
        for district, district_code in JJM_DISTRICTS.items():
            print(f"\n=== {district} (JJM district code {district_code}) ===")
            try:
                villages = village_list(session, district_code)
            except Exception as exc:
                print(f"  FAILED to fetch village list: {exc}")
                continue
            print(f"  JJM villages listed: {len(villages)}")
            if not villages:
                continue

            pairs = (
                db.query(Gazetteer, VillageAmenities)
                .join(VillageAmenities, VillageAmenities.gazetteer_id == Gazetteer.id)
                .filter(Gazetteer.district == district)
                .all()
            )
            print(f"  our villages to match: {len(pairs)}")

            jjm_names = [village_label_name(label) for _vid, label in villages]

            planned: list[tuple] = []
            for gaz, amenities in pairs:
                result = process.extractOne(gaz.name, jjm_names, scorer=fuzz.WRatio)
                if result is None or result[1] < MATCH_THRESHOLD:
                    continue
                _name, score, index = result
                planned.append((amenities, villages[index][0], jjm_names[index], score))

            if args.limit:
                planned = planned[: args.limit]
            print(f"  matched and queued for fetch: {len(planned)}")

            written = 0
            failed = 0
            for position, (amenities, village_id, jjm_name, score) in enumerate(planned, 1):
                cache_key = f"{district_code}:{village_id}"
                if cache_key in cache:
                    habitations = cache[cache_key]
                else:
                    try:
                        habitations = fetch_habitations(session, district_code, village_id)
                        cache[cache_key] = habitations
                    except Exception as exc:
                        failed += 1
                        if failed <= 3:
                            print(f"    fetch failed for {jjm_name}: {exc}")
                        continue
                    time.sleep(REQUEST_DELAY_SECONDS)

                summary = summarise(habitations)
                if not summary:
                    continue
                for field, value in summary.items():
                    setattr(amenities, field, value)
                written += 1

                if position % 50 == 0:
                    db.commit()
                    save_cache(cache)
                    print(f"    ...{position}/{len(planned)} fetched")

            db.commit()
            save_cache(cache)
            print(f"  villages given JJM data: {written}   fetch failures: {failed}")
            total_written += written

        print("\n=== Summary ===")
        print(f"  villages with current JJM tap-coverage data: {total_written}")
        print(f"  response cache: {CACHE_PATH.name} (re-runs resume from it)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
