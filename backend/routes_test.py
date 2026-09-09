"""
Dev-only endpoints backing frontend-test/index.html, so pipeline functions
can be exercised from a browser instead of the terminal.
"""

import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.asr import transcribe_detailed  # noqa: E402
from pipeline.lang_id import detect_language, is_supported  # noqa: E402

router = APIRouter(prefix="/test")


class DetectLanguageIn(BaseModel):
    text: str


@router.post("/detect-language")
def test_detect_language(payload: DetectLanguageIn):
    """
    Returns the detected language plus whether the pipeline can actually
    process it. Tamil/Telugu/Bengali and friends now come back correctly
    labelled with supported=false, instead of being reported as English.
    """
    language = detect_language(payload.text)
    return {
        "language": language,
        "supported": is_supported(language),
    }


@router.post("/transcribe")
async def test_transcribe(
    file: UploadFile = File(...),
    language: str | None = Form(None),
):
    """
    Transcribe an uploaded clip.

    `language` ("hi"/"mr"/"en") forces the language instead of detecting it.
    Forcing is always more reliable than detection on short clips, which is
    why the dev console offers it -- unconstrained detection was reading
    Hindi speech as English and translating it.
    """
    suffix = Path(file.filename).suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        result = transcribe_detailed(tmp_path, language_hint=language or None)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return {
        "transcription": result["text"],
        "language": result["language"],
        "language_probability": result["language_probability"],
        "language_was_detected": result["language_was_detected"],
    }
