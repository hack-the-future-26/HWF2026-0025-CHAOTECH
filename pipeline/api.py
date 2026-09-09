"""
api.py — FastAPI service wrapper for the P1 pipeline.

P2 can either:
  (a) import process_report directly:  from pipeline import process_report
  (b) call this service over HTTP:     POST /process-report

Run standalone:
    uvicorn pipeline.api:app --reload --port 8001
"""

from __future__ import annotations

import base64
import tempfile
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .main           import process_report
from .gazetteer_mock import MOCK_GAZETTEER

app = FastAPI(
    title="P1 — AI/NLP Intake Pipeline",
    description=(
        "Turns raw voice audio or text complaints into structured records "
        "(StructuredRecord) matching the Interface Contract from Section 2."
    ),
    version="0.1.0",
)


# ── Request / response models ─────────────────────────────────────────────────

class ReportRequest(BaseModel):
    text:          str | None  = None
    audio_base64:  str | None  = None
    duplicate_count: int       = 0
    is_synthetic:  bool        = False
    # P2 may pass real gazetteer rows; fallback to mock if absent
    gazetteer_rows: list[dict] | None = None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "pipeline-p1"}


@app.post("/process-report")
def handle_report(req: ReportRequest) -> dict:
    """
    Accept a citizen complaint (text or base64-encoded audio) and return
    a fully structured StructuredRecord.
    """
    if not req.text and not req.audio_base64:
        raise HTTPException(
            status_code=422,
            detail="Provide either 'text' or 'audio_base64'.",
        )

    gazetteer = req.gazetteer_rows if req.gazetteer_rows is not None else MOCK_GAZETTEER

    input_data: dict = {
        "duplicate_count": req.duplicate_count,
        "is_synthetic":    req.is_synthetic,
    }

    if req.text:
        input_data["text"] = req.text

    elif req.audio_base64:
        # Decode base64 audio to a temp file and pass the path
        try:
            audio_bytes = base64.b64decode(req.audio_base64)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 audio data.")

        suffix = ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            input_data["audio_path"] = tmp_path
            record = process_report(input_data, gazetteer)
        finally:
            os.unlink(tmp_path)

        return record

    return process_report(input_data, gazetteer)
