"""
Tests for issue-category and severity extraction.

Previously this file only printed its results and asserted nothing, so it
passed unconditionally -- including while it expected categories
("electricity", "sanitation") the extractor has never supported, and an
"expected" value of "road (Devanagari)" that is not a value the function can
return. Every case below is now a real assertion against the four frozen
demo categories.
"""

import sys

from extract import extract_issue_category, extract_severity

sys.stdout.reconfigure(encoding="utf-8")

CATEGORY_CASES = [
    ("sadak mein bahut gaddha hai", "road"),
    ("paani ki samasya hai", "water"),
    ("hospital bahut door hai", "health"),
    ("school mein shikshak nahi hai", "education"),
    ("सड़क में गड्ढा है", "road"),
    ("पानी नहीं आ रहा है", "water"),
    ("रस्ता खूप खराब आहे", "road"),
    # Most keyword hits wins, with declaration order breaking genuine ties.
    ("sadak ke paas hospital hai lekin doctor nahi, clinic bhi band hai", "health"),
    # Out of scope on purpose: electricity and sanitation are not among the
    # four frozen demo categories, so they must return None rather than being
    # forced into the nearest one.
    ("bijli nahi hai", None),
    ("kachra bahut din se nahi utha", None),
    ("some random unrelated sentence", None),
]

SEVERITY_CASES = [
    ("yeh bahut urgent hai please turant fix karo", "high"),
    ("normal complaint about a small issue", "medium"),
    ("thoda sa problem hai occasionally", "low"),
]

_failures: list[str] = []


def check(label: str, got, expected) -> None:
    if got == expected:
        print(f"  [PASS] {label} -> {got!r}")
    else:
        _failures.append(label)
        print(f"  [FAIL] {label} -> got {got!r}, expected {expected!r}")


def run() -> None:
    print("=== extract_issue_category ===")
    for text, expected in CATEGORY_CASES:
        check(text[:48], extract_issue_category(text), expected)

    print("\n=== extract_severity ===")
    for text, expected in SEVERITY_CASES:
        check(text[:48], extract_severity(text), expected)

    total = len(CATEGORY_CASES) + len(SEVERITY_CASES)
    print(f"\n  Result: {total - len(_failures)}/{total} passed, {len(_failures)} failed")
    if _failures:
        sys.exit(1)


if __name__ == "__main__":
    run()
