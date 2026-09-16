"""
Semi-automated eGramSwaraj GPDP capture tool.

HOW TO USE
----------
1. Run: python backend/capture_gpdp.py
2. A real Chrome window opens on your screen.
3. The script selects State=Maharashtra automatically.
4. YOU must: pick the district, block, panchayat, fill the CAPTCHA, click "Get Report".
5. Press Enter in this terminal once the report table is visible on screen.
6. The script reads the table, saves every work row to the DB, and moves to the next village.
7. You can Ctrl+C any time — progress is saved. Re-running skips already-captured villages.

Villages are processed highest-priority-score first, so partial runs always
capture the most valuable villages first.
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from database import SessionLocal, engine  # noqa: E402
from models import Base, Gazetteer, VillagePriority, VillageBudgetPlan  # noqa: E402

URL = "https://egramswaraj.gov.in/getGPDPReport.do"


def _safe_float(val):
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def get_financial_year(page):
    """Try to read the financial year from the page, else return None."""
    try:
        sel = page.query_selector("select[name='finYear'], select[name='financialYear']")
        if sel:
            return sel.evaluate("el => el.options[el.selectedIndex].text").strip()
        heading = page.query_selector("h3, h4, .report-title")
        if heading:
            match = re.search(r"20\d\d-\d{2,4}", heading.inner_text())
            if match:
                return match.group(0)
    except Exception:
        pass
    return None


def parse_gpdp_table(page):
    """
    Parse the GPDP works table from the loaded report page.
    Returns a list of dicts: work_name, sector, estimated_cost, status.
    Returns [] if no table found — NEVER invents data.
    """
    rows = []
    try:
        table_rows = page.query_selector_all("table tr")
        if not table_rows:
            return []

        headers = []
        for tr in table_rows:
            cells = [td.inner_text().strip() for td in tr.query_selector_all("th, td")]
            if not cells:
                continue

            if not headers:
                lower = [c.lower() for c in cells]
                if any("work" in c or "activity" in c for c in lower):
                    headers = lower
                continue

            if len(cells) < 2:
                continue

            row = {}
            for i, h in enumerate(headers):
                val = cells[i] if i < len(cells) else None
                if "work" in h or "activity" in h:
                    row["work_name"] = val
                elif "sector" in h or "department" in h:
                    row["sector"] = val
                elif "cost" in h or "amount" in h or "estimate" in h:
                    row["estimated_cost"] = _safe_float(val)
                elif "status" in h or "stage" in h:
                    row["status"] = val

            if row.get("work_name"):
                rows.append(row)

    except Exception as e:
        print(f"  [parse error] {e}")

    return rows


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        prioritized = (
            db.query(VillagePriority, Gazetteer)
            .join(Gazetteer, Gazetteer.id == VillagePriority.gazetteer_id)
            .order_by(VillagePriority.priority_score.desc())
            .all()
        )
        captured_ids = {r[0] for r in db.query(VillageBudgetPlan.gazetteer_id).distinct().all()}
        to_process = [row for row in prioritized if row.Gazetteer.id not in captured_ids]
        total_remaining = len(to_process)
    except Exception as e:
        print(f"DB error: {e}")
        db.close()
        return

    print(f"\n{'='*60}")
    print(f"  eGramSwaraj GPDP Capture Tool")
    print(f"{'='*60}")
    print(f"  Already captured: {len(captured_ids)} villages")
    print(f"  Remaining:        {total_remaining} villages")
    print(f"  Order:            highest priority score first")
    print(f"{'='*60}\n")

    if not to_process:
        print("All 441 villages already captured!")
        db.close()
        return

    captured_this_session = 0
    failed_this_session = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=200)
        context = browser.new_context()
        page = context.new_page()

        for idx, (vp, gaz) in enumerate(to_process, 1):
            print(f"\n[{idx}/{total_remaining}] {gaz.name}, {gaz.district} (score={vp.priority_score:.1f})")
            print(f"  Navigating to eGramSwaraj...")

            try:
                page.goto(URL, timeout=30000)
                page.wait_for_load_state("networkidle", timeout=15000)

                # Auto-select Maharashtra
                try:
                    page.select_option("select[name='stateId']", label="MAHARASHTRA")
                    page.wait_for_timeout(1000)
                    print(f"  [OK] State = Maharashtra selected")
                except Exception:
                    print(f"  [warn] Could not auto-select state — please select Maharashtra manually")

                print(f"\n  ┌─────────────────────────────────────────────────────┐")
                print(f"  │  CAPTCHA REQUIRED — do these steps in the browser:  │")
                print(f"  │                                                     │")
                print(f"  │  1. Select District: {gaz.district:<30} │")
                print(f"  │  2. Select Block, then Gram Panchayat               │")
                print(f"  │  3. Village: {gaz.name:<38} │")
                print(f"  │  4. Solve the CAPTCHA                               │")
                print(f"  │  5. Click 'Get Report' and wait for the table       │")
                print(f"  └─────────────────────────────────────────────────────┘")
                print(f"\n  Press Enter when the report table is visible on screen")
                print(f"  (or type 'skip' + Enter to skip this village,")
                print(f"   or type 'quit' + Enter to stop and save progress)")

                user_input = input("  > ").strip().lower()

                if user_input == "quit":
                    print(f"\n  Stopping. Progress saved.")
                    break

                if user_input == "skip":
                    print(f"  Skipped {gaz.name}.")
                    failed_this_session += 1
                    continue

                fin_year = get_financial_year(page)
                works = parse_gpdp_table(page)

                if not works:
                    print(f"  [warn] No work rows found in table. Saving NULL placeholder.")
                    db.add(VillageBudgetPlan(
                        gazetteer_id=gaz.id,
                        financial_year=fin_year,
                        work_name=None,
                        sector=None,
                        estimated_cost=None,
                        status=None,
                        source="gpdp_manual_capture",
                        fetched_at=datetime.now(timezone.utc),
                    ))
                    db.commit()
                    captured_this_session += 1
                    print(f"  Saved (0 works found, NULL row recorded so village won't be retried).")
                else:
                    for w in works:
                        db.add(VillageBudgetPlan(
                            gazetteer_id=gaz.id,
                            financial_year=fin_year,
                            work_name=w.get("work_name"),
                            sector=w.get("sector"),
                            estimated_cost=w.get("estimated_cost"),
                            status=w.get("status"),
                            source="gpdp_manual_capture",
                            fetched_at=datetime.now(timezone.utc),
                        ))
                    db.commit()
                    captured_this_session += 1
                    print(f"  [OK] Saved {len(works)} work rows for {gaz.name} (year={fin_year})")

            except KeyboardInterrupt:
                print(f"\n  Interrupted. Progress saved.")
                break
            except Exception as e:
                print(f"  [error] {e}")
                failed_this_session += 1

        browser.close()

    db.close()

    total_captured = len(captured_ids) + captured_this_session
    print(f"\n{'='*60}")
    print(f"  Session complete")
    print(f"  Captured this session: {captured_this_session} villages")
    print(f"  Total captured so far: {total_captured} / 441")
    print(f"  Remaining:             {441 - total_captured}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
