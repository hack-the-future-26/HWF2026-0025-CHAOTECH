"""
Loads REAL individually-sanctioned PMGSY road works into `government_project`,
and pins each one to a village where its endpoint names can be matched.

WHY THIS IS THE IMPORTANT LOADER
--------------------------------
The research report's central claim (§9, "Capability E") is that no system
anywhere scores citizen demand *against government investment data*. That
capability was impossible to build while the database had no notion of a
government project at all. This loader creates that side of the comparison.

Each row is a real road work: what was sanctioned, when, for how much, and
whether it has actually been built. Because every record carries
`IMS_ROAD_FROM` and `IMS_ROAD_TO` -- real place names -- works can be matched
to villages by name, with no geometry and no spatial join.

That makes two of the report's three demand/investment mismatches computable
(feature #2, §10.2):

  * demanded but unfunded  -- complaints where no sanctioned work exists
  * funded but undelivered -- a sanctioned work, still not built, next to a
                              village that has been complaining

SOURCE
------
POST https://pmgsy.dord.gov.in/dbweb/ChiefSecretary/GetRoadTenderDetails
Public, no login. Session cookie + anti-forgery token are obtained from
GET /dbweb first. See REAL_DATA_RESEARCH.md §2.2b.

READ THIS BEFORE QUOTING ANY TOTAL
----------------------------------
This endpoint returns only works still in the tender/execution pipeline --
"Not Started", "In Progress", "Agreement Cancelled". No row is ever
"Completed". So:

  * the row count is a BACKLOG, not the whole programme
  * it does NOT reconcile with the district aggregate's Balance column, which
    counts a different universe (REAL_DATA_RESEARCH.md §5.3)
  * never sum the two, and never call this total "undelivered spending" as
    though the government certified it as such

The honest phrasing is: "N road works are recorded in PMGSY's tender/execution
pipeline for this district, totalling Rs X of sanctioned cost, none marked
complete in this report."
"""

import re
import sys

import requests
from rapidfuzz import fuzz, process

from database import SessionLocal
from models import Gazetteer, GovernmentProject

BASE = "https://pmgsy.dord.gov.in/dbweb"
DASHBOARD = f"{BASE}/"
WORKS_ENDPOINT = f"{BASE}/ChiefSecretary/GetRoadTenderDetails"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ComplainBox-Hackathon/1.0)",
    "Referer": BASE,
}

# PMGSY's own codes, read off the dashboard's dropdowns.
STATE_CODE = 21  # Maharashtra
DISTRICTS = {"Kolhapur": 306, "Nashik": 393}

ALL_SCHEMES = 0

# Endpoint names are free text ("MDR-43 (Vani Kh.)", "SH 134"). Only match
# names that look like a settlement, and require a high score -- a wrong pin
# is worse than an honest NULL, because it would attribute a road to a village
# that never had one proposed.
MATCH_THRESHOLD = 88

# Road-network references rather than places: these are the *other* end of the
# link (a highway), not the village being connected.
ROAD_REF = re.compile(
    r"^(NH|SH|MDR|ODR|VR|RR)[\s\-–]*\d*|^\d+(\.\d+)?$", re.IGNORECASE
)


def open_session() -> requests.Session:
    """Dashboard session: cookie + anti-forgery token."""
    session = requests.Session()
    session.headers.update(HEADERS)
    page = session.get(DASHBOARD, timeout=60)
    page.raise_for_status()
    return session


def fetch_works(session: requests.Session, district_code: int) -> list[dict]:
    response = session.post(
        WORKS_ENDPOINT,
        json={
            "StateID": STATE_CODE,
            "DistrictID": district_code,
            "SchemeID": ALL_SCHEMES,
        },
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else []


def clean_place(raw: str | None) -> str | None:
    """
    Strips a road endpoint down to something matchable, or returns None if it
    is a road reference rather than a place.

        "MDR-43 (Vani Kh.)"  -> "Vani Kh"      (the place in the brackets)
        "SH 134"             -> None           (a highway, not a village)
        "Nanashi"            -> "Nanashi"
    """
    if not raw:
        return None
    value = raw.strip()
    if not value:
        return None

    bracketed = re.search(r"\(([^)]+)\)", value)
    if bracketed:
        value = bracketed.group(1).strip()
    else:
        value = re.sub(r"^(NH|SH|MDR|ODR|VR)[\s\-–]*\d*[A-Za-z]?", "", value).strip()

    value = value.strip(" .,-–")
    if not value or ROAD_REF.match(value):
        return None
    if len(value) < 3:
        return None
    return value


def parse_year(value) -> int | None:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1990 <= year <= 2100 else None


def to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def match_village(place: str | None, names: list[str], rows: list[Gazetteer]):
    """Best gazetteer match for a cleaned endpoint name, or (None, None)."""
    if not place:
        return None, None
    result = process.extractOne(place, names, scorer=fuzz.WRatio)
    if result is None or result[1] < MATCH_THRESHOLD:
        return None, None
    _name, score, index = result
    return rows[index], float(score)


def main() -> int:
    db = SessionLocal()
    try:
        session = open_session()
    except Exception as exc:
        print(f"FAILED to open PMGSY dashboard session: {exc}")
        db.close()
        return 1

    total_loaded = 0
    total_matched = 0

    try:
        for district, code in DISTRICTS.items():
            print(f"\n=== {district} (PMGSY district code {code}) ===")
            try:
                works = fetch_works(session, code)
            except Exception as exc:
                print(f"  FAILED to fetch works: {exc}")
                continue
            print(f"  works returned: {len(works)}")
            if not works:
                continue

            gazetteer_rows = (
                db.query(Gazetteer).filter(Gazetteer.district == district).all()
            )
            names = [g.name for g in gazetteer_rows]

            # Re-loadable: clear this district's previous batch first, exactly
            # as load_gazetteer/seed_synthetic_data do, so re-running does not
            # stack duplicates.
            db.query(GovernmentProject).filter(
                GovernmentProject.district == district,
                GovernmentProject.source == "pmgsy_dashboard",
            ).delete()

            matched_here = 0
            status_counts: dict[str, int] = {}

            for work in works:
                road_from = (work.get("IMS_ROAD_FROM") or "").strip()
                road_to = (work.get("IMS_ROAD_TO") or "").strip()

                # Prefer the destination: PMGSY roads are named "<existing
                # highway> to <village being connected>", so ROAD_TO is far
                # more often the settlement the work actually serves.
                village, score = match_village(clean_place(road_to), names, gazetteer_rows)
                field = "road_to"
                if village is None:
                    village, score = match_village(
                        clean_place(road_from), names, gazetteer_rows
                    )
                    field = "road_from" if village is not None else None

                status = (work.get("WORK_STATUS") or "").strip() or "Unknown"
                status_counts[status] = status_counts.get(status, 0) + 1

                db.add(
                    GovernmentProject(
                        source="pmgsy_dashboard",
                        external_id=work.get("IMS_PR_ROAD_CODE"),
                        package_id=(work.get("IMS_PACKAGE_ID") or "").strip() or None,
                        scheme_name=(work.get("PMGSY_SCHEME_NAME") or "").strip() or None,
                        work_name=(work.get("IMS_ROAD_NAME") or "").strip() or None,
                        road_from=road_from or None,
                        road_to=road_to or None,
                        district=district,
                        sanctioned_year=parse_year(work.get("IMS_YEAR")),
                        sanctioned_cost_lakh=to_float(work.get("TOTAL_SANCTIONED_COST")),
                        length_km=to_float(work.get("IMS_PAV_LENGTH")),
                        agreement_number=(work.get("TEND_AGREEMENT_NUMBER") or "").strip()
                        or None,
                        agreement_amount_lakh=to_float(work.get("TEND_AGREEMENT_AMOUNT")),
                        days_sanction_to_agreement=work.get("DAYS_SANCTION_TO_AGREEMENT"),
                        work_status=status,
                        matched_gazetteer_id=village.id if village else None,
                        match_score=score,
                        match_field=field,
                    )
                )
                if village is not None:
                    matched_here += 1

            db.commit()
            total_loaded += len(works)
            total_matched += matched_here

            print(f"  status breakdown: {status_counts}")
            print(
                f"  pinned to a known village: {matched_here}/{len(works)} "
                f"({matched_here / len(works):.0%})"
            )
            cost = sum(to_float(w.get("TOTAL_SANCTIONED_COST")) or 0 for w in works)
            print(f"  sanctioned cost in this pipeline: Rs {cost:,.2f} lakh")

        print("\n=== Summary ===")
        print(f"  government works loaded : {total_loaded}")
        print(f"  pinned to a village     : {total_matched}")
        print(
            "  NOTE: this is PMGSY's tender/execution BACKLOG, not the whole\n"
            "        programme, and it does not reconcile with the district\n"
            "        aggregate's Balance column. See REAL_DATA_RESEARCH.md 5.3."
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
