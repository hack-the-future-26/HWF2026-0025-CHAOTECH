# AwaazIQ feature roadmap (master list)

The single place to see every feature, its status, and the decisions still
waiting on the owner. Detailed designs live in the linked docs. Updated
2026-09-14.

Status: ✅ done · 🔨 being built · 📝 designed, not started · ⏸ parked ·
⛔ blocked on something outside the code · ❌ ruled out

## Features

| # | Feature | Status | Where the detail lives |
|---|---|---|---|
| 1 | **Exact report location**: map pin, "use my location", 5 km check, privacy-safe | ✅ Done (commits 4b83dff → 0040806, verified) | `BUILD_PROMPT_FOR_AGENT.md` Task 1 |
| 2 | **Village → specific asset view**: village bubbles; click to zoom and open the village priority panel; pins for each specific school, hospital, road spot and water point, each with its own priority | ✅ Built (910142e → 4dbe99e) + map fixes 2026-09-14 (**not yet committed**) | `BUILD_PROMPT_VILLAGE_ASSET_VIEW.md` |
| 3 | **Road names + real road shape from PMGSY GeoSadak**: pinned road assets named after the nearest road within 150 m, road drawn on the map | ✅ Built by Claude 2026-09-15 (91725ab → afea92a); the agent's earlier version was reverted (d4f58dc, 15a1d74, d765386). Village-centre assets are deliberately never matched. All 101 current matches come from demo reports. **Live DB needs one recompute** after the UDISE loader finishes | `BUILD_PROMPT_ROAD_GEOMETRY.md` (spec only) |
| 4 | **Groundwater evidence (NWDP)** on water clusters | ✅ Done (03c26d9; data checked against the live source) | `BUILD_PROMPT_FOR_AGENT.md` Task 4 |
| 5 | **Rainfall evidence (MOSDAC GSMaP)** | ⛔ Needs an ISRO MOSDAC account (email sign-up, approval by email) | `LIVE_GOV_DATA_RESEARCH.md` §3 |
| 6 | **Fake-complaint defence** (see below) | 📝 Designed; build prompt not written yet | `PHOTO_VERIFICATION_RESEARCH.md` |
| 7 | **Emergency urgency** (owner's definition, see below) | 📝 Needs one decision | this file, below |
| 8 | **Stale-record discount (trust-blend)**: trust an old government record less when a burst of recent reports contradicts it | 📝 Unblocked (half-life = 10 years decided) | `STALE_INFRA_DEFICIT_RESEARCH.md` §2 |
| 9 | **Fresher or live government data**: JJM water testing, CWC river levels, SACHET alerts loaded; wired into evidence 2026-09-15 | ✅ Loaded (Tasks 1-3, commits 4b880f2/ea4cd95/aac9de9) + wired as evidence-only corroboration (see below); eMARG blocked by CAPTCHA; **JJM scheme money (Task 4) was never attempted** — no commit, no table, not reported either way | `BUILD_PROMPT_DATA_PIPELINES_REMAINING.md` |
| 10 | **Satellite confirmation (Planet Labs)**, disasters only | ⛔ Needs the Planet Education & Research application (university email) | `STALE_INFRA_DEFICIT_RESEARCH.md` §2A |
| 11 | **Silent Need Detector** (equity term): flag villages that likely need help but under-report, instead of the current flat BharatNet-only proxy | 📝 Designed; not in Workstream B yet, no build prompt written | `SILENT_NEED_DETECTOR_RESEARCH.md` |
| 12 | **District-level GPDP investment totals** (panchayats, approved activities, ₹ outlay, popular/under-picked activities) | ✅ Loaded (99f97e4), real, tested. Deliberately **not** wired into scoring — a district total can't be honestly split to a village or asset. No UI/API shows it yet: there is no district-level view to put it in (see #15 in "Ruled out" below) | `BUILD_PROMPT_GPDP_DISTRICT_SUMMARY.md` |
| 13 | **Whole-village GPDP budget plan** (per-village works, sector, cost — CAPTCHA, human-assisted) | 🔨 Capture tool built (5064c7f) but never run: `village_budget_plan` has 0 rows. **The tool also skipped the prompt's required Step 0** (test whether one captcha covers many villages or only one) — it does a fresh `page.goto()` before every village, so it silently assumes the worst case (441 solves) without ever checking the cheaper alternative | `BUILD_PROMPT_DATA_PIPELINES_REMAINING.md` Task 5 |
| 14 | **PRIASoft village-panchayat receipt & expenditure** (real ₹ in/out per village, tied/untied 15th Finance Commission grant, no CAPTCHA) | ✅ Loaded (040b3b0), verified live and against the db 2026-09-15: 4,826 rows, Kolhapur matched 1,366/2,050 (66.6%), Nashik 651/2,776 (23.5% — genuine, Nashik's own gazetteer only has 289 villages vs 1,298 PRIASoft names there, not a bug). **Wired into `intelligence/investment.py` and `GET /investment-alignment` 2026-09-15**: every village row now carries its real unspent-grant balance (`unspent_grant_rupees`, whole-panchayat, independent of PMGSY roads) — 685 of 968 villages shown on the panel have a real PRIASoft record. Not folded into the existing funded/demanded classification (that logic has its own known bug, see the white-space table below) — exposed as its own real field alongside it | `BUILD_PROMPT_PRIASOFT_RECEIPT_EXPENDITURE.md` |
| 16 | **UDISE+ school condition → education infra_deficit** | ✅ Wired 2026-09-15: a specific school's own 2024-25 classroom/electricity/water/toilet condition now overrides the village-wide 2011 Census signal for that exact school (215 of 220 real education assets use it after a recompute); falls back to Census only for the 5 "unresolved" assets with no specific facility matched. New `intelligence/realdata.py::school_condition_deficit`, tests in `test_intelligence.py`. (Numbered #16, not #15, to avoid confusion with the research PDF's own feature #15 referenced in row #12 above.) | `BUILD_PROMPT_UDISE_SCHOOL_DATA.md` |

## The project's white space (research PDF §9), checked against the code 2026-09-14

The research PDF defines the white space as **Capability E: prioritising
citizen demand against government investment data, fused with intake, GIS
and demographics, at cluster level.** Its main features are:
- **#2** Demand-to-Investment Alignment (funded-but-undelivered /
  demanded-but-unfunded / funded-where-not-demanded, matched on
  **location + asset type**)
- **#18** double-funding detector
- **#12** scheme eligibility
- **#9** what-if (population reached per rupee)

The village → asset view (#2 in the table above) is the PDF's **feature
#21, "Grievance-to-Infrastructure Reclassification"**, which the PDF calls
"the deepest structural move in the whole design". It is the unit that
Capability E should match against.

| White-space piece | Status in code | Problem |
|---|---|---|
| #2 Alignment engine (`intelligence/investment.py`, `GET /investment-alignment`, "Demand vs Investment" panel) | Built | **Bug:** it counts *all* complaints within 5 km (health, school, water, road) against **road-only** PMGSY works, so "demanded but unfunded" is inflated (762 villages; e.g. Hajgoli Bk: 141 reports of every type vs 0 road works). Also only 184 of 552 PMGSY works are pinned to a village. |
| #18 Double-funding ("Already funded here") | Built | Roads only. It means "this village has sanctioned works", not "this specific road". |
| #12 Scheme eligibility | Built, all 4 categories | Rule-based (PMGSY / IPHS / RTE / JJM) and worth only +4 points. |
| #9 What-if | Partial | A fixed number of points per crore, not the PDF's "population reached per rupee" re-ranking. |
| Investment as a real input to the ranking | Weak | Only stalled road works raise the road infra_deficit. There is **no budget or investment data for health, schools or water.** |

### Two follow-up scrapes, done hands-on 2026-09-14

**JJM water quality — ✅ confirmed working, real current data pulled.**
The site encrypts every dropdown value with a fixed, hardcoded AES-128 key
found in its own JavaScript (`8080808080808080`, used as both key and IV) —
not a real security measure, just an obstacle a script has to replicate,
which I did. Chaining state → district → block → panchayat, then asking
for panchayat-level results (leaving the village field empty), returns one
row **per village**, for the **current financial year, 2026-27** — real
numbers, not 2011 Census: `Ardal village (Kolhapur): 30 samples tested,
pH/FRC/Turbidity/TDS/Hardness all tested, 0 villages where testing wasn't
done`. Two honest limits: this endpoint reports **testing coverage**
(how many samples were checked, and for what), not the actual contamination
readings — the `Contaminantwise` report on the same site should have those
but wasn't tested yet. And asking for one specific village directly returns
nothing; the working method is asking one level up and reading each
village's own row back.

**MGNREGA panchayat works and spending — ⚠️ found, currently inaccessible.**
The state-level page works (Maharashtra confirmed, state code 18), but
every server that actually generates a work-list or spending report
(`mnregaweb2`, `mnregaweb4`, `nregastrep`, `nreganarep`) returned either a
connection refusal or a genuine `503 Service Unavailable` — not a CAPTCHA
or a block, an IIS application error, the same kind of thing that happens
when a government server's app pool has stopped. Worth retrying later or
from a different network before concluding it's dead. Until then, MGNREGA
is not a reliable source.

**eGramSwaraj GPDP (the actual whole-panchayat budget plan) — still
CAPTCHA-blocked**, unchanged from before. The realistic plan for the
white-space feature stays: **PMGSY (roads) is the only automatable
investment source right now.** For a demo, downloading a GPDP PDF by hand
for a handful of showcase villages is possible; it just can't be automated
for all 441.

### Budget and investment data beyond roads (checked live 2026-09-14)

| Category | Source | Status | What it gives |
|---|---|---|---|
| Roads | PMGSY works (already loaded) | ✅ | Sanctioned cost, status, year per work. Only 184 of 552 are linked to a village. |
| **Schools** | **UDISE+ report card** (`kys.udiseplus.gov.in/web-app/api/school/report-card?udiseSchCode=…`) | ✅ **Confirmed**, no login | **Per-school `totalGrant` and `totalExpediture` for 2024-25.** Example: a Kolhapur school received ₹25,000 and spent ₹25,000. Same endpoint as the teacher and classroom data. |
| Water | JJM scheme records (ejalshakti) | ❌ Found, not confirmed | Public VillageProfile.aspx inspected live: report is an FHTC infrastructure tracker (tap connections by year, source type, chlorination/WTP flags) and contains NO scheme-level financial records (no sanctioned cost, expenditure, or work order dates). |
| **Whole village (panchayat)** | eGramSwaraj GPDP plans + 15th Finance Commission grants | ⛔ **CAPTCHA-protected** | The panchayat's full plan: works, estimated cost, sector, grants. Viewable by hand; needs manual download for demo villages or a formal request to the Panchayati Raj department. |
| Whole village (panchayat) | MGNREGA works per panchayat (`nrega.dord.gov.in`) | 🟡 Likely | Public state-reports page, no CAPTCHA, Maharashtra listed. The detailed report server didn't respond from here, so no rows pulled yet. Gives work type, approved cost, spending and status. |
| **Health** | NHM Record of Proceedings | ❌ Weakest | State/district-level PDFs only; no per-facility budget found online. Fallback: NHM's fixed per-facility entitlements (e.g. untied funds per sub-centre/PHC), labelled clearly as an *entitlement*, not actual spending. |

## Decisions already taken (don't reopen them without the owner)

- **Village score = the score of its most-in-need asset** (the highest
  asset score in that village). The village's "why this score" panel shows
  that asset's breakdown. A road score and a hospital score are never
  added together.
- **Demo reports get demo locations.** The 1,000 synthetic reports are
  attached to real registered schools and hospitals, and road/water
  reports are scattered around their village. They are labelled "demo
  data" everywhere, and an `--undo` flag reverses it.
- **Citizens can pick the specific school or hospital** in the report form.
- **Nothing is ever fabricated.** If real data can't be fetched, the
  feature is marked "found, not confirmed" and dropped (as happened with
  MOSDAC and IMD).
- **Satellite is for disasters only**, never for potholes, staffing or
  water.
- **No complaint is auto-rejected.** Every verification signal only raises
  or lowers confidence and flags the report for an officer.

## #7 Emergency urgency, as the owner defined it (2026-09-14)

**Urgency means an emergency only: bridge breakage or building breakage.**
It is *not* for potholes, a missing teacher, a missing doctor, or water
supply problems. Those are handled by demand, infra_deficit and the
stale-record discount instead.

**Confirmed by the owner, 2026-09-14:** emergency *is* the urgency term.
It is calculated for bridge and building damage **including cracks**, not
only full collapse. It should be graded: crack < partial damage <
collapse. Every other kind of report gets its signal from citizen reports,
through the burst / stale-record discount (#8). There is **no separate
emergency list**; it lives inside the score.

- **Today:** `urgency_points` gives a flat +3 to every road and water
  cluster (`intelligence/scoring.py`). That will be replaced.
- **Signals that count toward an emergency** (combined, none decisive
  alone):
  1. a bridge or building damage model run on the citizen's photo;
  2. catastrophic wording (collapsed, washed away, fallen);
  3. several independent reports within a short time;
  4. an active SACHET heavy-rain or flood alert for that district;
  5. later, satellite confirmation.
- **The pothole model** feeds road *condition* (infra_deficit
  corroboration), not urgency.
- **Models:**
  - Buildings: realistic (a published ground-photo classifier at 93.5%;
    GitHub starting points exist).
  - Bridges: no ready collapse model exists; one would need training.
- **Decided 2026-09-14 (owner asked Claude to choose): urgency cap 15,
  gap score max 69** (was 3 and 81; total stays 100). Graded crack 1/3,
  partial damage 2/3, collapse full, times a 0–1 confidence from the
  signals above. Tested on the real 116 clusters: a mid-ranked cluster
  with a confirmed collapse jumps to #8 (partial damage #23), 9 of the
  current top 10 stay, average rank shift ~4. Cap 9 only reaches #28;
  cap 20 lets emergencies dominate (#4).

## #6 Fake-complaint defence (what will be built)

| Layer | Stops | Status |
|---|---|---|
| Sign-in required; demand capped at 25; cluster needs ≥3 reports | Spam accounts flooding the ranking | ✅ Already built |
| Count distinct **accounts** (`user_id`), not distinct wording | One person filing the same complaint in different words | 📝 |
| Camera-only capture (`getUserMedia`), GPS and time taken at the moment of capture | Old or downloaded photos | 📝 |
| Duplicate-photo check (`imagehash`) | The same photo reused across villages | 📝 |
| Edit detection (error-level analysis) | Photos altered in an editor | 📝 |
| Screen-replay check (moiré, multi-frame) | Photographing a photo on another screen | 📝 Needs a model to be found |
| Owner's pothole model; building and bridge damage models | A real photo that doesn't show the claimed problem | 📝 Pothole model available; others per #7 |

## #8 Stale-record discount: remaining decision

Since urgency is now emergency-only (#7), the "burst of recent reports"
signal is used **only** to discount stale government records. It does
*not* go into urgency, which settles one of the two earlier open
questions. **Decided 2026-09-14: half-life = 10 years**
(`RECORD_TRUST_HALF_LIFE_YEARS = 10.0`). A 15-year-old Census record keeps
about 35% of its weight when fully contradicted. In the Shinganapur case
(contradiction around 0.8), the record's trust falls to about 48%, which
restores roughly 10 of the 20 lost infra_deficit points instead of about
6 with a 20-year half-life. **#8 is now unblocked and ready for a build
prompt.**

## #9 Fresher or live data: next actions

Scraping check results, 2026-09-14 (see `LIVE_GOV_DATA_RESEARCH.md` §4A):

1. **SACHET** alerts: ✅ verified live, buildable now.
2. **UDISE+ Know Your School**: ✅ **confirmed scrapable.** 2024-25
   per-school classroom condition and teacher counts, looked up by the
   UDISE code we already hold for all 9,352 schools. **The biggest
   upgrade:** real, current, specific-school data for the education
   infra_deficit.
3. **CWC flood forecasting**: ✅ **confirmed scrapable.** Live river
   readings (Nashik station read today at 17:00). Still needed: pick the
   working Kolhapur/Nashik stations and map the reading codes. Feeds
   emergency corroboration (#7).
4. **JJM WQMIS water quality**: ✅ **confirmed** (per-village testing
   coverage, FY 2026-27, AES method; see the section above).
5. **eMARG**: ❌ behind a CAPTCHA, so not scrapable. It needs a formal
   data request to NRIDA.

### Data loaded 2026-09-14/15, verified and wired 2026-09-15

The build prompt for Tasks 1-3 explicitly said "don't touch `scoring.py`
or `recompute.py`" so a human would review the wiring — this is that
review, done directly rather than handed to another agent.

- **Checked real row counts before trusting the completion report**:
  `water_testing` 9,250 rows, `river_reading` 11, `hazard_alert` 10,
  `gpdp_district_summary` 4. `jjm_scheme` (Task 4) — **table doesn't
  exist**; it was never attempted, not "found, not confirmed" as the
  prompt's ground rules required if it turned out not to work.
- **water_testing's 65% unmatched `gazetteer_id` is real, not a bug**:
  JJM tracks 2,850 distinct habitation names against a 1,042-row village
  gazetteer — many are hamlets below the gazetteer's granularity (spot
  checked 15, none existed under any spelling). Correctly stored as
  `NULL`, never guessed.
- **Test-suite bug, same pattern as before**: the three new test
  functions (`test_water_testing_evidence`, `test_hazard_near`,
  `test_gpdp_district_evidence`) were stapled to the bottom of
  `test_intelligence.py` as bare module-level calls, after `main()`.
  They ran and passed, but weren't in the counted total, and — worse — a
  failure in them would **not** have flipped the exit code, so CI would
  have reported green regardless. Moved into `main()`'s list; 121 → 131
  counted tests, real ones this time.
- **`hazard_near` queried the database live, once per cluster** — every
  other real_* lookup in this file loads its index once per `recompute()`
  call and passes it down as a plain list. Refactored to
  `load_river_readings_index(db)` / `load_hazard_alerts_index(db)` +
  `hazard_near(lat, lon, river_readings, hazard_alerts)`, matching the
  groundwater pattern exactly.
- **Real bug the refactor exposed**: comparing SQLite's naive
  `observed_at`/`effective`/`expires` against `datetime.now(timezone.utc)`
  raised `TypeError` the moment this ran against the real database instead
  of mocks. Fixed with one normalization point (`_as_utc`), not per
  comparison site.
- **Wired in as evidence only, not score** — same as groundwater already
  was: `water_testing_catchment_evidence` (new, aggregates JJM coverage
  across a water cluster's whole catchment, honest about partial matches)
  feeds `evidence.water_testing`; `hazard_near` feeds
  `evidence.hazard_corroboration` for road and water clusters. Confirmed
  on a real recompute: 210 of 218 water assets now carry real water-testing
  evidence, 37 assets carry real hazard corroboration. **Neither moves the
  priority score** — the urgency-term redesign that would actually score a
  SACHET/CWC-corroborated emergency (#7) hasn't been built yet, so this is
  visible in the evidence panel and nothing else changes yet.
- **Known weakness, not fixed, worth knowing about**: `hazard_near` flags
  "hazard" whenever *any* reading exists nearby in the time window — it
  doesn't check whether the reading's value is actually unusual (a calm
  river at normal level is treated the same as a flood level). This was
  already true of the delivered code; the refactor didn't change it. Fine
  for an evidence-only signal today; would need a real threshold before
  it's allowed to move a score.
- **GPDP capture tool (Task 5) skipped its own required Step 0**: the
  prompt was explicit that the tool must first test whether one captcha
  solve covers many villages or only one, and build accordingly.
  `backend/capture_gpdp.py` does a fresh page load before every single
  village regardless, so it silently assumes the expensive case (441
  solves) without ever checking the cheaper one. Not fixed here — testing
  it needs a human actually sitting with the browser open, which is the
  next real step if the ~2.5-3 hour worst-case estimate is worth revisiting
  before committing to it.

## Priority-score research status, all 9 terms (checked 2026-09-14)

`demand · population · infra_deficit · vulnerability · equity · strategic ·
urgency · feasibility · cost_penalty`. Research status per term, so nothing
gets assumed "handled" just because a neighboring term is:

| Term | Research | Notes |
|---|---|---|
| infra_deficit | ✅ Researched & decided | `STALE_INFRA_DEFICIT_RESEARCH.md` — half-life=10yrs. Design done, code not (#8). |
| urgency | ✅ Researched & decided | Bridge/building only, cap 15. Design done, code not (#7). |
| equity (Silent Need Detector) | ✅ Researched | `SILENT_NEED_DETECTOR_RESEARCH.md`. Design done, code not, **and not yet a numbered Workstream B item** (#11 above). |
| demand (Evidence Fusion) | 🟡 Partially researched | Real gap identified: current fusion is additive, not the Bayesian multiplicative design the team PDF specifies (§10.4). Only Census/PMGSY/groundwater are actually live; JJM/UDISE/CWC/SACHET are still unbuilt prompts. Corroboration softening for 1–2 reporter assets flagged but not designed in detail. |
| strategic (scheme-eligibility / investment / white space) | 🟡 Partially built, partially researched | `investment.py` exists with a known bug. Real budget data (GPDP, JJM scheme money) is the ongoing captcha-scraping effort, unfinished. |
| population | Not an open problem | Simple log-transform, already decided, nothing to research. |
| vulnerability | Not flagged as broken | Already pulls real Census/BharatNet data; no gap identified this session. |
| feasibility | ❌ Zero research done | Terrain, land status, partial-existing-infrastructure proxy — untouched. |
| cost_penalty | ❌ Zero research done | The team's own PDF calls this "the weakest-verified data category" (§14/§16.3) — no research yet on what real cost data exists. |

## #10 Feasibility Phase 2: OSM nearest-town distance + terrain (deferred, not built)

Built 2026-09-15 (this pass): feasibility now uses `dist_nearest_town_km`
(real Census 2011 field, already loaded) with a continuous distance
gradient plus a real all-weather-road-connectivity blend, replacing the old
binary `dist_subdistrict_hq_km <= 15km` cliff and its double-count with
vulnerability's isolation signal. See `intelligence/scoring.py`'s
`feasibility_points()` and `intelligence/realdata.py`'s
`real_town_distance_km()` / `real_road_connectivity()`.

**Explicitly deferred, not built this pass**: replacing the Census
nearest-town distance with a live OSM-derived one, and adding terrain
ruggedness (SRTM/Open-Elevation) as a further feasibility signal. Reasons,
not a rejection:

- Computing "distance to nearest town" from OSM needs real geospatial
  engineering this codebase doesn't have yet — downloading and parsing a
  full OSM PBF extract (Geofabrik), filtering `place=town`/`place=city`
  nodes, then a nearest-neighbour calculation. Not an API call like every
  other loader in this project; a materially bigger lift.
- Rural India's OSM `place` tagging is crowd-sourced and unverified for
  Kolhapur/Nashik specifically — attempted a live Overpass API check this
  session, it timed out twice, which is itself a relevant signal about
  relying on the free public endpoint. This project already ruled out
  OSM once for a different purpose (village-road coverage, see "Ruled out"
  below) — worth remembering before assuming it's reliable for this
  purpose either.
- It also changes what's being measured: Census's nearest-town distance is
  a human surveyor's on-the-ground judgment; OSM's is whatever got
  crowd-tagged. Not guaranteed to agree.
- Terrain (Open-Elevation TRI) is real and free, confirmed live (`200 OK`,
  no key needed) — but needs ~4,000 batched API calls plus caching
  infrastructure that doesn't exist yet either.

**If picked up later**: verify live OSM place-tag coverage for a handful of
real Kolhapur/Nashik villages first (the same "verify live, don't assume"
standard as every other data source here) before committing engineering
time to the PBF pipeline.

## Ruled out, with reasons

- **Sentinel satellites:** 10 m resolution is too coarse.
- **Google Earth Engine:** 24–48 hour approval and high risk of false
  alarms.
- **Bhuvan high-resolution imagery:** 1 m only for about 200 cities; rural
  areas get 2.5 m.
- **Mapillary / KartaView / OSM:** no coverage of internal village roads.
- **MSRTC bus GPS, PWD pothole portal, HMIS:** no public data.
- **NITI Aspirational Districts dashboard:** doesn't cover Kolhapur or
  Nashik.
- **AI deepfake detection or AI auto-scoring of severity:** unreliable; photos
  are evidence for an officer, not an automatic score.

## Related docs

- **`docs/AwaazIQ_Build_Plan.pdf`**: the team build plan (24 pages): every
  feature, its data, how to build it, and the 4-person work split. Source:
  `docs/AwaazIQ_Build_Plan.html`.

- `STALE_INFRA_DEFICIT_RESEARCH.md`
- `ASSET_LEVEL_PRIORITIZATION_RESEARCH.md`
- `PHOTO_VERIFICATION_RESEARCH.md`
- `PRIORITY_SCORE_ALTERNATIVES_SUMMARY.md`
- `LIVE_GOV_DATA_RESEARCH.md`
- `SILENT_NEED_DETECTOR_RESEARCH.md`
- `BUILD_PROMPT_FOR_AGENT.md`
- `BUILD_PROMPT_VILLAGE_ASSET_VIEW.md`
- `SESSION_LOG_2026-09-12.md`
