import re


# ── Script ranges ────────────────────────────────────────────────────────────
DEVANAGARI = re.compile(r"[\u0900-\u097F]")
LATIN_WORD  = re.compile(r"[a-zA-Z]")

# Other Indian scripts.
#
# Without these, the detector knew only Devanagari and Latin, so EVERY other
# Indian script fell through to the final `return "en"` -- Tamil, Telugu,
# Bengali, Kannada, Gujarati and Punjabi were all silently reported as
# English. That is worse than returning "unknown": downstream code trusted
# the label, ran English/Hindi keyword matching over Tamil text, found
# nothing, and produced a confident-looking record built on a false premise.
#
# These languages are NOT supported end to end -- no keyword dictionaries
# exist for them (see extract.py). Naming them correctly is what lets
# main.py assign a low confidence and flag them for human review instead of
# quietly mislabelling them as English.
OTHER_INDIC_SCRIPTS = [
    ("bn-Beng", re.compile(r"[\u0980-\u09FF]")),  # Bengali / Assamese
    ("pa-Guru", re.compile(r"[\u0A00-\u0A7F]")),  # Gurmukhi (Punjabi)
    ("gu-Gujr", re.compile(r"[\u0A80-\u0AFF]")),  # Gujarati
    ("or-Orya", re.compile(r"[\u0B00-\u0B7F]")),  # Odia
    ("ta-Taml", re.compile(r"[\u0B80-\u0BFF]")),  # Tamil
    ("te-Telu", re.compile(r"[\u0C00-\u0C7F]")),  # Telugu
    ("kn-Knda", re.compile(r"[\u0C80-\u0CFF]")),  # Kannada
    ("ml-Mlym", re.compile(r"[\u0D00-\u0D7F]")),  # Malayalam
]

# Languages the rest of the pipeline can actually process end to end.
SUPPORTED_LANGUAGES = {"hi-Deva", "mr-Deva", "hi-Latn", "mixed", "en"}


def is_supported(language: str) -> bool:
    """
    Whether the pipeline has keyword dictionaries and test coverage for this
    language. Anything else is detected honestly, then flagged -- never
    processed as though it were English.
    """
    return language in SUPPORTED_LANGUAGES

# ── Marathi function-word markers ────────────────────────────────────────────
MARATHI_MARKERS = [
    "आहे", "नाही", "मला", "तुम्ही", "काय", "गाव",
    "आमच्या", "आमच्या", "त्यांनी", "होते", "केले",
    "आम्हाला", "आमच्या", "झाले", "असे", "तसे",
    "जवळ", "च्या", "ला", "ना", "हो",
]

# ── Hindi function-word markers (Devanagari) ─────────────────────────────────
HINDI_DEVA_MARKERS = [
    "है", "हैं", "था", "थे", "की", "के", "का",
    "में", "से", "को", "पर", "और", "भी", "नहीं",
    "हमारे", "हमारा", "यहाँ", "वहाँ", "बहुत",
]

# ── Hinglish / Hindi-Latin markers ──────────────────────────────────────────
HINDI_LATIN_MARKERS = [
    "hai", "nahi", "gaon", "sadak", "paani", "pani",
    "bahut", "kharab", "baarish", "rasta", "ganda",
    "nala", "bijli", "sarkaar", "doctor", "aspatal",
    "theek", "achha", "zyada", "bilkul",
]


def detect_language(text: str) -> str:
    """
    Rule-based language identification.

    Returns one of:
        mr-Deva  — Marathi in Devanagari
        hi-Deva  — Hindi in Devanagari
        hi-Latn  — Hindi / Hinglish in Latin script
        mixed    — Code-mixed (both Devanagari and Latin present)
        en       — English
        ta-Taml / te-Telu / bn-Beng / kn-Knda / gu-Gujr / pa-Guru /
        ml-Mlym / or-Orya
                 — detected but NOT supported downstream; use is_supported()
                   before trusting anything extracted from these.
    """
    if not text or not text.strip():
        return "en"

    text = text.strip()

    # Check the other Indian scripts BEFORE the Latin fallback, so they are
    # named rather than swept into "en".
    for code, pattern in OTHER_INDIC_SCRIPTS:
        if pattern.search(text):
            return code

    has_devanagari = bool(DEVANAGARI.search(text))
    has_latin_words = bool(re.search(r"[a-zA-Z]{2,}", text))  # ≥2 Latin chars

    # ── Code-mixed: both scripts present ────────────────────────────────────
    if has_devanagari and has_latin_words:
        return "mixed"

    # ── Pure Devanagari ──────────────────────────────────────────────────────
    if has_devanagari:
        # Count Marathi vs Hindi markers
        marathi_hits = sum(1 for m in MARATHI_MARKERS if m in text)
        hindi_hits   = sum(1 for m in HINDI_DEVA_MARKERS if m in text)

        if marathi_hits > hindi_hits:
            return "mr-Deva"
        return "hi-Deva"

    # ── Latin script ─────────────────────────────────────────────────────────
    text_lower = text.lower()
    if any(marker in text_lower for marker in HINDI_LATIN_MARKERS):
        return "hi-Latn"

    return "en"