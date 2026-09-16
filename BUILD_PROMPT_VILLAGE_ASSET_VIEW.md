# Build prompt — Village & Asset priority view

Copy everything below this line into the coding agent.

---

You are working in the AwaazIQ / ComplainBox repo (civic complaint
prioritisation for rural Kolhapur and Nashik, Maharashtra). Backend is
FastAPI + SQLAlchemy + SQLite (`backend/`), the scoring engine is
`intelligence/`, the frontend is plain JS + D3 (`frontend/`), and the
citizen form is `frontend/report.html` + `frontend/js/report.js`.

## What you are building, in one paragraph

Today the map's bubbles are **category clusters** (e.g. "education problem
across several nearby villages"). The user wants a different hierarchy:

1. **District view:** one bubble per **village**. Colour and size come from
   the village's highest-scoring asset. Hovering shows the village name, its
   score and rank, and counts such as "2 road, 1 school, 1 health".
2. **Click a village:** the map zooms into that village **and** a detail
   panel opens showing the village's priority (why it scores what it does, its
   ranked list of assets, and its reports).
3. **Inside the village:** one pin per **specific asset**. That means a named
   school, a named hospital, a specific road spot, or a specific water point.
   Each pin has its own priority score and is coloured by it.
4. **Click an asset pin:** a detail panel opens for that one asset, with its
   full 9-term priority breakdown, evidence, reports, and how it was
   identified.

The existing category clusters, what-if and compare features must **keep
working unchanged**. They stay available behind a toggle.

## Ground rules (read these twice — earlier tasks broke them)

1. **Never fabricate government data.** A previous task generated fake
   rainfall numbers from a formula and labelled them as real MOSDAC data. It
   had to be reverted. If you cannot get a real value, store `None` and say
   so. The only synthetic data allowed anywhere in this task is in Step 6
   (demo report locations). It applies only to rows already marked
   `is_synthetic = 1`, and must be labelled as demo data in the database and
   the UI.
2. **Do not change the scoring formula.** Do not change the weights, anything
   in `intelligence/config.py` except the new constants listed below, or
   `score_cluster()` in `intelligence/scoring.py`. Asset and village scores
   reuse `score_cluster()` exactly as it is.
3. **Test-driven.** Add tests for every rule below. Run all suites before and
   after each step:
   - `python -m intelligence.test_intelligence` (currently 48/48)
   - `cd backend && python test_citizen_report_endpoint.py` (currently 33/33;
     it writes rows to `backend/hackathon.db`, so copy that file to a backup
     first)
   - `cd pipeline && python test_pipeline.py` (currently 46/46)
4. **One commit per step below.** After every commit, run `git status` and
   `git diff`. The working tree must be clean apart from the pre-existing
   untracked `*.md` research files and `.vscode/`. **Twice already, stale
   editor buffers were written back to disk after a commit, and once that
   silently reintroduced the fabricated code.** If `git status` shows modified
   tracked files after your commit, stop and fix it before continuing.
5. **Report real numbers only.** When you finish, report actual counts from
   the database (villages, assets by type and naming method, top village and
   its score). Do not estimate.
6. **If the code doesn't match this prompt** (a function name, a column, a
   route), stop and report the mismatch. Do not guess or invent a
   replacement.
7. Match the existing code style: comments explain *why*, not *what*; every
   tunable number goes in `intelligence/config.py` with a justification
   comment.

## Facts about the current code you will rely on

- `intelligence/recompute.py`:
  - `_load_reports(db)` builds report dicts that already include
    `precise_lat` and `precise_lon`.
  - `_apply_precise_coords(reports)` swaps a report's `latitude`/`longitude`
    for its precise pin when one exists.
  - `_work_groups(members)` does 250 m single-linkage grouping.
  - `recompute(db)` loops over category clusters. For each cluster it:
    1. calls `realdata.villages_near(...)`;
    2. calls `realdata.real_infra_deficit`, `real_vulnerability`,
       `real_reporting_capacity_deficit`, `real_scheme_eligibility` and
       `real_hq_distance_km`;
    3. adds the NWDP groundwater lookup for water;
    4. calls `score_cluster(...)`;
    5. builds `result["evidence"]`;
    6. writes `DemandCluster`, `WorkGroup` and `PriorityScore` rows.
- `intelligence/realdata.py`: `load_amenity_index`, `load_works_index`,
  `load_facility_index`, `load_groundwater_index`, `lookup_groundwater`,
  `villages_near`, `name_work_group`, `haversine_km`.
- `backend/models.py`:
  - `CitizenRequest` has `village`, `district`, `block`, `issue_category`,
    `severity`, `confidence`, `latitude`/`longitude` (the village centre),
    `precise_lat`/`precise_lon` (the citizen's pin), `is_synthetic`,
    `cluster_id`, `raw_text` and `created_at`.
  - `PublicFacility` holds 9,352 UDISE schools (`category='education'`) and
    1,112 NIC HealthGIS facilities (`category='health'`), each with `name`,
    `external_id`, `village`, `latitude` and `longitude`.
  - `GovernmentProject` holds PMGSY road works; 184 of them are matched to a
    village.
  - `Gazetteer` holds the villages (`id`, `name`, `district`, `block`,
    `population`, `latitude`, `longitude`).
- Migrations:
  - `backend/migrate.py` `ADDED_COLUMNS` adds columns to existing tables at
    server start.
  - Brand-new tables are created by `Base.metadata.create_all` and need no
    migration entry.
- `backend/main.py` registers routers with `app.include_router(...)`.
- `frontend/js/app.js`:
  - SVG layer groups: `gStates`, `gDistricts`, `gOutline`, `gReports`,
    `gClusters`, `gLabels`.
  - Map helpers: `navigate(level, opts)` (levels `india`, `state`,
    `district`, `cluster`), `fitTransform`, `pointTransform`, `animateTo`,
    `applyZoomCompensation`, `scoreColour`.
  - Panel helpers: `renderPanel(c)`, `renderEvidence`, `loadClusterDetail`,
    `loadClusterReports`, `flyToWorkGroup`, `closePanel`.
  - Navigation helpers: `renderBreadcrumb`, `renderStatus`.
  - Public API object: `window.AwaazIQ`.
  - Cluster zoom uses the district's fit, `pointTransform(lon, lat, base * 3.2)`,
    which is idempotent. Reuse that pattern.
- `frontend/js/panels.js`: the rail list (`renderList`, `#railList`), compare
  and what-if. All of it is cluster-based. Leave it working for cluster mode.
- `frontend/js/report.js`:
  - `loadVillages` keeps `villageCoords`.
  - `collect()` builds the form data; everything in `data` is appended to the
    FormData once, in `submit()`.
  - The Leaflet pin map sets `reportLat`/`reportLon`.

---

## Step 1 — Models and migrations

1. `CitizenRequest`: add three nullable columns and add them to
   `migrate.py` `ADDED_COLUMNS["citizen_request"]`:
   - `facility_id` (Integer): the school or hospital the citizen picked.
   - `pin_source` (Text): `"citizen_gps"` or `"synthetic_seed"`.
   - `asset_id` (Integer): the asset this report was grouped into, rebuilt on
     every recompute, like `cluster_id`.
2. In `routes_citizen_report.py`, where a validated pin is saved, also set
   `pin_source = "citizen_gps"`.
3. New model `Asset` (table `asset`), rebuilt on every recompute:

   | Column | Meaning |
   |---|---|
   | `id` | Primary key |
   | `asset_type` | `road`, `water`, `health` or `education` |
   | `name` | Display name |
   | `name_basis` | `citizen_selected`, `nearest_register`, `pmgsy_work`, `unnamed_pin` or `unresolved_village` |
   | `facility_id` | Nullable |
   | `source`, `external_id` | Nullable; register source and ID |
   | `latitude`, `longitude` | Pin position |
   | `location_basis` | `register_coordinates`, `citizen_gps_pin`, `synthetic_seed`, `village_centroid` or `mixed` |
   | `primary_gazetteer_id`, `village`, `block`, `district` | The asset's main village |
   | `villages_served` | JSON list of village names |
   | `report_count`, `distinct_reporters` | Demand |
   | `priority_score` | Float |
   | `breakdown`, `evidence`, `candidates` | JSON text |
   | `is_demo` | Boolean; True if every member report is synthetic |
   | `created_at` | Timestamp |

4. New model `VillagePriority` (table `village_priority`), rebuilt on every
   recompute:

   | Column | Meaning |
   |---|---|
   | `id` | Primary key |
   | `gazetteer_id`, `village`, `block`, `district` | Identity |
   | `latitude`, `longitude` | From the gazetteer |
   | `population` | From the gazetteer |
   | `report_count` | Reports from this village |
   | `counts_by_category` | JSON |
   | `asset_count` | Number of assets with reports from this village |
   | `priority_score` | The highest `priority_score` among those assets |
   | `top_asset_id` | That asset's ID |
   | `rank_in_district` | Rank within the district |
   | `is_demo` | Boolean |
   | `created_at` | Timestamp |

5. New constants in `intelligence/config.py`, each with a why-comment:

   | Constant | Value | What it controls |
   |---|---|---|
   | `ASSET_PIN_SNAP_M` | 300.0 | A pin this close to a registered school or hospital is treated as that facility |
   | `HEALTH_NEAREST_MAX_KM` | 8.0 | Same as `CATCHMENT_RADIUS_KM["health"]` |
   | `SCHOOL_CANDIDATE_RADIUS_M` | 2000.0 | Search radius for the unresolved-school candidate list |
   | `SCHOOL_CANDIDATE_MAX` | 6 | Length of that list |
   | `ASSET_GROUP_RADIUS_M` | 250.0 | Road/water grouping radius; same value as the existing `WORK_GROUP_RADIUS_M` |
   | `FACILITY_MAX_DISTANCE_KM` | 10.0 | A picked facility further than this from the picked village is rejected |
   | `ASSET_PUBLIC_COORD_DECIMALS` | 3 | Rounding (~110 m) for pin-based asset coordinates in API responses; privacy, see Step 4 |

Commit: `feat(models): add asset and village_priority tables, report facility/pin_source/asset_id columns`

## Step 2 — Extract the per-cluster scoring block into a reusable helper (no behaviour change)

In `recompute.py`, move the per-cluster block into one function:

```python
def _score_members(members, lat, lon, category, *, gazetteer, amenity_index,
                   works_index, gw_stations) -> dict
```

It covers everything from `nearby_villages = realdata.villages_near(...)` to
the finished `result` dict, including evidence and groundwater. It returns
`result`, exactly what the loop builds today. The cluster loop then calls it.

**Required proof of no behaviour change:**
1. Before the refactor, run recompute and dump
   `SELECT cluster_id, priority_score, breakdown FROM priority_score ORDER BY cluster_id`
   to a file.
2. After the refactor, run recompute again and dump the same query.
3. The two dumps must be identical. Put the diff result in your report. If
   they differ, stop and fix the refactor before doing anything else.

Commit: `refactor(recompute): extract _score_members for reuse (byte-identical cluster scores)`

## Step 3 — Build assets and village priorities in recompute

Add `_build_assets(reports, ...)` and `_build_villages(...)` to
`recompute.py`. Call them at the end of `recompute()`, after clusters.
Clear the `asset` and `village_priority` tables and reset
`CitizenRequest.asset_id` first, the same way clusters are cleared.

**Asset identity rules.** Apply them to every report that has coordinates
(after `_apply_precise_coords`), in this order; the first rule that matches
wins:

1. **Citizen-selected facility.**
   - When: `facility_id` is set, the facility exists, and its `category`
     equals the report's `issue_category`.
   - Asset key: `("facility", facility_id)`.
   - `name_basis`: `citizen_selected`.
2. **Pin next to a facility.**
   - When: the category is education or health, the report has a precise pin
     (`precise_lat` is not null), and a facility of the same category is
     within `ASSET_PIN_SNAP_M` of the pin.
   - Asset key: that facility.
   - `name_basis`: `nearest_register`.
3. **Health with no pin.**
   - When: the category is health and neither rule above matched.
   - Asset key: the nearest health facility within `HEALTH_NEAREST_MAX_KM` of
     the report's position.
   - `name_basis`: `nearest_register`.
   - The UI must show this as "nearest facility — not confirmed by citizen".
4. **Education with no facility and no pin.**
   - Asset key: `("unresolved", village_gazetteer_id_or_name, "education")`.
   - `name`: `"School in <village> (not specified)"`.
   - `candidates`: schools within `SCHOOL_CANDIDATE_RADIUS_M` of the village
     centre, sorted by distance, at most `SCHOOL_CANDIDATE_MAX`, each with
     `name`, `external_id` and `distance_m`.
   - `name_basis`: `unresolved_village`.
5. **Road and water.**
   - Group reports within the same village and category by
     `ASSET_GROUP_RADIUS_M` single-linkage on their coordinates (reuse the
     `_work_groups` distance logic).
   - Each group is one asset.
   - Road name: if the village has exactly one PMGSY work in `works_by_village`,
     use that work's name with `name_basis = pmgsy_work`. Otherwise use
     `"Road near <village>"`, adding `" #2"`, `" #3"` etc. when there are
     several groups, with `name_basis = unnamed_pin`.
   - Water name: `"Water point near <village>"`, numbered the same way, with
     `name_basis = unnamed_pin`.
   - Without pins, all of a village's road reports collapse into one asset.
     That is correct and honest.

Reports with no coordinates at all are skipped, as today.

**Asset position and location basis:**
- Facility-keyed assets use the facility's own coordinates, with
  `location_basis = register_coordinates`.
- Pin-based assets use the centroid of their reports.
- `location_basis` comes from the reports' `pin_source`: all `citizen_gps` →
  `citizen_gps_pin`; all `synthetic_seed` → `synthetic_seed`; all without a
  pin → `village_centroid`; anything else → `mixed`.

**Facility assets can serve several villages.** A hospital can receive
reports from several villages. It is **one** asset. `villages_served` lists
them, and its primary village is the most common one among its reports.

**Asset score:**
- Call `_score_members(asset_members, asset_lat, asset_lon, asset_type, ...)`.
  This is the same 9-term formula and the same real-data lookups around the
  asset's own location.
- Store `priority_score`, `breakdown` and `evidence`.
- Add `name_basis`, `location_basis`, `candidates` and `villages_served` to
  `evidence`.
- Set `CitizenRequest.asset_id` on each member report.

**Village priority.** For every gazetteer village with at least one report:
- The village's assets are the distinct assets of its reports.
- `priority_score` = the maximum of those assets' scores, and `top_asset_id`
  = that asset.
- `counts_by_category` = report counts by `issue_category`.
- `rank_in_district` = rank by `priority_score`, descending, within the
  district.
- `is_demo` = True if every report from the village is synthetic.
- The village's "why this score" is the top asset's breakdown, presented as
  "driven by its highest-need asset: <name>". **Do not invent a separate
  combined breakdown.** Road and health deficits can't be meaningfully added
  together, and the displayed breakdown must still sum to the displayed score.

**Tests** (in `intelligence/test_intelligence.py`, using small hand-built
fixtures, not the database):
- Each of the five rules above.
- A citizen-selected facility beats a nearby pin.
- An asset's breakdown sums to its `priority_score`.
- A village's score equals the maximum of its assets' scores.
- A hospital with reports from two villages is one asset with two
  `villages_served`.
- The candidate list is sorted by distance and capped at 6.
- Two road pins 400 m apart in one village give two assets; the same pins
  100 m apart give one.

Commit: `feat(intelligence): build per-asset and per-village priority scores`

## Step 4 — API

Create a new file `backend/routes_villages.py` and register it in `main.py`.

- `GET /villages?district=<name>` returns a list. Each item has:
  - `gazetteer_id`, `name`, `block`, `district`, `lat`, `lon`
  - `priority_score`, `rank_in_district`
  - `report_count`, `counts_by_category`, `asset_count`
  - `top_asset: {id, name, asset_type, priority_score}`
  - `is_demo`
- `GET /villages/{gazetteer_id}` returns:
  - the village fields above;
  - `assets`: the village's assets ranked by score, each with `id`, `name`,
    `asset_type`, `priority_score`, `report_count`, `name_basis`,
    `location_basis`, `lat`, `lon` and `is_demo`;
  - `top_asset`, with its `breakdown` and `evidence`;
  - `reports`: this village's reports, serialised with the existing
    `serialize_citizen_request()`, which already omits precise coordinates.
- `GET /assets/{asset_id}` returns all asset fields plus the parsed
  `breakdown`, `evidence` and `candidates`, its `reports` (serialised the same
  way), and `rank_in_village` for its primary village.
- `GET /gazetteer/facilities?district=&block=&village=&category=` goes in
  `routes_gazetteer.py`, for the citizen form:
  - `education`: schools whose `village` matches the chosen village
    (case-insensitive) or that lie within `SCHOOL_CANDIDATE_RADIUS_M` of the
    village centre.
  - `health`: facilities within `HEALTH_NEAREST_MAX_KM`, nearest first.
  - Return `id`, `name`, `sub_type` and `distance_m`.

**Privacy rule:** for assets whose `location_basis` is **not**
`register_coordinates` (road and water pins, which can be a citizen's exact
spot), round `lat`/`lon` to `ASSET_PUBLIC_COORD_DECIMALS` in every response.
Register-based assets (schools, hospitals) keep their public register
coordinates. Never return `precise_lat`/`precise_lon` or anything from
`citizen_identity`.

Endpoint tests to add to `backend/test_citizen_report_endpoint.py`:
- Response shapes for the three new routes.
- Pin-based asset coordinates are rounded.
- No `precise_lat` appears anywhere.
- Unknown IDs return 404.

Commit: `feat(api): village and asset priority endpoints, facility lookup for intake`

## Step 5 — Citizen form: pick the school or hospital

- `report.html` / `report.js`: when the chosen category is `education` or
  `health` and a village is chosen, fetch `/gazetteer/facilities` and show a
  `<select id="facility">`. Its first option is "Not sure / not listed" (the
  default). The field is optional. Add `facility_id` inside `collect()`, and
  only when a real option is selected. Do **not** append it again in
  `submit()`.
- `routes_citizen_report.py`, intake path only: parse `facility_id`. Accept
  it only if all of these hold:
  - it is a valid integer;
  - the facility exists;
  - `facility.category` equals the report's extracted `issue_category`;
  - the facility is within `FACILITY_MAX_DISTANCE_KM` of the picked village
    centre.

  Otherwise store `None`. Echo `facility_id` in the POST response only.
- Endpoint tests: valid pick saved; wrong category dropped; too far dropped;
  nonexistent dropped; non-numeric dropped; JSON path unaffected.

Commit: `feat(intake): let citizens pick the specific school or hospital`

## Step 6 — Demo locations for the synthetic reports (clearly labelled)

The 1,000 seeded reports are already fake demo complaints
(`is_synthetic = 1`), and all of them sit at their village centre, so the new
view has nothing to show. Create `backend/assign_synthetic_assets.py`:

- It touches **only** rows where `is_synthetic = 1 AND precise_lat IS NULL`.
  Never touch real citizen rows.
- Use `random.Random(report.id)` so every run gives the same result.
- Education/health reports:
  - Pick a real `PublicFacility` of the matching category: a school in or
    within 2 km of the report's village, or a hospital among the 3 nearest
    within 8 km.
  - Set `facility_id` to it, set `precise_lat`/`precise_lon` to its
    coordinates plus up to 30 m of jitter, and set
    `pin_source = "synthetic_seed"`.
  - If no facility is found, leave the row untouched.
- Road/water reports: for each (village, category), make 2–3 hotspot points
  within 1 km of the village centre. Place each report within 80 m of one
  hotspot, so several reports merge into one realistic asset. Set
  `pin_source = "synthetic_seed"`.
- Support a `--dry-run` flag that prints counts without writing, and an
  `--undo` flag that sets `precise_lat`, `precise_lon`, `facility_id` and
  `pin_source` back to NULL wherever `pin_source = 'synthetic_seed'`.
- Before running it, record the current number of category clusters and the
  top cluster score. Run it, run recompute, then report the new values. They
  will change slightly, because `_apply_precise_coords` moves the synthetic
  reports. That is expected, but report it.
- Every asset or village with `is_demo = True` must show a visible **"demo
  data"** badge in the UI (Step 7).

Commit the script and its test. **Do not commit `hackathon.db`**; check
`.gitignore`.
Commit: `feat(demo): deterministic, labelled demo locations for synthetic reports`

## Step 7 — Frontend map and panels

In `frontend/js/app.js`:

1. **Mode toggle.** At district level, add a small toggle, "Villages |
   Category clusters". Villages is the default. Category-cluster mode must be
   today's behaviour, byte-for-byte, so compare and what-if keep working.
2. **District level, village mode.**
   - Fetch `/villages?district=` and cache it per district.
   - Draw a new layer group `gVillages`, created alongside the other `g*`
     layers.
   - One circle per village. Radius uses a `d3.scaleSqrt` of `report_count`;
     fill uses the existing `scoreColour(priority_score)`.
   - Tooltip: `"<name> — score X (rank #n in district) · 2 road, 1 school, 1 health"`,
     plus a demo badge when `is_demo`.
   - Hide `gClusters` in this mode.
3. **New level `village`** via `navigate("village", {village})`:
   - Zoom with `pointTransform(v.lon, v.lat, base * 5)`, where `base` is the
     district's `fitTransform(...).k`. This is the same idempotent pattern the
     cluster level uses. Tune the 5 if pins overlap.
   - Fetch `/villages/{id}`.
   - Draw a new layer `gAssets`: one pin per asset, with an icon or letter by
     type (🏫 education, 🏥 health, 🛣 road, 💧 water), fill from
     `scoreColour(priority_score)`, and the name as a label.
   - Assets whose position lies outside the village (for example a hospital
     in the next village) still show, and the tooltip says "serves <village>".
   - Open the **village panel**:
     - Header: village, block, district, score, "rank #n of N villages in
       <district>", demo badge.
     - Column 1, "Why this score": the top asset's 9-term breakdown, titled
       "driven by its highest-need asset: <name>".
     - Column 2, "Fix first inside this village": the ranked asset list. Each
       row shows icon, name, score, reports and a `name_basis` badge
       ("citizen picked", "nearest register — not confirmed", "PMGSY work",
       "location only", "not specified"). Clicking a row opens that asset.
     - Column 3: the village's reports.
4. **New level `asset`** via `navigate("asset", {asset})`:
   - Keep the village's pins and highlight the selected one; fade the rest
     with the same approach as `cluster--faded`.
   - Open the **asset panel**:
     - Header: name, type, score, rank in village, demo badge.
     - Column 1: the full 9-term breakdown with the same provenance badges as
       today.
     - Column 2: facts. These are the register ID and source, `name_basis`,
       `location_basis`, the candidate list if unresolved, the groundwater
       evidence for water, the already-funded PMGSY banner if the evidence has
       undelivered works, and `villages_served`.
     - Column 3: its reports.
5. **Reuse, don't duplicate.** The 9-term breakdown rendering currently lives
   inside `renderPanel(c)`. Extract it into one function,
   `renderBreakdown(target, breakdown, evidence)`, and use it in the
   cluster, village and asset panels. The cluster panel must look exactly as
   it does today.
6. Update `renderBreadcrumb` to show India › Maharashtra › District › Village
   › Asset.
7. Add deep links `?at=village:<gazetteer_id>` and `?at=asset:<id>`.
8. `panels.js`: in village mode, the rail lists the current district's
   villages ranked by score, and clicking one navigates to it. In cluster mode
   the rail stays as it is today.

Manual check: serve the frontend, open Kolhapur, click a village, click an
asset. Take screenshots of all three levels and include them in your report.

Commit: `feat(frontend): village bubbles, asset pins, village and asset priority panels`

---

## Final report (required)

1. Test results for all three suites. Give the real numbers.
2. The Step 2 byte-identical proof.
3. Database counts:
   - villages with a priority;
   - assets by `asset_type` × `name_basis`;
   - assets by `location_basis`;
   - how many assets are demo.
4. The top 5 villages in each district, each with its score and top asset.
5. Cluster count and top score before and after Step 6.
6. `git status` output after the last commit. It must be clean.
7. Screenshots from Step 7.
8. Anything in this prompt that didn't match the code, and what you did about
   it.
