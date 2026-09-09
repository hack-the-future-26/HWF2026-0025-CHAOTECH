# ── Issue-category keyword dictionaries ──────────────────────────────────────
# Each key is the canonical category name.
# Keywords span Hindi (Devanagari + Latin), English, and Marathi.

ISSUE_KEYWORDS: dict[str, list[str]] = {
    "road": [
        # Hindi Devanagari — singular & plural forms
        "सड़क", "सड़के", "रास्ता", "रस्ता", "रोड",
        "गड्ढा", "गड्ढे", "गड्ढों",          # pothole singular + plurals
        "टूटी", "टूटा", "टूटे",
        "खराब सड़क", "कच्ची सड़क", "टूटी सड़क",
        "दुर्घटना",                              # accident → road issue
        # Hindi Latin / Hinglish
        "road", "sadak", "rasta", "pothole",
        "gaddha", "gaddhe", "gaddho",
        "broken road", "kacchi sadak",
        # English
        "highway", "bridge", "culvert", "path", "track", "pavement",
        "unpaved", "damaged road", "bad road", "accident",
        # Marathi
        "रस्ता", "रस्त्याची", "खड्डा", "खड्डे", "रस्त्यात",
    ],

    "water": [
        # Hindi Devanagari
        "पानी", "नल", "बोरवेल", "पेयजल", "पानी की समस्या",
        "पानी नहीं", "गंदा पानी", "नल खराब",
        # Hindi Latin / Hinglish
        "water", "paani", "pani", "nala", "borewell", "naala",
        "drinking water", "ganda pani",
        # English
        "tap", "pipeline", "supply", "shortage", "contaminated",
        "leakage", "drainage", "sewage",
        # Marathi
        "पाणी", "पाण्याची", "नळ", "बोअरवेल", "पाणीपुरवठा",
        "दूषित पाणी",
    ],

    "health": [
        # Hindi Devanagari
        "अस्पताल", "दवाखाना", "डॉक्टर", "दवा", "स्वास्थ्य",
        "स्वास्थ्य केंद्र", "एम्बुलेंस", "नर्स",
        # Hindi Latin / Hinglish
        "hospital", "doctor", "dawakhana", "aspatal", "phc",
        "medicine", "ambulance", "health centre", "clinic",
        # English
        "medical", "nurse", "health", "dispensary", "asha",
        "vaccination", "immunization",
        # Marathi
        "रुग्णालय", "दवाखाना", "डॉक्टर", "औषध", "आरोग्य",
        "आरोग्य केंद्र",
    ],

    "education": [
        # Hindi Devanagari
        "स्कूल", "विद्यालय", "पाठशाला", "शिक्षक", "अध्यापक",
        "कक्षा", "पढ़ाई", "किताब",
        # Hindi Latin / Hinglish
        "school", "vidyalaya", "teacher", "padhna", "kitab",
        "student", "class",
        # English
        "education", "classroom", "textbook", "midday meal",
        "anganwadi", "enrolment",
        # Marathi
        "शाळा", "शिक्षक", "वर्ग", "विद्यार्थी", "पुस्तके",
        "शाळेत",
    ],
}


# ── Severity keyword tiers ────────────────────────────────────────────────────
SEVERITY_HIGH_KEYWORDS = [
    # Hindi Devanagari
    "बहुत", "बेहद", "गंभीर", "खतरनाक", "बार-बार", "हमेशा",
    "तुरंत", "आपातकाल",
    # Hindi Latin / Hinglish
    "bahut", "severe", "urgent", "critical", "emergency",
    "repeated", "bar bar", "baar baar", "always", "every day",
    "har din", "dangerous",
    # English
    "crisis", "accident", "deaths", "injuries", "collapsed",
    # Marathi
    "खूप", "गंभीर", "धोकादायक", "वारंवार", "नेहमी",
]

SEVERITY_LOW_KEYWORDS = [
    # Hindi
    "thoda", "थोड़ा", "kabhi kabhi", "कभी-कभी",
    # English
    "minor", "slight", "sometimes", "occasional",
    # Marathi
    "थोडे", "कधी कधी",
]


def extract_issue_category(text: str) -> str | None:
    """
    Match text against ISSUE_KEYWORDS and return the category with the MOST
    keyword hits. Declaration order breaks genuine ties, so a report that
    really is ambiguous behaves exactly as it did before.

    Returning the *first* category with any hit -- the previous behaviour --
    made the answer depend on dictionary declaration order rather than on the
    text. "sadak ke paas hospital hai lekin doctor nahi, clinic bhi band hai"
    came back as `road` on a single incidental mention of a road, beating
    three health keywords, purely because road is declared first. The report
    is plainly about health.
    """
    t = text.lower()

    best_category: str | None = None
    best_hits = 0

    for category, keywords in ISSUE_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw.lower() in t)
        # Strictly greater keeps declaration order as the tie-break.
        if hits > best_hits:
            best_hits = hits
            best_category = category

    return best_category


def extract_severity(
    text: str,
    duplicate_count: int = 0,
) -> str:
    """
    Three-tier severity classification:
      high   — explicit urgency keywords OR many duplicate reports
      low    — explicit minor/occasional keywords AND no high keywords
      medium — everything else (default)
    """
    t = text.lower()

    has_high = any(kw.lower() in t for kw in SEVERITY_HIGH_KEYWORDS)
    has_low  = any(kw.lower() in t for kw in SEVERITY_LOW_KEYWORDS)

    if has_high or duplicate_count > 5:
        return "high"

    if has_low and not has_high:
        return "low"

    return "medium"