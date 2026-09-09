# ComplainBox — P1 + P2 + P3

Status against `Build-Plan-4-Person-Team.pdf`, covering **P1 (AI/NLP Intake Pipeline)**,
**P2 (Data & Backend Infrastructure)** and **P3 (Intelligence Engine)**.
P4 (Frontend/Demo) has not been started — `frontend/` does not exist yet.

Legend: ✅ done and verified &nbsp; ⚠️ partial / blocked on something outside this scope &nbsp; ❌ not started

## Current state at a glance

| | |
|---|---|
| Database | SQLite at `backend/hackathon.db` (WAL mode) |
| `gazetteer` | **1,042** places — 747 Kolhapur + 284 Nashik villages/towns (OpenStreetMap) + 11 taluka HQs |
| ↳ with real Census population | **948** (91%) |
| ↳ with a block assigned | **1,042** (100%, nearest-HQ approximation — see below) |
| `citizen_request` | **1,001** — 1,000 synthetic + 1 real test submission |
| ↳ with coordinates | **990** (98%) |
| ↳ with an issue category | **1,001** (100%) |
| ↳ assigned to a cluster | **722** (72%) |
| `demand_cluster` / `priority_score` | **113 / 113** — scores range 35.18 – 86.13 |
| Test coverage | **162 assertions across 7 suites, all passing** |

## How to run

```bash
# 1. Backend API (from backend/)
cd backend
uvicorn main:app --reload --port 8000

# 2. Internal dev console — open in a browser
frontend-test/index.html
```

Data-loading scripts (already run; re-run only to rebuild the database). **Run in this order:**

```bash
cd backend
python create_tables.py           # create tables + apply any missing columns
python load_gazetteer.py          # 1,031 villages from OpenStreetMap
python load_demographic_data.py   # real Census 2011 population
python repair_data.py             # taluka HQs, block assignment, coordinate backfill
python seed_synthetic_data.py     # 1,000 synthetic reports (clears the previous batch first)

cd ..
python -m intelligence.recompute  # cluster + score everything (~20s)
```

Tests:

```bash
# from the repo root
python -m pipeline.test_pipeline          # 46 end-to-end cases against the real gazetteer
python -m intelligence.test_intelligence  # 32 P3 assertions

# from pipeline/
cd pipeline
python test_extract.py     # 14 cases
python test_geocode.py     # 10 cases
python test_location.py    # 10 cases
python test_lang_id.py     # 42 cases

# from backend/ — needs the server running on :8000
cd ../backend
python test_citizen_report_endpoint.py   # 8 checks
```

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Health check → `{"status": "ok"}` |
| POST | `/citizen-report` | Ingest a report — JSON `{"text": "..."}` **or** multipart audio upload |
| GET | `/clusters` | All clusters, sorted by priority score desc, with breakdown + runner-up |
| GET | `/clusters/{id}` | One cluster + parsed score breakdown (404 if missing) |
| GET | `/map-data` | GeoJSON of **clusters** (default) or `?layer=reports` for individual reports |
| GET | `/citizen-reports` | Paginated report list (`?limit=`, `?offset=`) |
| POST | `/what-if` | **Live** — `{budget_delta, district}` → re-ranked clusters with per-cluster deltas |
| POST | `/recompute-scores` | Triggers the full P3 pass (~18s on 1,000 reports) |
| POST | `/test/detect-language` | Dev console — language detection |
| POST | `/test/transcribe` | Dev console — audio upload → transcription |

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
3. **P4 — the entire frontend**: map, dashboard, score-breakdown chart, Cluster A vs B view,
   what-if slider, citizen feedback screen. Every API it needs is live and returning real data.
