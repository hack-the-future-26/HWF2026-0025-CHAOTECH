"""
main.py — process_report()

The single public function P2 imports.  Orchestrates Steps 1-6 from the
P1 spec and returns a StructuredRecord matching the Interface Contract
(Section 2 of the build plan).
"""

from __future__ import annotations

from .lang_id  import detect_language, is_supported
from .asr      import transcribe
from .normalize import normalize_text
from .extract  import extract_issue_category, extract_severity
from .location import extract_location_phrase
from .geocode  import resolve_location


# ---------------------------------------------------------------------------
# Confidence helpers
# ---------------------------------------------------------------------------

def _language_confidence(language: str) -> float:
    """
    Rough confidence for the language detection step.
    'mixed' and 'hi-Latn' have slightly lower certainty than a clear script.

    A language the pipeline has no keyword dictionaries for (Tamil, Telugu,
    Bengali, ...) scores very low on purpose: the script was identified
    correctly, but nothing downstream can actually read it, so the record
    must carry that doubt rather than look as trustworthy as a Hindi one.
    The confidence gate in the priority engine then damps it, and the
    low-confidence value is the signal to route it to human review.
    """
    if not is_supported(language):
        return 0.15

    return {
        "hi-Deva": 0.95,
        "mr-Deva": 0.95,
        "en":      0.90,
        "hi-Latn": 0.80,
        "mixed":   0.70,
    }.get(language, 0.70)


def _severity_confidence(text: str, severity: str) -> float:
    """
    High severity from explicit keywords is more confident than a default.
    """
    if severity == "high":
        return 0.90
    if severity == "low":
        return 0.85
    return 0.70   # medium is the default fallback


def _apply_confidence_decay(scores: list[float]) -> float:
    """
    Confidence-decay rule (Step 9):
    If more than half the field scores are below 0.6, apply an additional
    10 % penalty to the overall confidence to signal that extraction quality
    is low across multiple steps.
    """
    avg = sum(scores) / len(scores)
    low_count = sum(1 for s in scores if s < 0.60)
    if low_count > len(scores) / 2:
        avg = avg * 0.90
    return round(avg, 2)


# ---------------------------------------------------------------------------
# Main pipeline function
# ---------------------------------------------------------------------------

def process_report(
    input_data: dict,
    gazetteer_rows: list[dict] | None = None,
) -> dict:
    """
    Turn raw voice audio or plain text into a StructuredRecord.

    Args:
        input_data:     dict with either:
                          {"text": "<complaint string>"}
                        or
                          {"audio_path": "<path to .wav/.mp3>"}
                        Optional extra fields:
                          "duplicate_count": int   (default 0)
                          "is_synthetic":    bool  (default False)

        gazetteer_rows: list of dicts from P2's gazetteer table.
                        Each row must have: name, district, block, lat, lon.
                        Pass [] or None for standalone / test usage — geocoding
                        will return None and confidence will be reduced.

    Returns:
        StructuredRecord dict matching the Interface Contract (Section 2).
    """

    gazetteer_rows = gazetteer_rows or []
    duplicate_count: int  = input_data.get("duplicate_count", 0)
    is_synthetic:    bool = input_data.get("is_synthetic", False)

    # ── 1. Input ─────────────────────────────────────────────────────────────
    if "audio_path" in input_data:
        raw_text = transcribe(input_data["audio_path"])
    else:
        raw_text = input_data.get("text", "").strip()

    if not raw_text:
        # Return a minimal low-confidence record rather than raising
        return {
            "raw_text":           "",
            "language_detected":  "en",
            "issue_category":     None,
            "severity":           "medium",
            "location_raw":       None,
            "location_resolved":  None,
            "is_synthetic":       is_synthetic,
            "confidence_overall": 0.10,
        }

    # ── 2. Language ──────────────────────────────────────────────────────────
    language      = detect_language(raw_text)
    lang_conf     = _language_confidence(language)

    # ── 3. Normalisation ─────────────────────────────────────────────────────
    normalized    = normalize_text(raw_text)

    # ── 4. Issue extraction ──────────────────────────────────────────────────
    issue_category = extract_issue_category(normalized)
    issue_conf     = 0.90 if issue_category else 0.30

    # ── 5. Severity ──────────────────────────────────────────────────────────
    severity      = extract_severity(normalized, duplicate_count)
    sev_conf      = _severity_confidence(normalized, severity)

    # ── 6. Location extraction ───────────────────────────────────────────────
    location_phrase = extract_location_phrase(normalized)

    # ── 7. Geocoding ─────────────────────────────────────────────────────────
    location_resolved = resolve_location(location_phrase, gazetteer_rows)
    geo_conf = (
        location_resolved["confidence"]
        if location_resolved
        else 0.20
    )

    # ── 8. Confidence (with decay) ───────────────────────────────────────────
    field_scores = [lang_conf, issue_conf, sev_conf, geo_conf]
    confidence_overall = _apply_confidence_decay(field_scores)

    # ── 9. Assemble StructuredRecord ─────────────────────────────────────────
    return {
        "raw_text":           raw_text,
        "language_detected":  language,
        "issue_category":     issue_category,
        "severity":           severity,
        "location_raw":       location_phrase,
        "location_resolved":  location_resolved,
        "is_synthetic":       is_synthetic,
        "confidence_overall": confidence_overall,
    }