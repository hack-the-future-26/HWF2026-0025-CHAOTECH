import re


# ── Filler words / discourse markers to strip ────────────────────────────────
FILLERS = [
    # English
    "umm", "uh", "uhh", "hmm", "you know", "actually", "basically",
    "i mean", "like", "so", "well", "right",
    # Hindi (Latin)
    "matlab", "yani", "aur", "toh", "na", "haan",
    # Hindi (Devanagari)
    "मतलब", "यानी", "तो", "ना", "हाँ", "अच्छा",
    # Marathi
    "म्हणजे", "बरं", "हो ना",
]

# ── Transliteration / spelling normalisation map ─────────────────────────────
# key → canonical form used by keyword dictionaries
TRANSLIT_MAP = {
    # Place-name variants
    "gaanv":   "gaon",
    "gaaon":   "gaon",
    "gaon":    "gaon",
    "gawn":    "gaon",
    "gram":    "gaon",
    # Road
    "sadak":   "road",
    "saDak":   "road",
    "rasta":   "road",
    "raasta":  "road",
    "raste":   "road",
    # Water
    "paani":   "water",
    "pani":    "water",
    "paanee":  "water",
    # Health
    "aspatal": "hospital",
    "aspataal":"hospital",
    "dawakhana":"hospital",
    # Education
    "vidyalaya":"school",
    "shala":   "school",
    "shaala":  "school",
}


def normalize_text(text: str) -> str:
    """
    Strip filler words and normalize common transliteration variants.
    Returns a cleaned, whitespace-collapsed string.
    """
    t = text.strip()

    # Remove filler words (word-boundary aware, case-insensitive)
    for filler in sorted(FILLERS, key=len, reverse=True):  # longest first
        t = re.sub(
            rf"(?<!\w){re.escape(filler)}(?!\w)",
            " ",
            t,
            flags=re.IGNORECASE,
        )

    # Normalise transliteration variants
    for wrong, right in TRANSLIT_MAP.items():
        t = re.sub(
            rf"\b{re.escape(wrong)}\b",
            right,
            t,
            flags=re.IGNORECASE,
        )

    # Collapse whitespace
    return re.sub(r"\s+", " ", t).strip()