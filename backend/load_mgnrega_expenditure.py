"""
Loads village-panchayat-level MGNREGA expenditure and wage/material ratio
from the Ministry of Rural Development's official NREGA portal
(gp_cummulative_report1.aspx) for Kolhapur and Nashik districts.

WHY THIS LOADER EXISTS
----------------------
Pulls genuine Gram Panchayat expenditure accounts directly from MGNREGA:
- Total expenditure (Rs in Lakhs)
- Wages paid (Rs in Lakhs and percentage share)
- Material expenditure (Rs in Lakhs and percentage share)
This provides authoritative, village-level rural employment and public works
investment tracking to complement PRIASoft (15th FC) and JJM (water).

SOURCE
------
Portal: MoRD MGNREGA / NREGA Soft
Endpoint: https://mnregaweb2.dord.gov.in/netnrega/state_html/gp_cummulative_report1.aspx

Run:
    python backend/load_mgnrega_expenditure.py [--dry-run] [--district Kolhapur] [--max-blocks 2]
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from rapidfuzz import fuzz, process

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from database import SessionLocal, engine  # noqa: E402
from models import Base, Gazetteer, VillageMgnregaExpenditure  # noqa: E402

BASE_URL = "https://mnregaweb2.dord.gov.in/netnrega"
STATE_CODE = 18  # Maharashtra

DISTRICT_PAYLOADS = {
    "Kolhapur": "U_OcZt90hZouLFauGZRk6OPxugxHmH2aMVo674eLoFJHoL-WMc2XbVyIHrBMCetJ_BljA-yz2NcqbaJmfM2qGFtIsppHpFFEvaBswsGeiFscHX9IdNOZsgSDqNqXYRuYZcvY1_vwGXIAij4E8JQ5aqNHjIBD169HSAhS3cgcCERT_YzWZxDV2DRb_NYjQ4Hb",
    "Nashik": "0R1zT8UJ5AovAibt1_z7TKODCkqwjxWFf4Ddw_HzMfGxTgCnhj3W-WwH7-Iy32-ChoHWZiVUvhjcklLCUDioXghBB4JrZOkZTd0bY7OGgCUvc_KRIzogm_G7wuTBi0l4bNrhOa2xcD5JwmBP7Uyj0eSwdOh-0Ezxycyexh2lycFjCJu_pJDDWD5-qdX56P9D",
}

MATCH_THRESHOLD = 88

HEADERS = {
    "User-Agent": "AwaazIQ-Research/1.0 (Civic Data Prioritisation; Contact: research@awaaziq.org)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def parse_amt_pct(val: str) -> tuple[float | None, float | None]:
    """
    Parses strings like:
      '0.56(100)' -> (0.56, 100.0)
      '4.12(76.8&nbsp;%)' -> (4.12, 76.8)
      '0(0&nbsp;%)' -> (0.0, 0.0)
      '2.31(48.91)' -> (2.31, 48.91)
      '0' -> (0.0, None)
    """
    if not val:
        return None, None
    clean = val.replace("&nbsp;", " ").replace("%", "").strip()
    m = re.match(r"^([\d\.]+)(?:\s*\(\s*([\d\.]+)\s*\))?", clean)
    if m:
        amt = float(m.group(1)) if m.group(1) else None
        pct = float(m.group(2)) if m.group(2) else None
        return amt, pct
    return None, None


class MgnregaClient:
    """Manages session cookies across hierarchical ASP.NET pages."""

    def __init__(self):
        self.cookie_jar = urllib.request.HTTPCookieProcessor()
        self.opener = urllib.request.build_opener(self.cookie_jar)
        self.session_ready = False

    def init_session(self) -> bool:
        """Initializes the session on the Maharashtra state portal."""
        url = f"{BASE_URL}/homestciti.aspx?state_code={STATE_CODE}&state_name=MAHARASHTRA&lflag=eng&labels=labels"
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with self.opener.open(req, timeout=35) as r:
                if r.status == 200:
                    self.session_ready = True
                    return True
        except Exception as e:
            print(f"  [ERROR] Failed to init session: {e}")
        return False

    def get(self, url: str, referer: str = None, retries: int = 3) -> str | None:
        h = dict(HEADERS)
        if referer:
            h["Referer"] = referer
        req = urllib.request.Request(url, headers=h)
        for attempt in range(1, retries + 1):
            try:
                with self.opener.open(req, timeout=35) as r:
                    if r.status == 200:
                        return r.read().decode("utf-8", errors="replace")
            except Exception as e:
                if attempt < retries:
                    time.sleep(attempt * 2.0)
                else:
                    print(f"  [ERROR] Failed to fetch {url}: {e}")
        return None


def extract_blocks_for_district(client: MgnregaClient, district: str) -> list[tuple[str, str]]:
    """Returns list of (block_name, po_index_href) for the district."""
    payload = DISTRICT_PAYLOADS.get(district)
    if not payload:
        return []
    url = f"{BASE_URL}/Homedist.aspx?payload={payload}"
    ref = f"{BASE_URL}/homestciti.aspx"
    html = client.get(url, referer=ref)
    if not html:
        return []

    links = re.findall(r'<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', html)
    blocks = []
    for href, text in links:
        clean = re.sub(r"<[^>]+>", "", text).strip()
        if "PoIndexFrame.aspx" in href and clean:
            clean_href = href.replace("../", "")
            blocks.append((clean, clean_href))
    return blocks


def parse_block_expenditure(html: str) -> tuple[str | None, list[dict]]:
    """
    Parses gp_cummulative_report1.aspx.
    Returns (fin_year, list of GP row dicts).
    """
    if not html:
        return None, []

    # Extract financial year from text
    fy_match = re.search(r"Financial Year\s*([0-9]{4}-[0-9]{4})", html, re.I)
    fin_year = fy_match.group(1) if fy_match else "2026-2027"

    tables = re.findall(r"<table[^>]*>([\s\S]*?)</table>", html)
    candidate_tables = []
    for t in tables:
        if "Panchayat" in t:
            t_rows = re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", t)
            candidate_tables.append((len(t_rows), t))

    if not candidate_tables:
        return fin_year, []

    candidate_tables.sort(key=lambda x: x[0], reverse=True)
    target_table = candidate_tables[0][1]

    rows = re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", target_table)
    parsed_rows = []

    for r in rows:
        cells = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
            for c in re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", r)
        ]
        # Valid data rows have: S.No (digit), Panchayat Name (not a digit), Total, Wages, Material
        if len(cells) >= 5 and cells[0].isdigit() and not cells[1].isdigit():
            s_no = int(cells[0])
            gp_name = cells[1]
            tot_exp = _safe_float(cells[2])
            wages_amt, wages_pct = parse_amt_pct(cells[3])
            mat_amt, mat_pct = parse_amt_pct(cells[4])

            parsed_rows.append({
                "s_no": s_no,
                "panchayat": gp_name,
                "total_exp_lakh": tot_exp,
                "wages_lakh": wages_amt,
                "wages_percent": wages_pct,
                "material_lakh": mat_amt,
                "material_percent": mat_pct,
                "raw_cells": cells,
            })

    return fin_year, parsed_rows


def load_mgnrega_expenditure(
    dry_run: bool = False,
    selected_district: str = None,
    selected_block: str = None,
    max_blocks: int = None,
    max_gps_per_block: int = None,
) -> dict:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    client = MgnregaClient()
    print("\n" + "=" * 70)
    print("STARTING MGNREGA GRAM PANCHAYAT EXPENDITURE LOADER")
    print(f"Districts: {list(DISTRICT_PAYLOADS.keys())}")
    print("=" * 70)

    total_fetched = 0
    total_matched = 0
    total_unmatched = 0
    saved_count = 0
    sample_records = []

    try:
        print("\nInitializing session on Maharashtra portal...")
        if not client.init_session():
            print("  [FATAL] Failed to initialize MGNREGA session.")
            return {}

        districts_to_process = (
            {selected_district: DISTRICT_PAYLOADS[selected_district]}
            if selected_district and selected_district in DISTRICT_PAYLOADS
            else DISTRICT_PAYLOADS
        )

        for dist_name in districts_to_process:
            print(f"\n--- Processing {dist_name} District ---")

            # Load Gazetteer records for fuzzy matching
            our_villages = db.query(Gazetteer).filter(Gazetteer.district == dist_name).all()
            our_names = {v.id: v.name for v in our_villages}
            print(f"  Loaded {len(our_villages)} villages from Gazetteer for {dist_name}")

            blocks = extract_blocks_for_district(client, dist_name)
            print(f"  Found {len(blocks)} blocks for {dist_name}")

            if selected_block:
                blocks = [(b, h) for b, h in blocks if b.upper() == selected_block.upper()]
                print(f"  Filtered to block: {selected_block} ({len(blocks)} found)")

            if max_blocks and max_blocks > 0:
                blocks = blocks[:max_blocks]
                print(f"  Capped at {max_blocks} blocks")

            for b_idx, (block_name, block_href) in enumerate(blocks):
                print(f"\n  [{b_idx+1}/{len(blocks)}] Fetching block {block_name}...")
                block_url = (
                    f"{BASE_URL}/{block_href}"
                    if not block_href.startswith("http")
                    else block_href.replace("http://", "https://")
                )
                b_html = client.get(block_url, referer=f"{BASE_URL}/Homedist.aspx")
                if not b_html:
                    continue

                # Find all GP links in this block
                all_gp_links = re.findall(
                    r'<a[^>]*href="([^"]*IndexFrame\.aspx\?payload=[^"]*)"[^>]*>([\s\S]*?)</a>',
                    b_html,
                )
                print(f"    Found {len(all_gp_links)} Gram Panchayats in block {block_name}")
                if not all_gp_links:
                    continue

                # Open the first GP to reach gp_cummulative_report1.aspx
                # If that report contains all block rows, we collect all of them in 1 shot!
                first_gp_href, first_gp_name = all_gp_links[0]
                first_gp_url = f"{BASE_URL}/{first_gp_href.replace('../', '')}"
                gp_html = client.get(first_gp_url, referer=block_url)
                if not gp_html:
                    continue

                m = re.search(r'href="([^"]*gp_cummulative_report1\.aspx\?payload=[^"]*)"', gp_html)
                if not m:
                    print(f"    [WARN] No cumulative report link found for block {block_name}")
                    continue

                rpt_href = m.group(1).replace("../", "")
                rpt_url = (
                    f"{BASE_URL}/{rpt_href}"
                    if not rpt_href.startswith("http")
                    else rpt_href.replace("http://", "https://")
                )

                time.sleep(0.5)
                rpt_html = client.get(rpt_url, referer=first_gp_url)
                if not rpt_html:
                    continue

                fin_year, gp_rows = parse_block_expenditure(rpt_html)

                # If the report only returned 1 row (just the first GP),
                # we iterate through the remaining GPs in the block
                if len(gp_rows) == 1 and len(all_gp_links) > 1:
                    target_gps = all_gp_links[1:]
                    if max_gps_per_block and max_gps_per_block > 0:
                        target_gps = target_gps[: max_gps_per_block - 1]
                    total_to_fetch = len(target_gps) + 1
                    print(f"    Fetching {total_to_fetch} Gram Panchayats in block {block_name}...")
                    for gp_idx, (g_href, g_name) in enumerate(target_gps):
                        g_clean = re.sub(r"<[^>]+>", "", g_name).strip()
                        g_url = f"{BASE_URL}/{g_href.replace('../', '')}"
                        time.sleep(0.15)
                        sub_gp_html = client.get(g_url, referer=block_url)
                        if not sub_gp_html:
                            continue
                        sub_m = re.search(r'href="([^"]*gp_cummulative_report1\.aspx\?payload=[^"]*)"', sub_gp_html)
                        if not sub_m:
                            continue
                        sub_rpt_url = f"{BASE_URL}/{sub_m.group(1).replace('../', '')}"
                        time.sleep(0.15)
                        sub_rpt_html = client.get(sub_rpt_url, referer=g_url)
                        if sub_rpt_html:
                            _, single_row = parse_block_expenditure(sub_rpt_html)
                            if single_row:
                                gp_rows.extend(single_row)
                        if (gp_idx + 2) % 10 == 0 or (gp_idx + 2) == total_to_fetch:
                            print(f"      Progress: {len(gp_rows)}/{total_to_fetch} GPs fetched...")

                print(f"    Processed {len(gp_rows)} Gram Panchayat expenditure rows (FY {fin_year})")
                total_fetched += len(gp_rows)

                dist_matched = 0
                dist_unmatched = 0

                for r in gp_rows:
                    gp_clean = r["panchayat"].strip()
                    gaz_id = None

                    # Fuzzy match to Gazetteer
                    if our_names:
                        match = process.extractOne(gp_clean, our_names, scorer=fuzz.WRatio)
                        if match and match[1] >= MATCH_THRESHOLD:
                            gaz_id = match[2]
                            dist_matched += 1
                        else:
                            dist_unmatched += 1

                    raw_str = json.dumps(r, ensure_ascii=False)
                    now_utc = datetime.now(timezone.utc)

                    if not dry_run:
                        existing = (
                            db.query(VillageMgnregaExpenditure)
                            .filter_by(
                                district=dist_name,
                                block=block_name,
                                village_name=gp_clean,
                                fin_year=fin_year,
                            )
                            .first()
                        )

                        if existing:
                            existing.gazetteer_id = gaz_id
                            existing.total_expenditure_lakh = r["total_exp_lakh"]
                            existing.wages_lakh = r["wages_lakh"]
                            existing.wages_percent = r["wages_percent"]
                            existing.material_lakh = r["material_lakh"]
                            existing.material_percent = r["material_percent"]
                            existing.raw_json = raw_str
                            existing.fetched_at = now_utc
                        else:
                            obj = VillageMgnregaExpenditure(
                                gazetteer_id=gaz_id,
                                village_name=gp_clean,
                                district=dist_name,
                                block=block_name,
                                fin_year=fin_year,
                                total_expenditure_lakh=r["total_exp_lakh"],
                                wages_lakh=r["wages_lakh"],
                                wages_percent=r["wages_percent"],
                                material_lakh=r["material_lakh"],
                                material_percent=r["material_percent"],
                                raw_json=raw_str,
                                fetched_at=now_utc,
                            )
                            db.add(obj)
                        saved_count += 1

                    if gaz_id and len(sample_records) < 5:
                        sample_records.append({
                            "panchayat": gp_clean,
                            "district": dist_name,
                            "block": block_name,
                            "gazetteer_id": gaz_id,
                            "fin_year": fin_year,
                            "total_exp_lakh": r["total_exp_lakh"],
                            "wages_lakh": r["wages_lakh"],
                            "material_lakh": r["material_lakh"],
                        })

                if not dry_run:
                    db.commit()

                total_matched += dist_matched
                total_unmatched += dist_unmatched
                pct_m = (dist_matched / (len(gp_rows) or 1)) * 100
                print(f"    Matched to Gazetteer: {dist_matched}/{len(gp_rows)} ({pct_m:.1f}%)")
                time.sleep(0.5)

        print("\n" + "=" * 70)
        print("MGNREGA EXPENDITURE LOADER FINISHED")
        print("=" * 70)
        print(f"Total GP rows processed: {total_fetched}")
        pct_tot = (total_matched / (total_fetched or 1)) * 100
        print(f"Matched to Gazetteer:    {total_matched} ({pct_tot:.1f}%)")
        print(f"Unmatched (audited):     {total_unmatched}")
        if not dry_run:
            print(f"Saved to database:       {saved_count}")
        print("\nSample matched Gram Panchayat records:")
        for s in sample_records:
            print(
                f"  {s['panchayat']} ({s['block']}, {s['district']}, gaz_id={s['gazetteer_id']}, FY {s['fin_year']}): "
                f"Total=Rs. {s['total_exp_lakh']}L, Wages=Rs. {s['wages_lakh']}L, Material=Rs. {s['material_lakh']}L"
            )
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
    parser = argparse.ArgumentParser(description="Load MGNREGA GP expenditure records.")
    parser.add_argument("--dry-run", action="store_true", help="Do not save to database")
    parser.add_argument("--district", choices=list(DISTRICT_PAYLOADS.keys()), help="Filter to one district")
    parser.add_argument("--block", type=str, help="Filter to one block name")
    parser.add_argument("--max-blocks", type=int, default=None, help="Maximum number of blocks to process")
    parser.add_argument("--max-gps-per-block", type=int, default=None, help="Maximum number of GPs per block to fetch")
    args = parser.parse_args()

    load_mgnrega_expenditure(
        dry_run=args.dry_run,
        selected_district=args.district,
        selected_block=args.block,
        max_blocks=args.max_blocks,
        max_gps_per_block=args.max_gps_per_block,
    )


if __name__ == "__main__":
    main()
