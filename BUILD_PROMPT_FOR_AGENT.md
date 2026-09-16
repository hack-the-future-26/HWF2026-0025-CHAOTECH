# Build prompt — AwaazIQ Phase 3 hardening

Copy everything below this line into the other agent.

---

You are working in the AwaazIQ / ComplainBox repo (civic complaint
prioritization engine for rural Maharashtra). Two research docs at the repo
root fully specify what to build — **read both in full before writing any
code**:

- `STALE_INFRA_DEFICIT_RESEARCH.md` — recency/trust-blend scoring
- `ASSET_LEVEL_PRIORITIZATION_RESEARCH.md` — per-road/school/hospital
  granularity, and the verified real data sources

Everything below is a concrete task list derived from those docs. Where
this prompt and a doc disagree, the doc is authoritative — it has full
reasoning, worked examples, and verification notes this prompt compresses.

## Ground rules (non-negotiable)

1. **Test-driven.** This repo has real assertion-based test suites
   (`intelligence/test_intelligence.py`, `pipeline/test_*.py`). Add tests
   for every new function before or alongside the implementation. Run the
   full existing suite before and after your changes — nothing existing
   may regress.
2. **Additive, never multiplicative, and never zeroing.** Every scoring
   term in `scoring.py` is a philosophy stated in that file's own
   docstring: confidence gates damp, never zero; missing data must never
   read as "no need." Your new blend logic must preserve this — a
   contradicted government record loses influence proportionally, never to
   zero.
3. **Exact decomposability.** `breakdown`'s values must keep summing to
   `priority_score` (asserted in `test_intelligence.py`). If you add a new
   breakdown key, update that assertion; don't break it silently.
4. **Never fabricate a result.** When you re-run the Shinganapur-style test
   (Task 6), report the actual score and rank you get — do not estimate or
   round to a number that "sounds right."
5. **Don't touch `backend/data/*`** (large binary/CSV data files) except to
   add new files your new loaders produce.
6. **Match existing code style**: comment density explaining *why* (not
   just *what*), no inline magic numbers — every tunable constant goes in
   `intelligence/config.py` with a comment justifying its value, same
   pattern already used throughout that file.
7. Commit in small, logical steps, one task below per commit or small
   group of commits — don't do this as one giant commit.

---

## Task 1 — Precise report location (Feature 3)

Everything else depends on this. Currently every report resolves to its
matched village's centroid coordinate. Add a real per-report coordinate:

- `frontend/js/report.js` + `report.html`: add a "tap or drag your exact
  location" map control to the citizen intake form (reuse whatever mapping
  library is already loaded; the citizen page currently has none, so a
  minimal Leaflet or plain-canvas picker is fine — keep it lightweight,
  this page deliberately has zero D3/TopoJSON dependencies per its own
  README). Submit `report_lat`/`report_lon` alongside the existing fields.
- `backend/routes_citizen_report.py`: accept the new optional
  `report_lat`/`report_lon` fields; when present, use them as the report's
  coordinate instead of the resolved village centroid. When absent, fall
  back to current behavior exactly as-is (don't make the pin mandatory —
  it must degrade gracefully).
- `backend/models.py`: add `precise_lat`/`precise_lon` (nullable) to
  `CitizenRequest`, distinct from the existing `latitude`/`longitude`
  (which stay the village-resolved fallback). `migrate.py`/`create_tables.py`
  need the new columns added the same additive way existing columns were.
- `intelligence/recompute.py`, `_load_reports()`: select the new columns;
  when `precise_lat`/`precise_lon` are present, use them for that report's
  position in `_work_groups()` instead of the village centroid. This is
  the only change `_work_groups()` itself needs — its 250m clustering logic
  is already correct and needs no modification (confirmed in
  `ASSET_LEVEL_PRIORITIZATION_RESEARCH.md` §0).

Test: a synthetic pair of reports in the same village with different
precise coordinates >250m apart must resolve into two separate work
groups; the same pair with no precise coordinates must resolve into one
(today's behavior, unchanged).

## Task 2 — DEFERRED, DO NOT BUILD YET: Recency + Trust-Blend scoring (Feature 1)

**Status update 2026-09-14: the open questions are answered, but don't
start until the owner hands this task over.**
- Half-life is **10 years** (`RECORD_TRUST_HALF_LIFE_YEARS = 10.0`).
- The burst/velocity signal is used **only** in the stale-record blend.
  **Do NOT add a velocity bonus to `urgency_points`** (Step 4 of the
  original spec below is cancelled). Urgency now means bridge/building
  emergencies only, which is a separate feature (#7 in `FEATURE_ROADMAP.md`).
- Mission Antyodaya is "found, not confirmed", so record ages come from
  the sources actually loaded: Census 2011, PMGSY sanction year, JJM, and
  UDISE+ 2024-25 once it's loaded.

<details><summary>Original spec, kept for when this unblocks</summary>



Full formulas and worked example are in `STALE_INFRA_DEFICIT_RESEARCH.md`
§2 and §2A's sibling section. Summary of the concrete changes:

- `intelligence/recompute.py`, `_load_reports()`: add `created_at` to the
  selected fields (it already exists on `CitizenRequest`, just isn't read).
- `intelligence/config.py`: add
  ```python
  BURST_WINDOW_HOURS = 72.0
  VELOCITY_RATIO_CEILING = 20.0
  RECORD_TRUST_HALF_LIFE_YEARS = 10.0  # owner decision 2026-09-14 (was 20)
  URGENCY_VELOCITY_POINTS = 3.0
  ```
  with a comment on each matching the existing file's style (see how
  `GATE_RADIUS_KM`'s comment documents the tuning table that produced it —
  match that level of justification, even if the justification here is
  "this is a policy choice pending real-world validation," as the doc says).
- `intelligence/scoring.py` or a new `intelligence/velocity.py` (your
  choice, keep `scoring.py`'s existing function-per-term style): implement
  `burst_ratio()`, `velocity_term()`, `record_freshness()` exactly as
  specified in `STALE_INFRA_DEFICIT_RESEARCH.md` §2 Steps 1–2. Burst ratio
  MUST dedupe by distinct reporter (reuse `_unique_reporters`'s logic, not
  raw report rows) — this is a stated anti-gaming requirement in the doc,
  not optional.
- `scoring.py`, replace the binary `if real_infra_deficit is not None`
  block (currently at the top of `score_cluster`, search for
  `data_basis["infra_deficit"] = "government_records"`) with the blended
  version from §2 Step 3. **Apply this same blend to `real_vulnerability`
  too** — the doc's §5 point 3 explicitly resolves this as decided, not
  optional, and the formula doesn't reference `category` so it generalizes
  for free.
- `scoring.py`, `urgency_points()`: add the velocity bonus per §2 Step 4.
- `recompute.py`: thread `velocity`/`record_freshness` inputs through to
  `score_cluster()` calls — you'll need the vintage year per real-data
  source; for Census-derived values use a computed
  `current_year - 2011` rather than a hardcoded `15` (the doc flags this
  exact anti-pattern).
- `app.js`: extend the `data_basis` provenance badge rendering (search for
  where it renders "from a real record" / "proxy" — currently two states)
  to a third state for a blended value, showing the trust percentage and
  the record's age, per §2 Step 5.
- Tests: add cases to `test_intelligence.py` for `burst_ratio`,
  `record_freshness`, and the blended `infra_deficit` — at minimum: no
  burst + old record → record wins near-fully; genuine burst + old record
  → blend shifts meaningfully; genuine burst + fresh record → record still
  wins near-fully (the "freshness floor" guarantee in the doc).

</details>

## Task 3 — MOSDAC GSMaP rainfall loader (Feature 6)

New loader, `backend/load_mosdac_rainfall.py`.

**Verification correction (2026-09-14, live-checked):** the GSMaP-ISRO Rain
*product page* (`mosdac.gov.in/gsmap-isro-rain`) loads fine and confirms the
dataset is real. The *open-data index page* returned HTTP 403 to a direct
automated request even with a browser user-agent — response headers show
Drupal-based anti-automation protection. **Do not assume this is a plain
unauthenticated bulk download like `load_bharatnet.py`.** First step of
this task: manually locate the actual GSMaP-ISRO Rain download endpoint
(open the site in a real browser, find the direct file/API URL — it may
need a session cookie, a referer header, or registration), confirm one
successful fetch, then model the loader's *retry/session handling* on
`load_pmgsy_works.py` (which already fights a similar anti-forgery-token
dance) rather than assuming `load_bharatnet.py`'s no-auth pattern applies.
Report back if it turns out to require a login/API key this project
doesn't have — that would downgrade this from "buildable now" to "found,
not confirmed," same as the original IMD lead.

- Source: `https://www.mosdac.gov.in/open-data` — GSMaP-ISRO Rain product,
  0.1°×0.1° grid, hourly, since March 2000, IMD-gauge-corrected.
- Fetch recent-window rainfall (align the window to `BURST_WINDOW_HOURS`
  from Task 2, so "rainfall in the last 72h" lines up with "reports in the
  last 72h") for the grid cells covering Kolhapur and Nashik districts.
- Store as a small new table (or a JSON cache file if a full table feels
  like overkill for a grid this coarse) keyed by grid cell + timestamp.
- This is a corroboration signal, not a scoring input yet — expose it via
  a new evidence field on road/water clusters (`recompute.py`'s `evidence`
  dict) saying e.g. "34mm rainfall in this area in the last 72h" alongside
  the existing infra_deficit evidence, so an officer can see it, without
  wiring it into the numeric score in this pass.

## Task 4 — NWDP groundwater loader

New loader, `backend/load_nwdp_groundwater.py`. This source was verified
live and working in `SESSION_LOG_2026-09-12.md` (real, unauthenticated,
direct CSV download, Maharashtra-specific hourly telemetry) but never had
a loader built. **Re-verified live again on 2026-09-14** — `nwdp.nwic.gov.in`
still returns HTTP 200, no login wall encountered. Of the two loaders in
this task, this is the one confirmed clean both times; build it first.

- Source: `nwdp.nwic.gov.in` — "Ground Water Level (Telemetry - Hourly),
  Maharashtra Ground Water Department" dataset, plain CSV.
- Same treatment as Task 3: recent-window groundwater level per station,
  joined to nearby villages by proximity, surfaced as evidence text on
  water clusters ("groundwater level in this area: X, trend: falling"),
  not yet a scoring input.
- Re-verify the CSV download link still resolves before building the full
  loader — it was checked once, months ago from this repo's timeline; a
  government portal's URL structure can change.

## Task 5 — PMGSY GeoSadak road geometry (Feature 5)

Full detail in `ASSET_LEVEL_PRIORITIZATION_RESEARCH.md` §3.

**Updated 2026-09-14. Read these before starting:**
- **Run this only after the Village & Asset view
  (`BUILD_PROMPT_VILLAGE_ASSET_VIEW.md`) is merged**, or at least never at
  the same time in the same working directory. Two agents editing one
  checkout at once is how stale-buffer overwrites happened before.
- **Scope is narrowed:** do **not** edit `intelligence/recompute.py`. The
  village/asset builder owns road naming. This task only provides the data
  and one lookup function:
  - `realdata.load_road_segment_index(db) -> list[dict]`
  - `realdata.nearest_road_segment(lat, lon, segments, max_distance_m=150.0) -> (segment_dict | None, distance_m | None)`

  Afterwards, the road-naming rule in the asset builder can prefer
  `nearest_road_segment` when it returns a segment. That is a small,
  separate follow-up.
- The official portal `geosadak-pmgsy.nic.in` **does not resolve (DNS
  failure, verified twice)**. Use only the DataMeet GitHub mirror
  (verified live).
- **Never invent road attributes.** If a field (surface, condition, name)
  is missing or blank for a segment, store `None`. If the shapefile has no
  road-name field at all, report that and store the road's ID/code as its
  identity instead.
- Before writing ingestion code, check `.gitignore` and the file size. Do
  not commit a large shapefile; keep it under `backend/data/` and ignored,
  as other bulk data already is.

- Download the Maharashtra `Road_DRRP` shapefile from
  `github.com/datameet/pmgsy-geosadak`.
- **First, inspect its actual attribute table for Kolhapur and Nashik**
  before writing any ingestion code — confirm what condition/surface
  fields genuinely exist per-segment (the doc flags this as unverified).
  Report back what you find before proceeding if it materially changes the
  design (e.g. if there's no usable condition attribute at all, this task
  becomes identity-only, not condition-scoring).
- New loader, `backend/load_pmgsy_geosadak.py`: parse the shapefile
  (`pyshp` or `geopandas`, whichever is already available/lighter — check
  `requirements.txt` first), store road segments with geometry (as a
  polyline or a sequence of lat/lon points — simplest: store start/end
  points and a handful of intermediate points, not a full GIS geometry
  column, since this project has no PostGIS) in a new table, e.g.
  `pmgsy_road_segment`.
- `intelligence/realdata.py`: add a function to find the nearest road
  segment to a coordinate (reuse `haversine_km` against each segment's
  points, or its start/end if you simplified geometry above) — this is
  what a road work group will match against once Task 1's precise
  coordinates exist.
- Do not remove or change the existing `load_pmgsy_works.py` (village-name
  matched works) — it's a different, still-valid data source (sanction/cost
  info the geometry layer doesn't have). This task adds geometry-based
  identity alongside it, doesn't replace it.

## Task 6 — SUPERSEDED, DO NOT BUILD

Replaced by `BUILD_PROMPT_VILLAGE_ASSET_VIEW.md`, which gives every
specific asset (school, hospital, road spot, water point) its own score
and every village a score. The original spec is kept below for history
only.

<details><summary>Original Task 6 spec (superseded)</summary>

Full detail in `ASSET_LEVEL_PRIORITIZATION_RESEARCH.md` §2. Depends on
Tasks 1 and 5.

- `backend/models.py`: add a `PriorityScore`-equivalent for `WorkGroup` —
  either a new table (`WorkGroupScore`) or nullable columns directly on
  `WorkGroup` (`priority_score`, `breakdown` JSON, `evidence` JSON) —
  match whichever pattern is less invasive given how `WorkGroup` rows are
  currently created/queried elsewhere in `routes_dashboard.py`.
- `intelligence/recompute.py`, inside the existing per-cluster loop where
  `_work_groups(members)` is called: for each resulting group, run the
  Task 2 blend logic **scoped to only that group's members** (not the
  whole cluster), matched against:
  - Roads: the nearest `pmgsy_road_segment` from Task 5, if within a
    reasonable distance (reuse `ASSET_CANDIDATE_RADIUS_M` from
    `realdata.py`); else fall back to the cluster-level `infra_deficit`
    already computed.
  - Schools/health: the matched `PublicFacility` from the existing
    `name_work_group()` call; until per-facility condition data exists
    (it doesn't yet — this is a known, documented gap, not a bug), use
    the work-group-scoped trust-blend of local reports alone as the score.
  - Water: same treatment as schools/health for now (JJM per-source data
    was not confirmed available per the doc's §4.2 — don't build on an
    unverified claim).
- Verify the "cluster keeps its funding-level score, work groups get their
  own dispatch-level score underneath it" split (doc §2's last paragraph)
  is what actually lands — don't let work-group scores silently override
  or get confused with the cluster's own `PriorityScore`.

</details>

## Task 7 — SUPERSEDED, DO NOT BUILD

Replaced by `BUILD_PROMPT_VILLAGE_ASSET_VIEW.md` Step 7, which adds village
bubbles, asset pins, and the village and asset panels. The original spec
is kept below for history only.

<details><summary>Original Task 7 spec (superseded)</summary>

Full detail in `ASSET_LEVEL_PRIORITIZATION_RESEARCH.md` §6.

- `app.js`: add a new SVG layer group (alongside the existing `gStates`,
  `gDistricts`, `gReports`, `gClusters`, `gLabels` — match that naming
  convention, e.g. `gWorkGroups`), rendered when a village cluster is open
  (the existing "cluster" navigation level).
- Each pin: colored/sized by the work group's own score from Task 6 (reuse
  the existing `d3.scaleLinear` 3-stop gradient pattern already used for
  cluster coloring — same visual language, different scope).
- Pin label: the matched facility name when one exists (schools/health,
  and roads once Task 5's geometry match resolves one); otherwise the
  work group's `sample_text` (already computed — the longest raw report
  text in that group) or a generic "Road issue, N reports" — never leave
  a pin unlabeled.
- Reuse the existing `flyToWorkGroup()` — it already pans to a work
  group's coordinates; it currently isn't paired with a visible pin at
  that destination. This task is what makes that landing meaningful.

</details>

## Task 8 — DO NOT BUILD YET: satellite-gated verification

`STALE_INFRA_DEFICIT_RESEARCH.md` §2A (Planet Labs, catastrophic road/
bridge verification) is fully specified but explicitly blocked on an
external Planet Labs Education & Research Program application (a real
university email, unknown approval turnaround) that hasn't happened yet.
**Do not implement any part of this task.** If you reach it, stop and
report back rather than proceeding.

---

## Verification, before you call this done

1. Full existing test suite passes (`intelligence/test_intelligence.py`,
   every `pipeline/test_*.py`).
2. New tests for every new function listed above pass.
3. Re-run the actual Shinganapur-style test from
   `SESSION_LOG_2026-09-12.md` (5 high-severity "bridge collapsed" reports
   in a village where Census shows no road deficit) against the now-built
   Task 2 blend, via a real `POST /recompute-scores` against a real or
   seeded local DB. **Report the actual resulting score and rank you get
   — do not estimate one.** Clean up any test reports you inject
   afterward and re-run recompute to restore the real dataset, exactly as
   the original session did.
4. Update `intelligence/WEIGHTS.md` and `README.md`'s status table to
   reflect what's now built vs. still pending (the README currently says
   several of these things "have not been started" or are proxies only —
   correct that once true).
