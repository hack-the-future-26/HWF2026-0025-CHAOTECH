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
| 3 | **Road names from PMGSY GeoSadak** (Task 5): real road identity for road pins | 📝 Run after #2 finishes, never at the same time | `BUILD_PROMPT_FOR_AGENT.md` Task 5 |
| 4 | **Groundwater evidence (NWDP)** on water clusters | ✅ Done (03c26d9; data checked against the live source) | `BUILD_PROMPT_FOR_AGENT.md` Task 4 |
| 5 | **Rainfall evidence (MOSDAC GSMaP)** | ⛔ Needs an ISRO MOSDAC account (email sign-up, approval by email) | `LIVE_GOV_DATA_RESEARCH.md` §3 |
| 6 | **Fake-complaint defence** (see below) | 📝 Designed; build prompt not written yet | `PHOTO_VERIFICATION_RESEARCH.md` |
| 7 | **Emergency urgency** (owner's definition, see below) | 📝 Needs one decision | this file, below |
| 8 | **Stale-record discount (trust-blend)**: trust an old government record less when a burst of recent reports contradicts it | 📝 Unblocked (half-life = 10 years decided) | `STALE_INFRA_DEFICIT_RESEARCH.md` §2 |
| 9 | **Fresher or live government data** | 📝 UDISE+, CWC and SACHET confirmed scrapable; JJM water quality likely; eMARG blocked by CAPTCHA | `LIVE_GOV_DATA_RESEARCH.md` §4A |
| 10 | **Satellite confirmation (Planet Labs)**, disasters only | ⛔ Needs the Planet Education & Research application (university email) | `STALE_INFRA_DEFICIT_RESEARCH.md` §2A |
| 11 | **Silent Need Detector** (equity term): flag villages that likely need help but under-report, instead of the current flat BharatNet-only proxy | 📝 Designed; not in Workstream B yet, no build prompt written | `SILENT_NEED_DETECTOR_RESEARCH.md` |

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
| Water | JJM scheme records (ejalshakti) | 🟡 Not verified | Scheme cost, spending and status per village, if reachable with the existing JJM loader's technique |
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
