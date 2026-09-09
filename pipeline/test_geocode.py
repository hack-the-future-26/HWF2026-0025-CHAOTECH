"""
Tests for gazetteer geocoding.

This file previously asserted nothing AND built its fixture rows with
"latitude"/"longitude" keys, while geocode.py reads "lat"/"lon". So it
printed `lat: None` on every run and nobody noticed -- which is exactly the
production bug that left all 1,000+ stored reports without coordinates and
/map-data permanently empty.

test_coordinates_are_returned below is the regression test for that. If the
key names drift apart again, it fails.
"""

import sys

from geocode import resolve_location

sys.stdout.reconfigure(encoding="utf-8")

# Keys MUST be "lat"/"lon" -- the build plan's Interface Contract (Section 2)
# and what geocode.py actually reads.
GAZETTEER_ROWS = [
    {"name": "Kagal", "district": "Kolhapur", "block": "Kagal", "lat": 16.5833, "lon": 74.3167},
    {"name": "Ichalkaranji", "district": "Kolhapur", "block": "Hatkanangale", "lat": 16.6910, "lon": 74.4600},
    {"name": "Sinnar", "district": "Nashik", "block": "Sinnar", "lat": 19.8500, "lon": 74.0000},
]

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [PASS] {label}")
    else:
        _failures.append(label)
        print(f"  [FAIL] {label}  {detail}")


def run() -> None:
    print("=== resolve_location ===")

    exact = resolve_location("Kagal village", GAZETTEER_ROWS)
    check("a close phrase resolves", exact is not None)
    check("resolves to the right village", exact and exact["village"] == "Kagal", str(exact))
    check("carries the district through", exact and exact["district"] == "Kolhapur", str(exact))

    typo = resolve_location("Ichalkarnji", GAZETTEER_ROWS)
    check("a typo'd name still fuzzy-matches", typo and typo["village"] == "Ichalkaranji", str(typo))

    check("no location phrase returns None", resolve_location(None, GAZETTEER_ROWS) is None)
    check(
        "an unrelated phrase returns None rather than guessing",
        resolve_location("some totally unrelated phrase xyz", GAZETTEER_ROWS) is None,
    )
    check("an empty gazetteer returns None", resolve_location("Kagal", []) is None)

    print("\n=== coordinates (regression test for the lat/lon key bug) ===")
    check(
        "a resolved location carries a latitude",
        exact is not None and exact.get("lat") is not None,
        f"got {exact}",
    )
    check(
        "a resolved location carries a longitude",
        exact is not None and exact.get("lon") is not None,
        f"got {exact}",
    )
    check(
        "coordinates are the gazetteer's actual values",
        exact is not None and abs(exact["lat"] - 16.5833) < 0.001 and abs(exact["lon"] - 74.3167) < 0.001,
        f"got {exact}",
    )

    total = 10
    print(f"\n  Result: {total - len(_failures)}/{total} passed, {len(_failures)} failed")
    if _failures:
        sys.exit(1)


if __name__ == "__main__":
    run()
