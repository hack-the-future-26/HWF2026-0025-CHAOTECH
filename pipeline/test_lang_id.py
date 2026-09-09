"""
Tests for language identification.

No test file existed for this module, which is how the biggest bug in it
survived: every Indian script other than Devanagari fell through to
`return "en"`, so Tamil, Telugu, Bengali, Kannada, Gujarati, Punjabi,
Malayalam and Odia were all reported as English. The
"not_mislabelled_as_english" cases below are the regression test.
"""

import sys

from lang_id import SUPPORTED_LANGUAGES, detect_language, is_supported

sys.stdout.reconfigure(encoding="utf-8")

SUPPORTED_CASES = [
    ("आप कैसे हैं", "hi-Deva"),
    ("हमारे गाँव में सड़क खराब है", "hi-Deva"),
    ("पानी नहीं आ रहा है", "hi-Deva"),
    ("रस्ता खूप खराब आहे", "mr-Deva"),
    ("मला पाणी मिळत नाही", "mr-Deva"),
    ("आमच्या गावात रस्ता खराब आहे", "mr-Deva"),
    ("sadak bahut kharab hai", "hi-Latn"),
    ("paani nahi aata gaon mein", "hi-Latn"),
    ("The road is broken", "en"),
    ("no water supply in our village", "en"),
    ("हमारे gaon mein road kharab", "mixed"),
    ("पाणी problem आहे", "mixed"),
    ("", "en"),
]

# Detected correctly, but the pipeline has no keyword dictionaries for them.
UNSUPPORTED_CASES = [
    ("சாலை மிகவும் மோசமாக உள்ளது", "ta-Taml", "Tamil"),
    ("రోడ్డు చాలా చెడ్డది", "te-Telu", "Telugu"),
    ("রাস্তা খুব খারাপ", "bn-Beng", "Bengali"),
    ("ರಸ್ತೆ ತುಂಬಾ ಕೆಟ್ಟದಾಗಿದೆ", "kn-Knda", "Kannada"),
    ("રસ્તો ખૂબ ખરાબ છે", "gu-Gujr", "Gujarati"),
    ("ਸੜਕ ਬਹੁਤ ਖਰਾਬ ਹੈ", "pa-Guru", "Punjabi"),
    ("റോഡ് വളരെ മോശമാണ്", "ml-Mlym", "Malayalam"),
    ("ରାସ୍ତା ବହୁତ ଖରାପ", "or-Orya", "Odia"),
]

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [PASS] {label}")
    else:
        _failures.append(label)
        print(f"  [FAIL] {label}  {detail}")


def run() -> None:
    print("=== supported languages ===")
    for text, expected in SUPPORTED_CASES:
        got = detect_language(text)
        check(f"{text[:34]!r} -> {expected}", got == expected, f"got {got!r}")

    print("\n=== other Indian scripts: detected, and NOT called English ===")
    for text, expected, name in UNSUPPORTED_CASES:
        got = detect_language(text)
        check(f"{name} -> {expected}", got == expected, f"got {got!r}")
        check(f"{name} is not mislabelled as English", got != "en", f"got {got!r}")

    print("\n=== is_supported() ===")
    for language in SUPPORTED_LANGUAGES:
        check(f"{language} is supported", is_supported(language))
    for _text, code, name in UNSUPPORTED_CASES:
        check(f"{name} ({code}) is flagged unsupported", not is_supported(code))

    total = (
        len(SUPPORTED_CASES)
        + len(UNSUPPORTED_CASES) * 2
        + len(SUPPORTED_LANGUAGES)
        + len(UNSUPPORTED_CASES)
    )
    print(f"\n  Result: {total - len(_failures)}/{total} passed, {len(_failures)} failed")
    if _failures:
        sys.exit(1)


if __name__ == "__main__":
    run()
