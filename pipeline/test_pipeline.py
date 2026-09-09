"""
test_pipeline.py

Full test suite for the P1 pipeline (Steps 7 & 9 of the P1 spec).

Run from the repo root:
    python -m pytest pipeline/test_pipeline.py -v

Or directly:
    python -m pipeline.test_pipeline

Coverage: 45 cases spanning
  - Pure Hindi / Devanagari (10)
  - Pure English (10)
  - Marathi (8)
  - Code-mixed / Hinglish (7)
  - Ambiguous / missing location (5)
  - Edge cases: empty input, severity tiers, is_synthetic flag (5+)
"""

from __future__ import annotations

import sys
import os
import io

# Reconfigure stdout to UTF-8 so Devanagari text prints on Windows cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Allow running as `python -m pipeline.test_pipeline` from repo root
# AND as `python -m pytest pipeline/test_pipeline.py`
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipeline.main           import process_report
from pipeline.gazetteer_mock import MOCK_GAZETTEER

REQUIRED_KEYS = {
    "raw_text", "language_detected", "issue_category",
    "severity", "location_raw", "location_resolved",
    "is_synthetic", "confidence_overall",
}


# ── Test-case definitions ─────────────────────────────────────────────────────
# Each dict: text, expect_category, expect_severity (optional), expect_lang (optional)

TEST_CASES: list[dict] = [

    # ── Hindi / Devanagari (10) ──────────────────────────────────────────────
    {
        "id": "HI-01",
        "text": "बारिश में सड़क बंद हो जाती है",
        "expect_category": "road",
        "expect_lang_prefix": "hi",
    },
    {
        "id": "HI-02",
        "text": "हमारे गांव में पानी की बड़ी समस्या है",
        "expect_category": "water",
    },
    {
        "id": "HI-03",
        "text": "अस्पताल बहुत दूर है, इलाज नहीं मिलता",
        "expect_category": "health",
        "expect_severity": "high",  # बहुत → high
    },
    {
        "id": "HI-04",
        "text": "स्कूल में शिक्षक नहीं हैं",
        "expect_category": "education",
    },
    {
        "id": "HI-05",
        "text": "गड्ढे की वजह से दुर्घटना हो सकती है, यह खतरनाक है",
        "expect_category": "road",
        "expect_severity": "high",
    },
    {
        "id": "HI-06",
        "text": "नल में पानी नहीं आता, बोरवेल भी खराब है",
        "expect_category": "water",
    },
    {
        "id": "HI-07",
        "text": "बार-बार बिजली जाती है और सड़क टूटी हुई है",
        "expect_category": "road",
        "expect_severity": "high",
    },
    {
        "id": "HI-08",
        "text": "दवाखाना बंद है, दवा नहीं मिलती",
        "expect_category": "health",
    },
    {
        "id": "HI-09",
        "text": "कच्ची सड़क से बच्चे स्कूल नहीं जा पाते",
        "expect_category": "road",
    },
    {
        "id": "HI-10",
        "text": "थोड़ा पानी आता है लेकिन नल कभी-कभी बंद रहता है",
        "expect_category": "water",
        "expect_severity": "low",
    },

    # ── English (10) ─────────────────────────────────────────────────────────
    {
        "id": "EN-01",
        "text": "The road connecting our village to the highway is unusable",
        "expect_category": "road",
        "expect_lang_prefix": "en",
    },
    {
        "id": "EN-02",
        "text": "There is no drinking water supply in Karvir village for the last 3 days",
        "expect_category": "water",
    },
    {
        "id": "EN-03",
        "text": "The primary health centre near Baramati has no doctor posted",
        "expect_category": "health",
    },
    {
        "id": "EN-04",
        "text": "School classroom roof has collapsed and children sit outside",
        "expect_category": "education",
    },
    {
        "id": "EN-05",
        "text": "Severe pothole on the main road caused an accident yesterday",
        "expect_category": "road",
        "expect_severity": "high",
    },
    {
        "id": "EN-06",
        "text": "Water pipeline is leaking and contaminating the drinking source",
        "expect_category": "water",
    },
    {
        "id": "EN-07",
        "text": "There is a slight issue with the drainage near Shirur block",
        "expect_category": "water",
        "expect_severity": "low",
    },
    {
        "id": "EN-08",
        "text": "Medical supplies have not reached the dispensary in Haveli for weeks",
        "expect_category": "health",
    },
    {
        "id": "EN-09",
        "text": "Bridge over the river near Bhor is damaged and dangerous",
        "expect_category": "road",
        "expect_severity": "high",
    },
    {
        "id": "EN-10",
        "text": "Midday meal is not being served in the government school at Maval",
        "expect_category": "education",
    },

    # ── Marathi / mr-Deva (8) ────────────────────────────────────────────────
    {
        "id": "MR-01",
        "text": "आमच्या गावात पाणी नाही",
        "expect_category": "water",
        "expect_lang_prefix": "mr",
    },
    {
        "id": "MR-02",
        "text": "रस्त्यात खूप खड्डे आहेत, गाड्या बंद पडतात",
        "expect_category": "road",
        "expect_severity": "high",
    },
    {
        "id": "MR-03",
        "text": "शाळेत शिक्षक नाहीत, मुले घरी बसतात",
        "expect_category": "education",
    },
    {
        "id": "MR-04",
        "text": "दवाखाना उघडत नाही, डॉक्टर येत नाहीत",
        "expect_category": "health",
    },
    {
        "id": "MR-05",
        "text": "नळाला पाणी येत नाही, बोअरवेल बंद आहे",
        "expect_category": "water",
    },
    {
        "id": "MR-06",
        "text": "रुग्णालयात औषध नाही, रुग्णांना खूप त्रास होतो",
        "expect_category": "health",
        "expect_severity": "high",
    },
    {
        "id": "MR-07",
        "text": "रस्त्याची खूप वाईट अवस्था आहे, वारंवार अपघात होतात",
        "expect_category": "road",
        "expect_severity": "high",
    },
    {
        "id": "MR-08",
        "text": "पाण्याची थोडी समस्या आहे, कधी कधी नळ बंद असतो",
        "expect_category": "water",
        "expect_severity": "low",
    },

    # ── Code-mixed / Hinglish (7) ────────────────────────────────────────────
    {
        "id": "CM-01",
        "text": "Hamare gaon mein road bahut kharab hai",
        "expect_category": "road",
        "expect_severity": "high",
        "expect_lang_prefix": "hi",
    },
    {
        "id": "CM-02",
        "text": "paani nahi hai, borewell bhi kharab",
        "expect_category": "water",
    },
    {
        "id": "CM-03",
        "text": "hospital bahut door hai, doctor nahi milta",
        "expect_category": "health",
        "expect_severity": "high",
    },
    {
        "id": "CM-04",
        "text": "sadak pe bahut gaddha hai near Karvir village",
        "expect_category": "road",
        "expect_severity": "high",
    },
    {
        "id": "CM-05",
        "text": "हमारे school mein teacher नहीं है",
        "expect_category": "education",
    },
    {
        "id": "CM-06",
        "text": "पानी की problem है, nala overflow ho raha hai",
        "expect_category": "water",
    },
    {
        "id": "CM-07",
        "text": "rasta bahut bura hai, bar bar same issue repeat hota hai",
        "expect_category": "road",
        "expect_severity": "high",
    },

    # ── Ambiguous / missing location (5) ─────────────────────────────────────
    {
        "id": "AMB-01",
        "text": "The road is bad",   # no location
        "expect_category": "road",
        "expect_no_location_resolved": True,
    },
    {
        "id": "AMB-02",
        "text": "पानी नहीं",         # extremely short, no location
        "expect_category": "water",
        "expect_no_location_resolved": True,
    },
    {
        "id": "AMB-03",
        "text": "Road issue near XYZ nonexistent place ABC",  # fake place
        "expect_category": "road",
        "expect_no_location_resolved": True,   # gazetteer won't match
    },
    {
        "id": "AMB-04",
        "text": "Karvir village has a water shortage problem",
        "expect_category": "water",
        # Karvir IS in mock gazetteer → should resolve
        "expect_location_district": "Kolhapur",
    },
    {
        "id": "AMB-05",
        "text": "Baramati mein road ki bahut buri haalat hai",
        "expect_category": "road",
        "expect_severity": "high",
        "expect_location_district": "Pune",
    },

    # ── Edge cases (5+) ───────────────────────────────────────────────────────
    {
        "id": "EDGE-01",
        "text": "",   # empty input
        "expect_category": None,
        "expect_confidence_max": 0.20,
    },
    {
        "id": "EDGE-02",
        "text": "The water pipe broke urgently near Panhala",
        "expect_category": "water",
        "expect_severity": "high",
        "expect_location_district": "Kolhapur",
    },
    {
        "id": "EDGE-03",
        "text": "road",   # single-word input
        "expect_category": "road",
    },
    {
        "id": "EDGE-04",
        "text": "Hamare gaon mein road bahut kharab hai",
        "is_synthetic": True,
        "expect_synthetic": True,
        "expect_category": "road",
    },
    {
        "id": "EDGE-05",
        "text": "The road broke again and again every day near Kagal block",
        "duplicate_count": 10,
        "expect_category": "road",
        "expect_severity": "high",
    },
    {
        "id": "EDGE-06",
        "text": "Thoda paani aata hai occasionally",
        "expect_category": "water",
        "expect_severity": "low",
    },
]


# ── Schema validator ──────────────────────────────────────────────────────────

def _validate_schema(record: dict, case_id: str) -> list[str]:
    errors = []
    missing = REQUIRED_KEYS - set(record.keys())
    if missing:
        errors.append(f"[{case_id}] Missing keys: {missing}")
    if not isinstance(record.get("confidence_overall"), float):
        errors.append(f"[{case_id}] confidence_overall must be float")
    return errors


# ── Main runner ───────────────────────────────────────────────────────────────

def run_tests(gazetteer_rows: list[dict] | None = None) -> int:
    """
    Run all TEST_CASES. Returns exit code (0 = all passed).
    """
    rows = gazetteer_rows if gazetteer_rows is not None else MOCK_GAZETTEER
    passed = 0
    failed = 0
    schema_errors: list[str] = []

    SEP = "-" * 65
    print(f"\n{SEP}")
    print(f"  P1 Pipeline Test Suite  |  {len(TEST_CASES)} cases")
    print(SEP)

    for case in TEST_CASES:
        cid  = case["id"]
        text = case.get("text", "")

        input_data: dict = {"text": text}
        if case.get("duplicate_count"):
            input_data["duplicate_count"] = case["duplicate_count"]
        if case.get("is_synthetic"):
            input_data["is_synthetic"] = True

        result = process_report(input_data, rows)

        # Schema check
        schema_errors.extend(_validate_schema(result, cid))

        # Assertion checks
        ok = True
        reasons: list[str] = []

        if "expect_category" in case:
            if result["issue_category"] != case["expect_category"]:
                ok = False
                reasons.append(
                    f"category: got {result['issue_category']!r}, "
                    f"want {case['expect_category']!r}"
                )

        if "expect_severity" in case:
            if result["severity"] != case["expect_severity"]:
                ok = False
                reasons.append(
                    f"severity: got {result['severity']!r}, "
                    f"want {case['expect_severity']!r}"
                )

        if "expect_lang_prefix" in case:
            lang = result.get("language_detected", "")
            if not lang.startswith(case["expect_lang_prefix"]):
                ok = False
                reasons.append(
                    f"language: got {lang!r}, "
                    f"want prefix {case['expect_lang_prefix']!r}"
                )

        if case.get("expect_no_location_resolved"):
            if result["location_resolved"] is not None:
                ok = False
                reasons.append("expected location_resolved=None")

        if "expect_location_district" in case:
            lr = result.get("location_resolved")
            got = lr.get("district") if lr else None
            if got != case["expect_location_district"]:
                ok = False
                reasons.append(
                    f"district: got {got!r}, "
                    f"want {case['expect_location_district']!r}"
                )

        if "expect_confidence_max" in case:
            if result["confidence_overall"] > case["expect_confidence_max"]:
                ok = False
                reasons.append(
                    f"confidence {result['confidence_overall']} "
                    f"> max {case['expect_confidence_max']}"
                )

        if case.get("expect_synthetic"):
            if not result.get("is_synthetic"):
                ok = False
                reasons.append("expected is_synthetic=True")

        # Report
        status = "PASS" if ok else "FAIL"
        short_text = repr(text[:40]) if text else "<empty>"
        reason_str = " | " + "; ".join(reasons) if reasons else ""
        print(f"  [{status}] {cid:8s}  {short_text}{reason_str}")

        if ok:
            passed += 1
        else:
            failed += 1

    # Schema error summary
    if schema_errors:
        print(f"\n  ⚠  Schema errors ({len(schema_errors)}):")
        for err in schema_errors:
            print(f"     {err}")

    print(f"\n{SEP}")
    print(f"  Result: {passed}/{len(TEST_CASES)} passed, {failed} failed")
    print(f"{SEP}\n")

    return 0 if failed == 0 and not schema_errors else 1


# ── pytest-compatible test function ──────────────────────────────────────────

def test_all_cases():
    """pytest entry point — fails if any case fails."""
    exit_code = run_tests(MOCK_GAZETTEER)
    assert exit_code == 0, "One or more pipeline test cases failed."


if __name__ == "__main__":
    sys.exit(run_tests(MOCK_GAZETTEER))