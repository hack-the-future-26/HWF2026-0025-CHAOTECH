# AwaazIQ — what's actually left to build (2026-09-16)

`FEATURE_ROADMAP.md` is the detailed historical record but was last updated
2026-09-14 and is stale in places — several items it lists as "not built"
were actually completed in the sessions since (Emergency Urgency, the
Stale-Record trust-blend, the whole Workstream C photo-verification system,
and JJM scheme money, among others). Every line below was checked against
the live code/DB today, not copied from that doc. Where the two disagree,
this file is the corrected one.

## Wired and real, but sitting unused — cheapest wins first

- **`school_condition_evidence()`** (`intelligence/realdata.py`): builds a
  human-readable sentence from real UDISE+ teacher/classroom/grant data,
  never called from `recompute.py`. Only `school_condition_deficit()` (the
  0-1 score) is used. The narrative sentence version is dead code.

## Designed, not coded

- **#11 Silent Need Detector** (equity term): still the flat
  BharatNet-fibre-only proxy (`real_reporting_capacity_deficit` in
  `realdata.py`, unchanged). Researched properly 2026-09-16, owner decided
  to leave it as-is. See the dedicated section below for what was actually
  checked before that decision.
- **B3 — corroboration softening for 1-2 reporter assets**: zero code
  (confirmed by grep, no matches anywhere for the concept). Discussed
  earlier this session, never designed in detail, not built.
- **🔴 Duplicate/double-funding detector** (comparing nearby *different*
  sanctioned projects against each other, not the same-project
  already-funded check that already exists): explicitly scoped out as its
  own future feature when the 🟢/🟠 funding-mismatch banners were built
  this session (2026-09-16). No code.
- **D4 — demo script**: not built (confirmed, no file exists).
- **Feasibility Phase 2 — OSM nearest-town distance + terrain ruggedness**:
  explicitly deferred 2026-09-15 (`FEATURE_ROADMAP.md` #10). Terrain
  (Open-Elevation) is confirmed live and free but needs ~4,000 batched
  calls and caching that doesn't exist; OSM needs a PBF pipeline this repo
  has no infrastructure for, and the free Overpass API timed out twice in
  testing.
- **Village aggregation redesign**: `VillagePriority.priority_score` is
  still `max(assets)` — a village's number is always just its single
  highest-scoring asset. An independent `has_urgent_asset` flag was added
  2026-09-16 so a real emergency at a non-top asset is at least visible,
  but the underlying aggregation formula itself (per-category rollup,
  IMD-style anti-cancellation blend) is a real, researched, still-open
  design decision — see the OECD/Alkire-Foster/NITI-Aayog/IMD/FEMA
  research from that session for the actual precedent.

## #11 Silent Need Detector — researched properly 2026-09-16, left as-is

`SILENT_NEED_DETECTOR_RESEARCH.md`'s design has two parts, checked
separately rather than accepted at face value:

**Part 1 — load real literacy data.** The doc's Step 1 claims literacy
joins to villages "by the same join key (village census code), same
pattern as `load_demographic_data.py`." That's wrong — checked
`load_village_amenities.py` (the actual precedent) and it joins by
**fuzzy name matching** (`rapidfuzz`), because `Gazetteer` has no census
code column at all. Still real and buildable: found and verified a live,
no-login source (`censusindia.gov.in`'s per-district Primary Census
Abstract files — downloaded Nashik's, a real 1,984-row file with
`Literates`/`Illiterate Persons` columns by gender), it would just need
the same fuzzy-match pattern, not a clean join.

**Part 2 — the expected-vs-actual reports ratio (the actual point of the
feature).** This needs a real baseline of genuine citizen reporting
behavior to compare against. Checked the real data: 1,000 of 2,014
reports are explicitly synthetic; of the other 1,014, 947 have no GPS pin
and no citizen account — almost all bulk-loaded test data, not field
reports. Only 67 reports in the whole database carry a real citizen GPS
pin. A baseline built from this would be calibrated on where test
scripts happened to drop fake reports, not real digital-divide behavior —
confident-looking, but not actually true.

**Also checked, at the owner's request: is BharatNet + literacy even
enough to prove a village can't report, if the data problem were
solved?** No — both are real but partial proxies. BharatNet measures
fibre to the panchayat *office*, not villagers' phones (already stated in
the existing code's own docstring). Literacy measures general
reading/writing, not digital literacy specifically. Neither captures
mobile network coverage, smartphone ownership, awareness that AwaazIQ
exists, or whether people already complain through a different channel
entirely. Checked whether a better connectivity signal exists instead:

- TRAI's public data is telecom-circle level (state-wide), nowhere near
  village granularity.
- TRAI + the Department of Posts just launched an actual village-level
  telecom survey (5.68 lakh villages, the right granularity) — but it's a
  brand-new MoU with data collection just starting; no public dataset
  exists yet.
- Tarang Sanchar (DoT's tower locator): real and live, but a
  one-address-at-a-time citizen tool, no bulk API.
- OpenCelliD (crowdsourced global tower database): real data exists but
  requires registration to pull, and being crowdsourced from ordinary
  phones, it's structurally thinnest exactly in the poor, remote villages
  this feature is trying to find — the same rural-crowdsourcing bias this
  project already rejected OSM over.

**Decision**: leave `real_reporting_capacity_deficit` as the flat
BharatNet proxy. Not because the idea is bad — the core methodology (an
independent deficit prediction compared against actual reports) is real,
published research (Kontokosta et al., NYU, on 20M+ NYC 311 requests) —
but because neither the calibration data nor a meaningfully better input
signal exists yet. Revisit if/when either changes: real citizen report
volume grows, or the new TRAI village survey publishes real data.

## Blocked on something outside the code (not a coding task)

- **eGramSwaraj GPDP** (whole-panchayat budget plan, all sectors): CAPTCHA-
  protected, unchanged. `backend/capture_gpdp.py` exists (human-assisted
  capture tool) but was never actually run — `village_budget_plan` has 0
  rows — and it also skipped its own required Step 0 (test whether one
  CAPTCHA solve covers many villages before assuming the ~441-solve worst
  case).
- **eMARG** (health facility data): CAPTCHA-blocked, needs a formal data
  request to NRIDA.
- **NASA GPM IMERG rainfall**: needs a free NASA Earthdata Login
  (self-service, but still an account nobody has created yet). Verified
  live 2026-09-16 — the AWS mirror refuses anonymous access.
- **Satellite confirmation (Planet Labs)**: needs the Planet Education &
  Research application (university email).
- **Real per-facility health budget data**: doesn't exist publicly beyond
  NHM's fixed entitlement amounts (not actual spending).
- **JJM water contamination readings** (vs. today's testing-coverage-only
  data): the `Contaminantwise` report on the same JJM WQMIS site was never
  actually tried.

## Real but zero research done yet

- **`cost_penalty` scoring term**: still purely population-scaled
  (`cost_penalty_points()` in `intelligence/scoring.py`), no real cost
  data behind it at all. The team's own build plan calls this the
  weakest-verified category — genuinely untouched.

## Fake-complaint defence (#6) — checked item by item against the real code

Everything in this list is now built (Workstream C, merged and verified
this session) except:

- **Screen-replay check "needs a model to be found"** (roadmap's own
  wording) — turned out not to need one: built with moiré-pattern +
  multi-frame sensor-noise analysis (C7), no ML model, live and tested.
- Everything else in the original #6 table (sign-in, distinct-account
  corroboration, camera-only capture, duplicate-photo hash, edit
  detection, pothole/crack model, building/bridge damage model) is done.

## Explicitly ruled out (listed for completeness, not "remaining")

Sentinel satellites (too coarse), Google Earth Engine (approval delay +
false-alarm risk), Bhuvan high-res imagery (rural-area resolution too
low for building/road detail), Mapillary/KartaView/OSM street imagery (no
internal village road coverage), MSRTC bus GPS / PWD pothole portal / HMIS
(no public data), NITI Aspirational Districts dashboard (doesn't cover
Kolhapur or Nashik), AI auto-scoring of severity or deepfake detection
(unreliable for a real complaint system — photos stay evidence for an
officer, never an automatic verdict).

## Corrections this file makes to `FEATURE_ROADMAP.md`

- **JJM scheme money (roadmap's Task 4)**: roadmap says "never attempted,
  no table". Actually real and loaded: `JjmVillageScheme`, 2,183 rows
  across 643 villages, wired into `jjm_scheme_catchment_evidence()` and
  the funding-mismatch banners built 2026-09-16.
- **MGNREGA**: roadmap says "found, currently inaccessible" (503 errors).
  Actually loaded since (119 real rows in `village_mgnrega_expenditure`)
  and wired into road/water evidence 2026-09-16
  (`mgnrega_catchment_evidence()`) — 75 real assets now carry it.
- **Investment alignment engine's road-only counting bug** (roadmap's
  white-space table, #2): fixed 2026-09-15, before the roadmap's own
  "Updated" date caught up — `intelligence/investment.py` now filters
  `issue_category == "road"` before counting demand against PMGSY works.
- **Emergency Urgency (#7), Stale-Record trust-blend (#8), and the entire
  Fake-Complaint Defence system (#6)**: roadmap lists these as "needs a
  decision" / "designed, not started". All three are fully built, tested,
  and verified against real data as of this session.
