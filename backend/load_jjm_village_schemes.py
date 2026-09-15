"""
Loads real per-scheme JJM water-supply records (scheme identity, cost,
expenditure, status) from ejalshakti.gov.in's village profile report.

WHY THIS LOADER EXISTS
-----------------------
`load_jjm_water.py` already crawls the JJM Citizen Corner's
`VillageInformation.aspx/BindHabitationInfo` JSON endpoint for current
household tap-connection coverage -- that's real, current, and already
wired into water's infra_deficit. It does NOT carry scheme identity, cost,
or work-order status; that data lives on a completely different report:

    GET  JJM/JJMReports/profiles/rpt_VillageProfile.aspx
    POST back with:
        ctl00$CPHPage$ddVillagecodetype = "1"        (search by LGD code)
        ctl00$CPHPage$txtVillagelgdcode = <LGD code>
        ctl00$CPHPage$hidVillageLgd_Code = <LGD code>  (server reads this
            hidden field, not the visible text box -- confirmed live: the
            search silently returns "No record found." if only the text
            field is set)
        __EVENTTARGET = "ctl00$CPHPage$btnShow"

Verified live, three real villages (Ambade, Bachi, and a third LGD code),
each returning a real "List of schemes" table: Scheme Id, Scheme name,
Type, Category, Work order date, Estimated cost (Rs. lakh), Reported
expenditure (Rs. lakh), Status. A fourth code (Ajra, a block HQ town)
correctly returned "No record found." -- a real miss, not a scraper bug.

This is the earlier VillageProfile.aspx dead end's SIBLING report, not the
same page -- that one (`JJM/JJM/Public/Profile/VillageProfile.aspx`) is
FHTC tap-tracking only, confirmed dead for financial data. This one
(`JJM/JJMReports/profiles/rpt_VillageProfile.aspx`) is a different report
under a different path and does carry real scheme financials.

DELIBERATE OMISSION
--------------------
The same page also lists O&M staff names, women's committee members, and
water-quality sample collectors' names (e.g. "SARITA JADHAV"). None of
that is scraped or stored -- only the scheme-level financial/status table,
matching this project's standing rule against storing individual-level
data (REAL_DATA_RESEARCH.md §2.3, load_village_amenities.py's own
DELIBERATE OMISSION section).

BEING A GOOD CITIZEN
---------------------
One GET + one POST per village, rate-limited, only villages already
matched to our gazetteer via `lgd_village` (656 of them, not all 3,174 LGD
rows this project has loaded). Idempotent: clears a village's existing
scheme rows before re-inserting.

Usage:
    python load_jjm_village_schemes.py [--dry-run] [--limit N]
"""

import argparse
import concurrent.futures
import re
import sys
import time
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

import requests
from bs4 import BeautifulSoup

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from database import SessionLocal  # noqa: E402
from models import JjmVillageScheme, LgdVillage  # noqa: E402

SEARCH_URL = "https://ejalshakti.gov.in/JJM/JJMReports/profiles/rpt_VillageProfile.aspx"
HEADERS = {
    "User-Agent": "AwaazIQ-Research/1.0 (Civic Data Prioritisation; Contact: research@awaaziq.org)",
}
REQUEST_DELAY_SECONDS = 1.0


def _extract_hidden_fields(html: str) -> dict:
    fields = {}
    for m in re.finditer(r'<input[^>]*type="hidden"[^>]*>', html):
        tag = m.group(0)
        name_m = re.search(r'name="([^"]+)"', tag)
        value_m = re.search(r'value="([^"]*)"', tag)
        if name_m:
            fields[name_m.group(1)] = value_m.group(1) if value_m else ""
    return fields


def _parse_float(text: str | None) -> float | None:
    if not text:
        return None
    cleaned = re.sub(r"[^\d.]", "", text)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def fetch_village_schemes(session: requests.Session, lgd_code: str) -> tuple[str | None, list[dict]]:
    """
    Returns (jjm_village_id, [scheme_dict, ...]). Empty list, not an
    exception, when the site says "No record found." for this code.
    """
    r1 = session.get(SEARCH_URL, headers=HEADERS, timeout=20)
    r1.raise_for_status()
    fields = _extract_hidden_fields(r1.text)

    payload = dict(fields)
    payload["ctl00$CPHPage$ddVillagecodetype"] = "1"
    payload["ctl00$CPHPage$txtVillagelgdcode"] = lgd_code
    payload["ctl00$CPHPage$hidVillageLgd_Code"] = lgd_code
    payload["__EVENTTARGET"] = "ctl00$CPHPage$btnShow"
    payload["__EVENTARGUMENT"] = ""

    r2 = session.post(SEARCH_URL, headers=HEADERS, data=payload, timeout=20)
    r2.raise_for_status()
    soup = BeautifulSoup(r2.text, "html.parser")

    if soup.find(id="CPHPage_lblerrer"):
        return None, []

    jjm_village_id = None
    village_span = soup.find(id="CPHPage_lblVillage")
    if village_span:
        m = re.search(r"JJM VillageId\s*:\s*(\d+)", village_span.get_text())
        if m:
            jjm_village_id = m.group(1)

    schemes = []
    scheme_id_header = soup.find(lambda tag: tag.name == "th" and "Scheme Id" in tag.get_text())
    if scheme_id_header is None:
        return jjm_village_id, schemes
    table = scheme_id_header.find_parent("table")
    if table is None:
        return jjm_village_id, schemes

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 9:
            continue
        scheme_id_text = cells[1].get_text(strip=True)
        if not scheme_id_text:
            continue
        schemes.append(
            {
                "scheme_id": scheme_id_text,
                "scheme_name": cells[2].get_text(strip=True),
                "scheme_type": cells[3].get_text(strip=True) or None,
                "scheme_category": cells[4].get_text(strip=True) or None,
                "work_order_date": cells[5].get_text(strip=True) or None,
                "estimated_cost_lakh": _parse_float(cells[6].get_text(strip=True)),
                "reported_expenditure_lakh": _parse_float(cells[7].get_text(strip=True)),
                "status": cells[8].get_text(strip=True) or None,
            }
        )
    return jjm_village_id, schemes


def _fetch_one(v, delay_sec: float):
    """
    Runs in a worker thread: its own Session (not shared -- ASP.NET
    viewstate/postback state is per request-flow, and requests.Session
    isn't documented as thread-safe for concurrent use anyway). Pure
    fetch-and-parse, no DB access -- all writes happen back in the main
    thread, the same division of labour load_udise_school_data.py already
    established for parallelizing a scrape against a government site
    safely (SQLite has one writer; only the main thread touches it).
    """
    session = requests.Session()
    try:
        jjm_village_id, schemes = fetch_village_schemes(session, v.lgd_village_code)
        error = None
    except requests.RequestException as exc:
        jjm_village_id, schemes, error = None, [], str(exc)
    time.sleep(delay_sec)
    return v, jjm_village_id, schemes, error


def run(limit: int | None, dry_run: bool, workers: int, resume: bool) -> None:
    db = SessionLocal()
    # Default expire_on_commit=True marks every object this session has
    # loaded as stale after each commit -- including the other LgdVillage
    # rows still sitting in `villages`, waiting their turn in the loop.
    # Reading v.village_name for the NEXT village after any earlier
    # village's commit then triggers a refresh SELECT that (for reasons
    # not fully pinned down, but reproduced live: crashed at iteration 28
    # of 270) raised ObjectDeletedError even though nothing was deleted.
    # This session only ever reads LgdVillage once and writes a different
    # table, so disabling the auto-expire is safe here.
    db.expire_on_commit = False
    try:
        query = db.query(LgdVillage).filter(LgdVillage.gazetteer_id.isnot(None))
        villages = query.all()

        if resume:
            done_ids = {
                row[0] for row in db.query(JjmVillageScheme.gazetteer_id).distinct().all()
            }
            before = len(villages)
            villages = [v for v in villages if v.gazetteer_id not in done_ids]
            print(f"Resuming: {before - len(villages)} villages already loaded, skipping them.")

        if limit:
            villages = villages[:limit]

        total_schemes = 0
        no_record = 0
        errors = 0
        worker = partial(_fetch_one, delay_sec=REQUEST_DELAY_SECONDS)
        print(f"Starting parallel crawl ({workers} workers, {REQUEST_DELAY_SECONDS}s inter-request delay per worker)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for i, (v, jjm_village_id, schemes, error) in enumerate(
                executor.map(worker, villages), start=1
            ):
                if error:
                    errors += 1
                    print(f"[{i}/{len(villages)}] {v.village_name} ({v.lgd_village_code}): ERROR {error}")
                    continue

                if not schemes:
                    no_record += 1
                    print(f"[{i}/{len(villages)}] {v.village_name} ({v.lgd_village_code}): no record")
                else:
                    print(
                        f"[{i}/{len(villages)}] {v.village_name} ({v.lgd_village_code}): "
                        f"{len(schemes)} scheme(s)"
                    )
                    if not dry_run:
                        db.query(JjmVillageScheme).filter(
                            JjmVillageScheme.gazetteer_id == v.gazetteer_id
                        ).delete()
                        now = datetime.now(timezone.utc)
                        for s in schemes:
                            db.add(
                                JjmVillageScheme(
                                    gazetteer_id=v.gazetteer_id,
                                    lgd_village_code=v.lgd_village_code,
                                    jjm_village_id=jjm_village_id,
                                    fetched_at=now,
                                    **s,
                                )
                            )
                        db.commit()
                    total_schemes += len(schemes)

        print(
            f"\nDone. {len(villages)} villages checked, {total_schemes} real schemes found, "
            f"{no_record} with no record, {errors} errors."
        )
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=8, help="Concurrent worker threads (default 8, matching load_udise_school_data.py's precedent)")
    parser.add_argument("--no-resume", dest="resume", action="store_false", help="Re-fetch villages already loaded instead of skipping them")
    args = parser.parse_args()
    run(limit=args.limit, dry_run=args.dry_run, workers=args.workers, resume=args.resume)
