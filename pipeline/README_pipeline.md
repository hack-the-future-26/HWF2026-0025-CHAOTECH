# pipeline/

AI/NLP Intake Pipeline — **P1** of the 48-hour sprint.

Turns raw voice audio or text complaints (Hindi · English · Marathi) into a
structured `StructuredRecord` matching the Interface Contract defined in
Section 2 of the build plan.

---

## Files

| File | Purpose |
|------|---------|
| `main.py` | `process_report()` — the single function P2 imports |
| `lang_id.py` | Language detection (hi-Deva / mr-Deva / en / hi-Latn / mixed) |
| `asr.py` | Speech-to-text via `faster-whisper` |
| `normalize.py` | Filler-word removal, transliteration normalisation |
| `extract.py` | Rule-based issue-category + severity extraction |
| `location.py` | Location-phrase extraction (trigger-word + proper-noun heuristics) |
| `geocode.py` | Fuzzy geocoding against the gazetteer |
| `gazetteer_mock.py` | 30-entry mock gazetteer (Kolhapur + Pune) for standalone testing |
| `api.py` | FastAPI service wrapper (optional — P2 can import directly) |
| `test_pipeline.py` | 46-case test suite |

---

## Quick Start

### 1. Install dependencies
```bash
cd pipeline/
pip install -r requirements.txt
```

> **First run**: `faster-whisper` downloads the `small` model (~460 MB) on first call to `transcribe()`. Text-only tests skip this.

### 2. Run tests
```bash
# From repo root
python -m pytest pipeline/test_pipeline.py -v

# Or directly
python -m pipeline.test_pipeline
```

### 3. Start the API server (optional)
```bash
uvicorn pipeline.api:app --reload --port 8001
```

Then POST to `http://localhost:8001/process-report`:
```json
{ "text": "बारिश में सड़क बंद हो जाती है" }
```

---

## P2 Integration

P2 imports the pipeline directly — no HTTP needed:

```python
from pipeline import process_report

# With real gazetteer rows from the database
record = process_report(
    {"text": "Hamare gaon mein road bahut kharab hai"},
    gazetteer_rows=db_rows,   # list of dicts: name, district, block, lat, lon
)
```

`process_report` also accepts audio:
```python
record = process_report(
    {"audio_path": "/tmp/clip.wav"},
    gazetteer_rows=db_rows,
)
```

### StructuredRecord shape returned

```json
{
  "raw_text": "बारिश में सड़क बंद हो जाती है",
  "language_detected": "hi-Deva",
  "issue_category": "road",
  "severity": "medium",
  "location_raw": null,
  "location_resolved": null,
  "is_synthetic": false,
  "confidence_overall": 0.61
}
```

Fields match **Section 2 Interface Contract** exactly.

---

## Design Decisions

- **Rule-based extraction** (not ML) — deliberately chosen per the spec for auditability.
- **Three-tier severity** — `high` / `medium` / `low` based on keyword presence.
- **Confidence decay** — if >50 % of field scores are below 0.6, overall confidence is penalised by 10 %.
- **Marathi = third language** — matches Kolhapur/Pune demo districts.
- **Mock gazetteer** — 30 real village names; replaced by P2's database at integration.

---

## Gazetteer contract (for P2)

P2's gazetteer rows must have these keys (matching P2's schema):

```
name       TEXT   — village / ward / block name
district   TEXT
block      TEXT
lat        FLOAT
lon        FLOAT
```
