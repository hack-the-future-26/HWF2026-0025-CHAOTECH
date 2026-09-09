"""
Tests for location-phrase extraction.

Previously assertion-free, and its own printed output showed a case labelled
"[no match -> None]" returning "trigger words this sentence" -- a visible
failure that could not fail the run. Now asserted.

The first case below is the regression test for the bug where the FIRST
trigger word won: "Hamare gaon mein road bahut kharab hai, Karvir ke paas"
returned "Hamare road bahut" because "gaon" at position 1 grabbed the window
and the real place name near the later trigger was never looked at.
"""

import sys

from location import extract_location_phrase

sys.stdout.reconfigure(encoding="utf-8")

# (text, expected substring in the result, or None meaning "must be None")
CASES = [
    # Regression: the place name sits at the END, behind a later trigger.
    ("Hamare gaon mein road bahut kharab hai, Karvir ke paas", "Karvir"),
    ("sadak bahut kharab hai near Kagal", "Kagal"),
    ("pani nahi aata teen din se Naydongri gaon mein", "Naydongri"),
    ("The road near Kagal village is unusable during monsoon", "Kagal"),
    ("water problem near, Kagal chowk.", "Kagal"),
    ("रस्ता खूप खराब आहे Talsande जवळ", "Talsande"),
    ("सड़क में बड़ा गड्ढा है Kothali गांव में", "Kothali"),
    # Sentence-initial place name, no preceding trigger.
    ("Baramati mein road ki bahut buri haalat hai", "Baramati"),
    # Nothing place-like at all.
    ("The road is bad", None),
]

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [PASS] {label}")
    else:
        _failures.append(label)
        print(f"  [FAIL] {label}  {detail}")


def run() -> None:
    print("=== extract_location_phrase ===")
    for text, expected in CASES:
        result = extract_location_phrase(text)
        if expected is None:
            check(f"{text[:44]!r} -> None", result is None, f"got {result!r}")
        else:
            check(
                f"{text[:44]!r} -> contains {expected!r}",
                result is not None and expected in result,
                f"got {result!r}",
            )

    print("\n=== the extracted phrase should not be pure domain vocabulary ===")
    noise = {"road", "bahut", "hamare", "water", "hai"}
    result = extract_location_phrase("Hamare gaon mein road bahut kharab hai, Karvir ke paas")
    tokens = set((result or "").lower().split())
    check(
        "no complaint-vocabulary words leak into the location phrase",
        not (tokens & noise),
        f"got {result!r}",
    )

    total = len(CASES) + 1
    print(f"\n  Result: {total - len(_failures)}/{total} passed, {len(_failures)} failed")
    if _failures:
        sys.exit(1)


if __name__ == "__main__":
    run()
