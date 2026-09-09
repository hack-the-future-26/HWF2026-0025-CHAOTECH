"""
Loads the OFFICIAL administrative hierarchy from the Local Government
Directory (Ministry of Panchayati Raj) into `lgd_village`.

WHY THIS LOADER MATTERS
-----------------------
`gazetteer.block` has never been an official fact. It was produced by a
nearest-taluka-headquarters geometric guess, which the README has flagged
from the start as "a geometric approximation, not official boundary data".

Measured against LGD before this was written, that guess agrees with the
official taluka for 549 of 939 matched villages (58%) and with the official
development block for 447 (48%). It is not reliably either one -- it sits
between two genuinely different administrative units.

That is not cosmetic. `block` labels every cluster, groups the officials'
worklist, and is read as the unit of responsibility. A wrong block sends a
work order to the wrong office.

WHAT LGD SETTLES
----------------
Taluka (sub-district) and development block are stored SEPARATELY here. In
India they are different divisions that often but not always coincide, and
collapsing them is what produced the ambiguity in the first place.

Every level also carries its Census 2011 code. That is the join key this
project has never had: village_amenities came from the Census and PMGSY
carries block ids, but until now the only thing linking any of them to our
gazetteer was fuzzy name matching. A code is not a guess.

SOURCE
------
https://github.com/ramSeraph/opendata -- a daily mirror of LGD's own bulk
CSV exports, published as 7z-compressed CSV on GitHub releases:

    lgd-latest        villages_by_blocks.<DDMonYYYY>.csv.7z   (~10 MB)

`villages_by_blocks` is the one file worth having: it carries the complete
chain -- state, district, sub-district, development block, village -- plus
the Census 2011 code at every level, on a single row.

Chosen over lgdirectory.gov.in itself because the portal serves these only
through an interactive session; this mirror publishes the same dumps daily as
plain files. data.gov.in also catalogues LGD but updates monthly.

The exact filename carries a date and the release keeps ~156 of them, so the
newest is resolved from the GitHub API at run time rather than pinned -- a
hardcoded date would silently rot within weeks.

Run:  python load_lgd_hierarchy.py
Idempotent: clears the table first, so re-running refreshes.
"""

import csv
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import py7zr  # noqa: E402
from rapidfuzz import fuzz, process  # noqa: E402

from database import SessionLocal, engine  # noqa: E402
from models import Base, Gazetteer, LgdVillage  # noqa: E402

RELEASES = "https://api.github.com/repos/ramSeraph/opendata/releases/tags/lgd-latest"

# GitHub redirects release downloads to a CDN and throttles clients that send
# no User-Agent -- without this the 10 MB fetch times out rather than failing
# fast, which is a far more confusing way to lose.
HEADERS = {"User-Agent": "AwaazIQ/1.0 (LGD hierarchy loader)"}
DOWNLOAD = (
    "https://github.com/ramSeraph/opendata/releases/download/lgd-latest/{name}"
)

STATE = "Maharashtra"

# LGD's district spelling -> ours. LGD writes Nashik as "Nashik", but the
# health register writes "Nasik", so this mapping is kept explicit rather than
# assumed -- the same trap that silently returned zero rows in
# load_health_facilities.py.
DISTRICTS = {
    "Kolhapur": "Kolhapur",
    "Nashik": "Nashik",
    "Nasik": "Nashik",
}

# Same threshold as load_village_amenities.py, so a village that matched its
# census row there matches its LGD row here. Two different thresholds across
# two loaders would put the same village under two different identities.
MATCH_THRESHOLD = 85

CACHE = Path(__file__).resolve().parent / "data" / "lgd_villages_by_blocks.csv"


def newest_asset() -> str | None:
    """Filename of the most recently published villages_by_blocks dump."""
    try:
        request = urllib.request.Request(RELEASES, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=120) as response:
            release = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
        print(f"  ! could not list releases: {type(err).__name__}: {err}")
        return None

    candidates = [
        asset
        for asset in release.get("assets", [])
        if asset["name"].startswith("villages_by_blocks.")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda a: a["updated_at"])["name"]


def fetch_rows() -> list[dict] | None:
    """The villages_by_blocks CSV as dict rows, cached on disk after first run."""
    if CACHE.exists() and CACHE.stat().st_size > 1_000_000:
        print(f"using cached {CACHE.name} ({CACHE.stat().st_size / 1e6:.0f} MB)")
        with CACHE.open(encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    name = newest_asset()
    if not name:
        print("nothing to download")
        return None

    print(f"downloading {name} (~10 MB)…")
    try:
        request = urllib.request.Request(DOWNLOAD.format(name=name), headers=HEADERS)
        with urllib.request.urlopen(request, timeout=600) as response:
            blob = response.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
        print(f"  ! download failed: {type(err).__name__}: {err}")
        return None

    # py7zr needs a seekable file object, and the archive holds exactly one CSV.
    with py7zr.SevenZipFile(io.BytesIO(blob)) as archive:
        extracted = archive.readall()
    if not extracted:
        print("  ! archive was empty")
        return None
    inner_name, inner = next(iter(extracted.items()))
    text = inner.read().decode("utf-8-sig")

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(text, encoding="utf-8")
    print(f"  extracted {inner_name}")
    return list(csv.DictReader(io.StringIO(text)))


def clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    # LGD writes an absent local body as the literal code "0".
    return text if text and text != "0" else None


def main() -> None:
    rows = fetch_rows()
    if not rows:
        print("nothing loaded")
        return

    ours = []
    for row in rows:
        if (row.get("State Name (In English)") or "").strip() != STATE:
            continue
        district = DISTRICTS.get((row.get("District Name (In English)") or "").strip())
        if district:
            ours.append((district, row))

    print(f"{len(ours)} LGD villages across {sorted(set(d for d, _ in ours))}")

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    removed = db.query(LgdVillage).delete(synchronize_session=False)
    if removed:
        print(f"cleared {removed} existing rows")

    # Match our gazetteer to LGD, NOT the other way round, and assert a link
    # only when the answer is unambiguous.
    #
    # Village names repeat relentlessly in Maharashtra: "Pimpalgaon" occurs in
    # nine different talukas of Nashik district alone, every one of them
    # scoring 100 on the name. Matching LGD -> gazetteer attached whichever
    # copy the scorer happened to return first and then published its taluka as
    # the official correction -- which would be worse than the geometric guess
    # it replaces, because the guess at least used coordinates.
    #
    # LGD carries no lat/lon, and neither our gazetteer nor village_amenities
    # stores a Census village code, so there is nothing left to disambiguate a
    # repeated name with. The honest response is to link only where exactly one
    # taluka tops the score, and to record the rest as ambiguous rather than
    # guess. Same rule as work-group asset naming.
    lgd_by_district: dict[str, list] = {}
    for district, row in ours:
        lgd_by_district.setdefault(district, []).append(row)

    link: dict[int, tuple[dict, float]] = {}
    ambiguous = 0

    for village in db.query(Gazetteer).all():
        if not village.name or not village.district:
            continue
        candidates = lgd_by_district.get(village.district, [])
        if not candidates:
            continue

        names = [c.get("Village Name (In English)") or "" for c in candidates]
        hits = process.extract(
            village.name,
            names,
            scorer=fuzz.WRatio,
            score_cutoff=MATCH_THRESHOLD,
            limit=None,
        )
        if not hits:
            continue

        best = max(h[1] for h in hits)
        top = [h for h in hits if h[1] == best]
        # Several LGD villages sharing the top score means the name genuinely
        # does not identify one place. Distinct talukas among them is proof.
        if len({candidates[h[2]].get("Subdistrict Name (In English)") for h in top}) > 1:
            ambiguous += 1
            continue

        link[village.id] = (candidates[top[0][2]], round(best, 2))

    # Ambiguity runs both ways. Two of our gazetteer villages can land on one
    # LGD village -- our gazetteer has its own repeated names. Keying the
    # write by id(row) let the second silently overwrite the first, so the
    # loader reported more links than it actually wrote. Drop those too: if we
    # cannot say which of our villages is that LGD village, we do not know.
    claimed: dict[int, list[int]] = {}
    for gazetteer_id, (row, _) in link.items():
        claimed.setdefault(id(row), []).append(gazetteer_id)
    for gazetteer_ids in claimed.values():
        if len(gazetteer_ids) > 1:
            for gazetteer_id in gazetteer_ids:
                link.pop(gazetteer_id, None)
                ambiguous += 1

    matched = len(link)
    corrected_taluka = 0
    for village in db.query(Gazetteer).all():
        if village.id not in link:
            continue
        row, _ = link[village.id]
        subdistrict = clean(row.get("Subdistrict Name (In English)"))
        if (
            village.block
            and subdistrict
            and village.block.strip().lower() != subdistrict.lower()
        ):
            corrected_taluka += 1

    # Rows are written for every LGD village in the pilot districts, so the
    # hierarchy stays complete; gazetteer_id is filled in only where the link
    # was unambiguous.
    linked_rows = {id(row): (gid, score) for gid, (row, score) in link.items()}

    for district, row in ours:
        gazetteer_id, score = linked_rows.get(id(row), (None, None))
        db.add(
            LgdVillage(
                gazetteer_id=gazetteer_id,
                match_score=score,
                lgd_village_code=clean(row.get("Village Code")),
                village_name=clean(row.get("Village Name (In English)")),
                census_2011_village_code=clean(row.get("Village Census 2011 Code")),
                lgd_subdistrict_code=clean(row.get("Subdistrict Code")),
                subdistrict_name=clean(row.get("Subdistrict Name (In English)")),
                census_2011_subdistrict_code=clean(
                    row.get("Subdistrict Census 2011 Code")
                ),
                lgd_block_code=clean(row.get("Development Block Code")),
                block_name=clean(row.get("Development Block Name (In English)")),
                lgd_district_code=clean(row.get("District Code")),
                district_name=district,
                census_2011_district_code=clean(row.get("District Census 2011 Code")),
                lgd_state_code=clean(row.get("State Code")),
                state_name=STATE,
            )
        )

    db.commit()
    db.close()

    print(f"\nloaded {len(ours)} LGD villages into lgd_village")
    print(f"  unambiguously linked to a gazetteer village: {matched}")
    print(f"  left unlinked -- name matches several talukas: {ambiguous}")
    print(
        f"  of the linked, our geometric block disagrees with the official "
        f"taluka: {corrected_taluka}"
    )


if __name__ == "__main__":
    main()
