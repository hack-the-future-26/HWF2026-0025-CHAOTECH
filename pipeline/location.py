import re

# ── Trigger words that often precede/surround a location name ────────────────
LOCATION_TRIGGERS_EN = [
    "village", "gaon", "gram", "ward", "block",
    "near", "in", "at", "of", "from", "mein",
    "district", "tehsil", "taluka", "panchayat",
    # Transliterated proximity words -- "Karvir ke paas" / "Talsande javal"
    # are extremely common in Hinglish/Marathi-Latin input and were missing,
    # so the place name in them had no nearby trigger at all.
    "paas", "pas", "javal", "jawal", "javal", "chowk", "phata", "naka",
]

LOCATION_TRIGGERS_HI = [
    # Devanagari
    "गांव", "गाव", "गाँव", "ग्राम", "वार्ड", "ब्लॉक",
    "जवळ", "पास", "में", "मे", "के", "की",
    "जिला", "तहसील", "पंचायत", "तालुका",
]

ALL_TRIGGERS = set(
    [t.lower() for t in LOCATION_TRIGGERS_EN]
    + LOCATION_TRIGGERS_HI
)

# ── Devanagari word pattern (for proper-noun heuristic) ──────────────────────
_DEVA_WORD = re.compile(r"[\u0900-\u097F]+")

# ── Stopwords to exclude from Devanagari proper-noun detection ───────────────
DEVA_STOPWORDS = {
    "है", "हैं", "था", "थे", "की", "के", "का", "में", "से", "को", "पर",
    "और", "भी", "नहीं", "यह", "वह", "हम", "आप", "यहाँ", "वहाँ", "बहुत",
    "गंभीर", "खराब", "पानी", "सड़क", "अस्पताल", "स्कूल", "बोरवेल", "नल",
    "आहे", "नाही", "मला", "गाव", "पाणी", "रस्ता",
}

# Words to strip from the returned phrase so the geocoder gets a clean name
PHRASE_NOISE_WORDS = {
    "mein", "me", "near", "in", "at", "of", "from", "the",
    "village", "gaon", "gram", "ward", "block", "district",
    "tehsil", "taluka", "panchayat", "ke", "ki", "ka",
}

# Latin-script words that are domain vocabulary, never place names. Used to
# score candidate windows: a window whose only content is "road bahut" is a
# worse location guess than one containing "Karvir".
DOMAIN_NOISE_WORDS = {
    # infrastructure nouns
    "road", "sadak", "rasta", "rastha", "gaddha", "pothole", "highway",
    "water", "paani", "pani", "nal", "borewell", "tap", "supply", "pipe",
    "hospital", "aspatal", "dawakhana", "doctor", "clinic", "phc", "health",
    "school", "shala", "shikshak", "teacher", "class", "education",
    # common complaint words that show up inside windows
    "bahut", "kharab", "bura", "buri", "haalat", "samasya", "problem",
    "nahi", "nahin", "hai", "hain", "he", "se", "kal", "din", "teen",
    "hamare", "hamara", "humara", "our", "is", "very", "bad", "please",
    "fix", "issue", "urgent", "turant", "severe", "cut", "off", "been",
    "has", "and", "for", "two", "days", "now", "no", "available", "center",
}


def _looks_like_place(token: str) -> bool:
    """
    True if a token plausibly names a place rather than describing the problem.

    Latin: Title-Case and not domain vocabulary. Devanagari: not a stopword
    and long enough to be a name.
    """
    clean = token.strip(".,!?;:")
    if not clean:
        return False
    low = clean.lower()
    if low in PHRASE_NOISE_WORDS or low in DOMAIN_NOISE_WORDS:
        return False
    if re.match(r"^[A-Z][a-zA-Z]{2,}$", clean):
        return True
    if _DEVA_WORD.fullmatch(clean) and clean not in DEVA_STOPWORDS and len(clean) >= 3:
        return True
    return False


def _clean_phrase(phrase: str) -> str:
    """
    Remove noise trigger/function words so the geocoder sees only the
    proper-noun part (e.g. 'Baramati mein road' -> 'Baramati').
    """
    tokens = phrase.split()
    cleaned = [
        t for t in tokens
        if t.lower().strip(".,!?;:") not in PHRASE_NOISE_WORDS
    ]
    return " ".join(cleaned).strip() or phrase  # fall back to original if all stripped


def extract_location_phrase(text: str) -> str | None:
    """
    Extract the most likely location phrase from a normalised complaint string.

    Strategy (in priority order):
    1. Trigger-word window: find a known trigger word and grab the 2-word window
       around it — this captures "near Karvir village", "gaon Karve", etc.
    2. Capitalised Latin word sequence: in English/Hinglish text, a run of
       Title-Case words not at the start of the sentence is likely a proper noun.
    3. Devanagari proper-noun heuristic: a Devanagari word that is NOT in the
       stopword list and appears after a trigger word.

    Returns None if no location phrase can be found.
    """
    words = text.split()

    # ── Strategy 1: trigger-word window, best candidate wins ────────────────
    # Returning the *first* trigger's window was wrong: in "Hamare gaon mein
    # road bahut kharab hai, Karvir ke paas" the trigger "gaon" at position 1
    # produced "Hamare road bahut" and the real place name, sitting next to
    # the later trigger "paas", was never looked at. Score every trigger
    # window instead and keep the one that actually contains a place-like
    # token.
    best_phrase: str | None = None
    best_score = 0

    for i, word in enumerate(words):
        clean = word.strip(".,!?;:")
        if clean.lower() not in ALL_TRIGGERS and clean not in ALL_TRIGGERS:
            continue

        window = words[max(0, i - 2): i + 4]
        phrase = " ".join(window).strip(".,!?;: ")
        if not phrase:
            continue

        cleaned = _clean_phrase(phrase) or phrase
        place_tokens = [t for t in cleaned.split() if _looks_like_place(t)]

        # A window containing a place-like token beats one that doesn't; among
        # those, prefer the one with least surrounding noise.
        score = 0
        if place_tokens:
            score = 100 - len(cleaned.split())

        if score > best_score:
            best_score = score
            # Hand the geocoder just the place-like tokens when we found them;
            # fuzzy-matching "Karvir" beats fuzzy-matching "road bahut Karvir".
            best_phrase = " ".join(place_tokens)
        elif best_phrase is None:
            best_phrase = cleaned

    # Only trust a trigger window that actually contained a place-like token.
    # A window of pure domain vocabulary ("Hamare road bahut") must NOT short-
    # circuit the proper-noun strategies below -- that is what hid "Karvir".
    if best_score > 0 and best_phrase:
        return best_phrase

    weak_phrase = best_phrase

    # -- Strategy 2: capitalised Latin sequence (>=1 title-case word, not pos 0)
    cap_pattern = re.compile(r"(?<!\A)(?<!\. )([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)")
    match = cap_pattern.search(text)
    if match:
        return _clean_phrase(match.group(1).strip())

    # -- Strategy 2b: sentence-initial Title-Case word that looks like a place
    # (handles "Baramati mein..." where Baramati is at position 0)
    if words and re.match(r"^[A-Z][a-z]{2,}$", words[0]):
        candidate = words[0]
        if candidate.lower() not in PHRASE_NOISE_WORDS:
            return candidate

    # -- Strategy 3: Devanagari word not in stopword list
    deva_words = _DEVA_WORD.findall(text)
    for dw in deva_words:
        if dw not in DEVA_STOPWORDS and len(dw) >= 3:
            return dw

    # Nothing better found -- fall back to the weak trigger window if there
    # was one, so behaviour is no worse than before this scoring pass.
    return weak_phrase