"""
Emergency Urgency Detection & Signal Fusion (Feature #7).

Urgency means an emergency only: bridge breakage or building breakage,
including cracks -- not full collapse only. It is explicitly NOT for
potholes, a missing teacher, a missing doctor, or water supply problems.

Grading:
    crack (1/3) < partial damage (2/3) < collapse (1.0)
Scaled by 0-1 confidence from independent signals (none decisive alone):
    1. Photo damage classifier (buildings only; scoped as optional interface until C6 lands)
    2. Catastrophic wording in report text (multilingual: EN, HI, MR)
    3. Short-window report burst (velocity_term / burst_ratio)
    4. Active SACHET heavy-rain/flood alert or nearby CWC river warning
"""

from __future__ import annotations

import re
from . import config

# ---------------------------------------------------------------------------
# Keywords: Bridge & Building Structures
# ---------------------------------------------------------------------------

BRIDGE_KEYWORDS = [
    "bridge", "culvert", "flyover", "overbridge", "footbridge",
    "pul", "pool", "setu",
    "पूल", "सेतु", "साकव",
]

BUILDING_KEYWORDS = [
    "building", "roof", "wall", "ceiling", "classroom", "school",
    "hospital", "phc", "clinic", "dispensary", "subcentre", "sub-centre",
    "structure", "chhat", "deewar", "diwar", "imarat", "bhint", "kholi", "kamra",
    "छत", "दीवार", "भिंत", "इमारत", "खोली", "कमरा", "शाळा", "रुग्णालय", "दवाखाना",
    "वर्ग", "कक्षा", "टाकी", "tank",
]

# ---------------------------------------------------------------------------
# Keywords: Damage Tiers (Collapse, Partial Damage, Crack)
# ---------------------------------------------------------------------------

COLLAPSE_KEYWORDS = [
    # English
    "collapse", "collapsed", "collapsing", "washed away", "washed out",
    "fallen down", "fell down", "caved in", "cave in", "destroyed",
    # Hindi (Devanagari & transliterated)
    "गिर गया", "गिर गई", "ढह गया", "ढह गई", "बह गया", "बह गई", "ध्वस्त",
    "gir gaya", "gir gayi", "dhah gaya", "dhah gayi", "beh gaya", "beh gayi",
    # Marathi (Devanagari & transliterated)
    "कोसळला", "कोसळली", "वाहून गेला", "वाहून गेली", "पडला", "पडली", "उद्ध्वस्त", "खचून गेला",
    "kosalla", "kosalli", "vahun gela", "vahun geli", "padla", "padli",
]

PARTIAL_DAMAGE_KEYWORDS = [
    # English
    "partially collapsed", "partial collapse", "partially damaged", "partial damage",
    "damaged", "broken", "fractured", "weakened", "sinking", "tilted", "unsafe",
    "gave way",
    # Hindi
    "क्षतिग्रस्त", "टूटा हुआ", "टूटी हुई", "टूट गया", "टूट गई", "नुकसान",
    "tuta", "tuti", "tut gaya", "tut gayi", "kshatigrast",
    # Marathi
    "पडझड", "मोडतोड", "खचला", "खचली", "मोडला", "मोडली", "भंगला",
    "padjhad", "khachla", "khachli", "modla", "modli",
]

CRACK_KEYWORDS = [
    # English
    "crack", "cracks", "cracked", "hairline", "fissure", "crevice",
    # Hindi
    "दरार", "दरारें", "तरेड़", "चटक",
    "darar", "dararein",
    # Marathi
    "तडा", "तडे", "भेग", "भेगा", "चिर", "चिरा",
    "tada", "tade", "bheg", "bhega",
]


def detect_report_emergency(text: str, category: str | None = None) -> tuple[float, str | None, dict]:
    """
    Examines report text for bridge or building damage.

    Returns:
        (grade, grade_label, match_details)
        grade: 0.0, 1/3 (crack), 2/3 (partial), 1.0 (collapse)
        grade_label: None, "crack", "partial", "collapse"
    """
    if not text:
        return 0.0, None, {}

    t = text.lower()

    # Determine structure relevance:
    # - In road / water: MUST mention bridge or structural component explicitly.
    # - In education / health: the facility itself is a school or hospital building.
    is_bridge = any(kw in t for kw in BRIDGE_KEYWORDS)
    is_building = any(kw in t for kw in BUILDING_KEYWORDS) or category in ("education", "health")

    if not (is_bridge or is_building):
        return 0.0, None, {}

    # Check damage tiers from highest severity to lowest
    matched_damage = None
    grade = 0.0
    grade_label = None

    for kw in COLLAPSE_KEYWORDS:
        if kw in t:
            # Special check for road category:
            # If in road category and not mentioning a bridge, "sadak tut gayi" or "road collapsed"
            # without a bridge/culvert refers to road surface / pavement subsidence, which is road condition.
            if category == "road" and not is_bridge:
                continue
            matched_damage = kw
            grade = config.EMERGENCY_GRADE_COLLAPSE
            grade_label = "collapse"
            break

    if grade == 0.0:
        for kw in PARTIAL_DAMAGE_KEYWORDS:
            if kw in t:
                if category == "road" and not is_bridge:
                    continue
                matched_damage = kw
                grade = config.EMERGENCY_GRADE_PARTIAL
                grade_label = "partial"
                break

    if grade == 0.0:
        for kw in CRACK_KEYWORDS:
            if kw in t:
                if category == "road" and not is_bridge:
                    continue
                matched_damage = kw
                grade = config.EMERGENCY_GRADE_CRACK
                grade_label = "crack"
                break

    if grade == 0.0:
        return 0.0, None, {}

    match_details = {
        "is_bridge": is_bridge,
        "is_building": is_building,
        "matched_damage": matched_damage,
        "sample_text": text[:200],
    }
    return grade, grade_label, match_details


def evaluate_emergency_signals(
    members: list[dict],
    category: str | None,
    *,
    velocity: float = 0.0,
    hazard_flag: bool = False,
    hazard_evidence: dict | None = None,
    photo_damage: dict | None = None,
) -> dict:
    """
    Evaluates the combined emergency signals across member reports:
      1. Photo damage classifier (scoped down / optional)
      2. Catastrophic wording in reports
      3. Report burst velocity
      4. Active SACHET / CWC hazard alert

    None of the signals is decisive alone.
    If no emergency is detected (or signals don't fire), urgency is 0.0.
    """
    if not members and not photo_damage:
        return {
            "grade": 0.0,
            "grade_label": None,
            "confidence": 0.0,
            "urgency": 0.0,
            "evidence": {
                "urgency_points": 0.0,
                "grade": 0.0,
                "grade_label": None,
                "confidence": 0.0,
                "signals": {},
            },
        }

    # Signal 2: Catastrophic wording
    detected_reports = []
    seen_texts = set()
    distinct_wording_reporters = 0

    for m in members:
        raw = m.get("raw_text") or ""
        g, glabel, details = detect_report_emergency(raw, category)
        if g > 0.0:
            detected_reports.append((g, glabel, details, raw))
            norm_key = raw.strip().lower()
            if norm_key not in seen_texts:
                seen_texts.add(norm_key)
                distinct_wording_reporters += 1

    wording_grade = max((r[0] for r in detected_reports), default=0.0)
    wording_label = (
        max(detected_reports, key=lambda r: r[0])[1] if detected_reports else None
    )

    # Signal 1: Photo damage classifier
    photo_grade = 0.0
    photo_label = None
    photo_conf = 0.0
    if photo_damage and photo_damage.get("grade", 0.0) > 0.0:
        photo_grade = float(photo_damage["grade"])
        photo_label = photo_damage.get("grade_label")
        photo_conf = float(photo_damage.get("confidence", 0.0))

    # Overall grade is the highest detected grade
    grade = max(wording_grade, photo_grade)
    grade_label = wording_label if wording_grade >= photo_grade else photo_label

    if grade <= 0.0:
        # Ground rule: Never fabricate a value. If no emergency is claimed
        # by photo or report wording, urgency is strictly 0.0.
        return {
            "grade": 0.0,
            "grade_label": None,
            "confidence": 0.0,
            "urgency": 0.0,
            "evidence": {
                "urgency_points": 0.0,
                "grade": 0.0,
                "grade_label": None,
                "confidence": 0.0,
                "signals": {
                    "catastrophic_wording": {"fired": False, "contribution": 0.0},
                    "report_burst": {"fired": velocity > 0.0, "velocity": round(velocity, 4), "contribution": 0.0},
                    "hazard_alert": {"fired": hazard_flag, "contribution": 0.0},
                    "photo_damage_model": {"fired": False, "status": "not_implemented", "contribution": 0.0},
                },
            },
        }

    # Signal confidence contributions (none decisive alone)
    # Wording:
    if distinct_wording_reporters >= 2:
        wording_conf = config.EMERGENCY_CONF_WORDING_CORROBORATED
    elif distinct_wording_reporters == 1:
        wording_conf = config.EMERGENCY_CONF_WORDING_SINGLE
    else:
        wording_conf = 0.0

    # Burst velocity:
    burst_conf = min(config.EMERGENCY_CONF_BURST_MAX, config.EMERGENCY_CONF_BURST_MAX * max(0.0, velocity))

    # Hazard alerts (SACHET / CWC river):
    hazard_conf = 0.0
    sachet_count = 0
    cwc_count = 0
    if hazard_evidence:
        sachet_count = hazard_evidence.get("sachet_alerts", 0)
        cwc_count = hazard_evidence.get("cwc_river_warnings", 0)
        if sachet_count > 0:
            hazard_conf += config.EMERGENCY_CONF_HAZARD_SACHET
        if cwc_count > 0:
            hazard_conf += config.EMERGENCY_CONF_HAZARD_RIVER
        hazard_conf = min(config.EMERGENCY_CONF_HAZARD_MAX, hazard_conf)
    elif hazard_flag:
        hazard_conf = config.EMERGENCY_CONF_HAZARD_SACHET

    # Photo model signal (if wired):
    photo_signal_conf = min(config.EMERGENCY_CONF_PHOTO_MAX, config.EMERGENCY_CONF_PHOTO_MAX * max(0.0, photo_conf))

    total_conf = min(1.0, wording_conf + burst_conf + hazard_conf + photo_signal_conf)
    total_conf = round(total_conf, 4)

    urgency = round(config.URGENCY_POINTS * grade * total_conf, 2)

    evidence = {
        "urgency_points": urgency,
        "grade": round(grade, 4),
        "grade_label": grade_label,
        "confidence": total_conf,
        "signals": {
            "catastrophic_wording": {
                "fired": wording_grade > 0.0,
                "distinct_reporters": distinct_wording_reporters,
                "contribution": round(wording_conf, 4),
                "matched_samples": [r[3][:150] for r in detected_reports[:3]],
            },
            "report_burst": {
                "fired": velocity > 0.0,
                "velocity": round(velocity, 4),
                "contribution": round(burst_conf, 4),
            },
            "hazard_alert": {
                "fired": hazard_conf > 0.0,
                "sachet_alerts": sachet_count,
                "cwc_river_warnings": cwc_count,
                "contribution": round(hazard_conf, 4),
            },
            "photo_damage_model": {
                "fired": photo_grade > 0.0,
                "status": "wired" if photo_damage else "not_implemented",
                "contribution": round(photo_signal_conf, 4),
            },
        },
    }

    return {
        "grade": grade,
        "grade_label": grade_label,
        "confidence": total_conf,
        "urgency": urgency,
        "evidence": evidence,
    }

