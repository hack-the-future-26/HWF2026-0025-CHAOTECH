# AwaazIQ (ComplainBox) — From Citizen Voice to Intelligent Action

AwaazIQ takes rural infrastructure complaints (roads, water, health, schools) in Hindi,
Marathi, English or a mix, by text or voice. It places each report on the map, ties it to a
specific school, hospital, road spot or water point, and ranks what to fix first using a
9-part priority score built mostly from government records. Photos attached to a complaint
are checked for authenticity and analysed by the team's pothole/crack model.

The team build plan is in [`docs/AwaazIQ_Build_Plan.pdf`](docs/AwaazIQ_Build_Plan.pdf).
The sections further down (P1, P2, P3) record the original hackathon build; the sections
directly below describe the project as it stands now.

Legend: ✅ done and verified &nbsp; ⚠️ partial / blocked on something outside this scope &nbsp; ❌ not started

## Current state at a glance (16 September 2026)

| | |
|---|---|
| Database | SQLite at `backend/hackathon.db` (WAL mode), rebuilt from the loaders below |
| `gazetteer` | **1,042** places in Kolhapur and Nashik |
| Named facilities | **10,464** (9,352 UDISE schools + 1,112 health facilities) |
| PMGSY road works | **552** |
| `citizen_request` | **2,031** total (**1,000** flagged `is_synthetic`, the rest real/test intake) |
| ↳ assigned to a cluster | **1,502** |
| `demand_cluster` / `priority_score` | **118** — scores range 18.69 – 56.89 |
| Specific assets / villages ranked | **819** assets across **443** villages |
| Photo checks (Workstream C) | C1–C7 built, including a live vision-LLM building/bridge damage model (C6) — see "Photo checks" below |
| Test coverage | intelligence **291/291** · backend endpoint **66/66** · pipeline **46/46** · photo checks **65/65** |

## How to run

```bash
# 1. Backend API (from backend/) — the frontend expects port 8001
cd backend
python -m uvicorn main:app --host 127.0.0.1 --port 8001

# 2. Frontend (from the repo root), served on localhost so the live camera works
python -m http.server 5500 --bind 127.0.0.1 --directory frontend
```

Then open:

| Page | URL |
|---|---|
| Officials dashboard (map, village and asset panels) | http://127.0.0.1:5500/index.html |
| Citizen complaint form (live camera photos) | http://127.0.0.1:5500/report.html |
| Photo Lab (test any photo against every photo check) | http://127.0.0.1:5500/photo-lab.html |
| API docs | http://127.0.0.1:8001/docs |

The camera needs a secure context: `localhost` works; from a phone, serve over https.
A different API address can be passed as `?api=http://host:port`.

**Pothole model.** Photo checks need `models/pothole_best.pt` (150 MB, git-ignored because it
is over GitHub's 100 MB limit). Get the file from the team and place it there, or set
`AWAAZIQ_POTHOLE_MODEL`. Without it every other check still runs and the model check says
"not available".

**Building/bridge damage model (C6).** This one real check is prototype-stage only: it calls
a vision-LLM through a local OmniRoute gateway (`AWAAZIQ_VLM_DAMAGE_MODEL=1` by default,
model set by `AWAAZIQ_VLM_MODEL`) rather than a dedicated hosted model/API key, since that's
what was available to demo with. Swap the gateway call and model out for a real provider key
before any actual deployment. Set `AWAAZIQ_VLM_DAMAGE_MODEL=0` to disable it — every other
check still runs, and the grading falls back to a lower-confidence proxy signal.

Python packages beyond `backend/requirements.txt` and `pipeline/requirements.txt`:
`ultralytics`, `torch`, `imagehash`, `scipy`, `pillow`, `numpy` (Workstream C photo checks),
`py7zr` (LGD hierarchy loader).

Data-loading scripts (re-run only to rebuild the database). **Run in this order** — this is
the complete list as of 16 September 2026; several were added well after the original
hackathon build and are easy to miss if you're going by memory:

```bash
cd backend
python create_tables.py            # create tables + apply any missing columns
python load_gazetteer.py           # villages from OpenStreetMap
python load_demographic_data.py    # real Census 2011 population
python repair_data.py              # taluka HQs, block assignment, coordinate backfill
python load_lgd_hierarchy.py       # LGD admin hierarchy (needs py7zr)
python load_village_amenities.py   # Census village amenities
python load_bharatnet.py           # BharatNet fibre status
python load_udise_schools.py       # UDISE school register
python load_health_facilities.py   # NIC health facilities
python load_pmgsy_works.py         # PMGSY sanctioned road works
python load_pmgsy_geosadak.py      # PMGSY GeoSadak road line geometry + official names
python load_jjm_water.py           # JJM tap coverage (cached in jjm_cache.json)
python load_jjm_water_testing.py   # real current JJM water-quality testing coverage (needs pycryptodome, the site's own AES-encrypted dropdowns)
python load_jjm_village_schemes.py # real JJM scheme cost/status per village (needs LGD hierarchy loaded first)
python load_udise_school_data.py   # real 2024-25 UDISE+ classroom/teacher/grant condition (needs the UDISE register loaded first)
python load_mgnrega_expenditure.py # real MGNREGA panchayat expenditure
python load_priasoft_receipt_expenditure.py  # real village panchayat receipts/expenditure (15th Finance Commission)
python load_cwc_river_levels.py    # live CWC river-level readings
python load_sachet_alerts.py       # live SACHET disaster alerts
python load_nwdp_groundwater.py    # real groundwater telemetry
python load_gpdp_district_summary.py  # real district-level GPDP investment totals
python load_flood_inundation.py    # real NDEM 2013/2021 satellite flood-inundation extents
python seed_synthetic_data.py      # 1,000 sample reports, flagged is_synthetic
python assign_synthetic_assets.py  # attach sample reports to real schools/hospitals

cd ..
python -m intelligence.recompute   # cluster + score everything (~1 min)
```

One confirmed dead end, not run: `load_mosdac_rainfall.py` requires an ISRO MOSDAC account
with human email approval, has no public bulk endpoint, and correctly leaves its table at 0
rows rather than fabricating anything (see `FEATURE_ROADMAP.md` #5).

Tests:

```bash
# from the repo root
python -m intelligence.test_intelligence  # 291 checks (scoring, assets, C2 distinct accounts, real-data evidence)
cd pipeline && python test_pipeline.py    # 46 end-to-end cases

# from backend/
python test_photo_checks.py               # 65 checks for C1–C7, including the vision-LLM damage model
python test_citizen_report_endpoint.py    # 66 checks — writes to hackathon.db, back it up first
python test_assign_synthetic_assets.py    # 4 checks
```

After pulling changes that add columns, run `python create_tables.py` once in `backend/`.

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Health check → `{"status": "ok"}` |
| POST | `/auth/register`, `/auth/login` | Citizen accounts |
| POST | `/citizen-report` | Ingest a report — JSON `{"text": "..."}`, or the multipart intake form with photos (`attachments`, `capture_meta`, `burst_<i>`) |
| GET | `/citizen-report/{id}` | Citizen-side status of one report |
| GET | `/clusters`, `/clusters/{id}` | Category clusters with score breakdown + runner-up |
| GET | `/villages?district=`, `/villages/{gazetteer_id}` | Village priorities and the village panel |
| GET | `/assets/{id}` | Asset panel: 9-part breakdown, evidence, reports and **photo checks** |
| GET | `/gazetteer/...` | Districts, blocks, villages, departments, facilities for the intake form |
| GET | `/map-data` | GeoJSON of clusters or reports |
| POST | `/what-if` | Budget simulation |
| GET | `/investment-alignment` | Demand vs investment |
| POST | `/recompute-scores` | Full clustering and scoring pass |
| POST | `/photo-checks/analyze` | Photo Lab — run every photo check on one photo, store nothing |
| GET | `/photo-checks/model` | Model details and measured accuracy |
| GET | `/photo-checks/report/{id}` | Photo checks for one report (no coordinates) |
| GET | `/report-attachment/{id}`, `/annotated`, `/ela` | The photo, with detected damage boxed, and its error-level image |

## Workstream C — photo checks and fake-complaint defence

Built on branch `abhay-hanchate-c3`. The full list of changes is in
[`changes-abhayhanchate-c3.pdf`](changes-abhayhanchate-c3.pdf).
**No check ever rejects a complaint:** each one raises or lowers the report's confidence
(never below half) and flags doubtful photos for an officer.

| | Check | How | Status |
|---|---|---|---|
| C1 | Camera-only capture, GPS and time at capture | `getUserMedia` + Image Capture API; GPS and time recorded at the shutter press; flag if more than 5 km from the village | ✅ |
| C2 | Count distinct accounts, not wording | `recompute.reporter_key`: one account = one voice; wording only for anonymous reports | ✅ |
| C3 | Duplicate photo | Perceptual hash (`imagehash`) against every stored photo; flag when reused by another account or village | ✅ |
| C4 | Edit detection | Editing software named in the photo's metadata → flag. Error-level analysis shown as an **advisory** heat map (it could not separate edited from genuine photos in tests) | ✅ / ⚠️ advisory |
| C5 | Pothole model as road evidence | Team YOLOv8 model (`crack`, `pothole`); boxes drawn; `evidence["photo_defect"]` on assets | ✅ |
| C6 | Building/bridge damage grade | crack < partial < collapse from the photo and the wording (en/hi/mr/Hinglish); `evidence["emergency_damage"]` for B2 | ✅ (photo signal weak on structures) |
| C7 | Screen replay | Moiré spectrum check + 5-frame burst (identical frames = still image fed to the camera) | ✅ (calibrated on simulated recaptures) |

**Model accuracy** (88 held-out real photos): accuracy 81.8%, precision 74%, recall 86%, F1 0.80,
ROC-AUC 0.92. Misses water-filled and debris-filled potholes; false alarms mostly on dirt roads.
Details, method and limits: [`models/README.md`](models/README.md).

Code: `backend/photo_checks.py` (the checks), `backend/routes_photo_checks.py` (API),
`frontend/js/camera-capture.js`, `frontend/js/photo-checks.js`, `frontend/photo-lab.html`.

---

## P1 — AI/NLP Intake Pipeline

| Step | Plan asks for | Status | Notes |
|---|---|---|---|
| 0. Environment | `faster-whisper`, `sentence-transformers`, `spacy`, `regex`, `geopy`, `fastapi` | ✅ | All installed. `sentence-transformers` is now genuinely used by P3. |
| 1. Language ID | `detect_language()`, tested on 10 sentences | ✅ | `pipeline/lang_id.py`. Returns `hi-Deva` / `mr-Deva` / `hi-Latn` / `mixed` / `en`, plus 8 other Indian scripts flagged `supported: false`. 42 test cases. |
| 2. Speech-to-text | Wire `faster-whisper`, confirm on real clips | ⚠️ **partly** | `pipeline/asr.py` rewritten — constrained language detection, VAD, no repeat loops, domain prompts, optional language override. **Hindi and English transcribe correctly** on TTS clips; **Marathi is poor even when forced** (model limit — see Known limitations). Real human voice still untested. |
| 3. Text normalization | Strip fillers, normalize transliteration | ✅ | `pipeline/normalize.py`. |
| 4. Issue + severity extraction | Keyword dictionaries per category, per language | ✅ | `pipeline/extract.py` — **4 categories** (road, water, health, education), matching the frozen demo scope. |
| 5. Location extraction | Trigger words + window extraction | ✅ | `pipeline/location.py` — scores every trigger window and keeps the one containing an actual place-like token. |
| 6. Geocoding | Fuzzy-match against gazetteer via `rapidfuzz` | ✅ | `pipeline/geocode.py`, against the real 1,042-row gazetteer. |
| 7. Assemble & test | `process_report()`, 20 test cases | ✅ | 46 cases in `pipeline/test_pipeline.py` across Hindi/English/Marathi/code-mixed + 3 unresolvable locations. |
| 8. Hand off to P2 | Importable by P2, passing tests | ✅ | Live-wired into `POST /citizen-report`. |
| 9. Hardening | More test cases, confidence-decay rule | ✅ | Confidence decay in `pipeline/main.py`; all tuning knobs are module-level constants. |

**P1 deliverable**: met, except real recorded voice input.

## P2 — Data & Backend Infrastructure

| Step | Plan asks for | Status | Notes |
|---|---|---|---|
| 0. Environment | Postgres 15 + PostGIS + pgvector | ⚠️ **deliberate deviation** | SQLite instead (PostGIS wasn't installable in reasonable time). Plain lat/lon floats, haversine in Python rather than PostGIS. Consequence: P3 computes distances itself instead of using `ST_DWithin`. |
| 1. Schema | 4 tables | ✅ (+1 extra) | Plus `citizen_request_raw` per Step 5. P3 added `citizen_request.cluster_id` and four columns to `demand_cluster`. |
| 2. Load gazetteer | Village/ward/block names + lat/lon | ✅ | 1,031 places from OpenStreetMap + 11 taluka HQs added by `repair_data.py`. |
| 3. Load demographic + infrastructure | Census + PMGSY | ✅ (population) / ❌ (PMGSY) | 948/1,042 rows carry real Census population. PMGSY skipped per the plan's own risk table. |
| 4. Ingestion API | `POST /citizen-report` | ✅ | `backend/routes_citizen_report.py`. JSON text and multipart audio. |
| 5. PII redaction | Strip phones + name prefixes | ✅ | Runs synchronously before storage. 8/8 checks pass. |
| 6. Read API for P4 | `/clusters`, `/clusters/{id}`, `/map-data` | ✅ (+1) | Plus `/citizen-reports`. |
| 7. What-if endpoint | Stub now, wire to P3 later | ✅ **now live** | Calls P3's `recompute_with_budget`. Returns `"stub": false`. |
| 8. Synthetic seed data | 800–1,500 rows, realistic distribution | ✅ | 1,000 rows, weighted by real Census population. Re-runnable (clears the previous batch). |
| 9. Deploy | Railway/Render | ⚠️ **containerized, not deployed** | `backend/Dockerfile`, `docker-compose.yml` written, **not verified** — Docker isn't installed here. See `backend/DEPLOY.md`. |

**P2 deliverable**: met except deployment.

## P3 — Intelligence Engine

Lives in [`intelligence/`](intelligence/). Every weight and simplification is documented in
[`intelligence/WEIGHTS.md`](intelligence/WEIGHTS.md).

| Step | Plan asks for | Status | Notes |
|---|---|---|---|
| 0. Environment | scikit-learn, sentence-transformers, numpy | ✅ | Sanity check passed: `"सड़क बहुत खराब है"` vs `"the road is very bad"` → cosine **0.971**; vs an unrelated water complaint → **0.105**. |
| 1. Embeddings | `paraphrase-multilingual-MiniLM-L12-v2` | ✅ | `intelligence/embeddings.py`. In-memory, as the plan permits (pgvector is unavailable on SQLite anyway). |
| 2. Duplicate/corroboration gating | Same category + ~2 km | ✅ | `intelligence/clustering.py`. Gate radius **tuned to 8 km** against the real data — see WEIGHTS.md for the measurement table. |
| 3. Clustering | DBSCAN per category, write clusters + `cluster_id` | ✅ | 113 clusters from 722 clustered reports. Noise points are deliberately not made into clusters. |
| 4. Population affected | Sum gazetteer population in a catchment | ✅ | `intelligence/population.py`. Radius varies by category (health 8 km → water 3 km). |
| 5. Gap Score | `w1·demand + w2·log(1+pop) + w3·infra + w4·vuln`, weights in WEIGHTS.md | ✅ | Equal 0.25 weights. `intelligence/scoring.py`. |
| 6. Priority Score | `gap · confidence_gate + equity + strategic + urgency + feasibility − cost` | ✅ | Confidence gate damps, never zeroes. |
| 7. Explainability output | `breakdown` populated with per-term contributions | ✅ | **The nine terms sum exactly to the score** — asserted in tests and verified across all 113 live clusters (0 mismatches). |
| 8. Runner-up / counterfactual | `runner_up_cluster_id` for the top clusters | ✅ | Computed for every cluster, not just the top 10. |
| 9. What-if function | `recompute_with_budget(budget_delta, district)` | ✅ | `intelligence/whatif.py`, wired into `POST /what-if`. |
| 10. Recompute job | A single trigger P2 can call | ✅ | `POST /recompute-scores`, ~18s on 1,000 reports. Idempotent. |

**P3 deliverable**: met.

### Verified: the thesis actually works

The whole point of the system is that a quieter cluster in an underserved area can
outrank a louder one. It does, on live data:

| | Cluster 50 | Cluster 88 |
|---|---|---|
| Category | road | education |
| Block | Chandgad (low-connectivity) | Kagal |
| Citizen reports | **4** | **11** |
| Population affected | 25,626 | 27,168 |
| **Priority score** | **62.05** | **45.37** |

The equity term (+10.0) and the capped demand term are what carry it — visible in the
breakdown, not hidden in a model.

---

## Bugs found and fixed in this pass

Each of these was found by auditing the running system against the database, not by reading code.

1. **Every report lost its coordinates.** `routes_citizen_report.py` built gazetteer rows with
   `"latitude"`/`"longitude"` keys while `geocode.py` read `"lat"`/`"lon"`, so **0 of 1,004 stored
   reports had coordinates** and `/map-data` returned an empty FeatureCollection. The tests missed
   it because `gazetteer_mock.py` used the *correct* keys — the mock did not match production.
   Fixed on both sides to the Interface Contract's `lat`/`lon`; `test_geocode.py` now asserts
   coordinates come through.
2. **`Karvir` was not in the gazetteer** — the village named in both the research report's worked
   example and the build plan's Interface Contract. Overpass returns `place=village|town` nodes,
   which excludes taluka names. `repair_data.py` adds all 27 taluka HQs.
3. **`block` was NULL on all 1,031 gazetteer rows.** Now assigned by nearest taluka HQ within the
   same district — a **geometric approximation, not official boundary data**, labelled as such
   wherever it surfaces.
4. **Location extraction returned the first trigger window, not the best one.**
   `"Hamare gaon mein road bahut kharab hai, Karvir ke paas"` returned `"Hamare road bahut"`.
   Now every trigger window is scored and the one containing an actual place-like token wins.
   Location resolution went from **82.6% → 99%**.
5. **33% of seeded reports had no category.** The seeder generated 6 categories (adding electricity
   and sanitation) while the extractor only ever supported 4 — and the seeder's own docstring
   asserted the pipeline supported all 6. Out-of-scope templates removed; now **100% categorized**.
6. **`extract_issue_category` returned the first category with any keyword hit**, so the answer
   depended on dictionary declaration order. `"sadak ke paas hospital hai lekin doctor nahi, clinic
   bhi band hai"` returned `road` on one incidental mention, beating three health keywords. Now the
   category with the most hits wins, declaration order breaking ties. (An earlier README claimed
   this was already fixed; it was not — the change appears to have been lost in a branch merge.)
7. **The database path was relative to the working directory** (`sqlite:///./hackathon.db`), so
   running anything from the repo root silently created and used a second, empty database. Now
   anchored to `backend/`.
8. **The seeder appended instead of replacing**, stacking a second 1,000 rows on every run. Now
   clears the previous synthetic batch first, like `load_gazetteer.py` does.
9. **Three test files contained zero assertions** (`test_extract`, `test_geocode`, `test_location`)
   — they only printed, so they could never fail, and `test_location`'s own output showed a
   visible failure nobody caught. All three now assert; the count went from 0 → 34 real checks.
10. **Six major Indian languages were reported as English.** `lang_id.py` knew only Devanagari
    and Latin, so every other Indian script fell through to `return "en"` — Tamil, Telugu,
    Bengali, Kannada, Gujarati, Punjabi, Malayalam and Odia were all labelled English, and
    downstream code then ran Hindi/English keyword matching over them and recorded a
    confident-looking result. Now detected correctly and flagged `supported: false`, with a
    0.15 language confidence so the priority engine's confidence gate damps them. There was no
    test file for this module at all; `pipeline/test_lang_id.py` now covers it with 42 checks.
11. **Speech-to-text read Hindi as English and translated it.** `asr.py` passed
    `language=None`, letting Whisper choose from 99 languages — on short noisy audio it drifts
    to English, and once it decides the audio is English it *translates* instead of
    transcribing. It also had no VAD (Whisper invents fluent sentences over silence — "Hello,
    how are you?" is one of its best-known hallucinations), no repeat-loop guard, and no domain
    vocabulary. Now: detection restricted to hi/mr/en via Whisper's per-language probabilities,
    `vad_filter=True`, `condition_on_previous_text=False`, `task="transcribe"` pinned, and a
    per-language `initial_prompt` of civic vocabulary. The dev console gained a language
    selector, because forcing beats detecting on short clips.

## Known limitations — read before demoing

- **The pilot runs on sample complaints.** The 1,000 reports are generated demo data
  (flagged `is_synthetic`); rankings are a demonstration, not findings.
- **Photo checks are signals, not verdicts.** The pothole model catches about 86% of potholes
  on held-out photos, and about 1 in 4 of its alerts is a false alarm. Capture GPS and time come from the
  citizen's browser, so they raise the cost of faking a photo rather than prove it. The model
  has not yet been tried on real phone captures.
- **A report the text pipeline cannot classify becomes a "Water point" asset** in
  `intelligence/recompute.py` (any non-road category falls through to water). Owner:
  Workstream B.

- **Marathi speech-to-text is poor, and neither forcing the language nor a bigger model
  fixes it.** Measured on a TTS clip of *"आमच्या गावात रस्ता खूप खराब आहे"*:

  | Model | Hindi | Marathi | Speed |
  |---|---|---|---|
  | `small` (default) | ✅ exact | ❌ `आम्च्या गावात रस्ता कुब खराभा हे` | ~4s |
  | `medium` | ⚠️ worse (`गाव`, `हैं`) | ❌ `अम्च्या गावात रस्ता खुब खराबा है।` | ~8s, 450s to download |

  **Keep `small`.** `medium` is 2× slower, worse on Hindi, and still wrong on Marathi.
  Marathi needs a model actually trained for it — a fine-tuned IndicWhisper/IndicVoices
  checkpoint, exactly as the research report §13.3 recommends. Vanilla Whisper at any size is
  the wrong tool. (Caveat: one TTS clip per language is an indication, not a benchmark.)
- **Real human voice has still never been tested.** The clips in `pipeline/sample_audio/` are
  Google TTS, not human speech — cleaner than any real microphone, so they do not exercise
  the noise and silence conditions where Whisper hallucinates. Record real clips before
  relying on a live mic.
- **`infra_deficit` is a proxy** derived from reported severity, not PMGSY asset condition.
- **`vulnerability` is a proxy** derived from settlement size, not a published deprivation index.
- **`equity` uses a 10-block hardcoded list**, explicitly sanctioned by the build plan for MVP,
  not derived from real connectivity/literacy data.
- **Blocks are geometric approximations**, not official taluka boundaries.
- **28% of reports are in no cluster.** That is correct behaviour, not a gap: a lone
  uncorroborated report in a sparse area is not yet a demand signal, and forcing it into a
  cluster would claim corroboration that does not exist.
- **Not deployed.** The API runs locally only.

Every one of these is listed with its replacement in
[`intelligence/WEIGHTS.md`](intelligence/WEIGHTS.md#summary-of-every-simplification-in-one-place).

## Interface Contract deviations (read before P4 integrates)

- **`language_detected` values**: the contract shows `"hi-Deva"`-style codes, and the
  implementation returns exactly those — `hi-Deva` / `mr-Deva` / `hi-Latn` / `mixed` / `en`.
  (An earlier README claimed the opposite; it was wrong.)
- **`location_resolved` keys** are `lat`/`lon`, matching the contract.
- **`cluster_id`** is an integer, not the `"CL-042"` string form shown in the contract's example.
  P4 should format it for display.

## What's left that genuinely needs a human

1. **Record real voice clips** and run them through `asr.py` (P1 Step 2).
2. **Deploy to Railway or Render** (P2 Step 9) — needs an account and a `docker build`.
3. **Try the photo checks on real phones**, including a phone photographing a screen, to
   confirm the camera path and the moiré check outside simulation.
4. **Remaining build-plan work** for workstreams A, B and D (see `docs/AwaazIQ_Build_Plan.pdf`).
