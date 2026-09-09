from rapidfuzz import process, fuzz


def resolve_location(
    location_phrase: str | None,
    gazetteer_rows: list[dict],
    threshold: int = 75,
) -> dict | None:
    """
    Fuzzy-match *location_phrase* against the gazetteer and return a
    location_resolved dict or None.

    Args:
        location_phrase:  Raw extracted location string from location.py.
        gazetteer_rows:   List of dicts with at least the keys:
                            name, district, block, lat, lon
        threshold:        Minimum similarity score (0-100) to accept a match.

    Returns:
        dict with keys district, block, village, lat, lon, confidence
        or None when no confident match is found.
    """
    if not location_phrase or not location_phrase.strip():
        return None

    if not gazetteer_rows:
        return None

    # Build name list, guarding against rows that lack a 'name' field
    names = [
        row.get("name", "") or ""
        for row in gazetteer_rows
    ]

    # Filter out empty names before matching
    valid_pairs = [
        (name, row)
        for name, row in zip(names, gazetteer_rows)
        if name.strip()
    ]

    if not valid_pairs:
        return None

    valid_names, valid_rows = zip(*valid_pairs)

    result = process.extractOne(
        location_phrase,
        valid_names,
        scorer=fuzz.WRatio,
    )

    if result is None:
        return None

    match, score, idx = result

    if score < threshold:
        # Return a low-confidence flag rather than silently returning None
        return None

    row = valid_rows[idx]

    return {
        "district": row.get("district", ""),
        "block":    row.get("block", ""),
        "village":  row.get("name", match),
        "lat":      row.get("lat"),
        "lon":      row.get("lon"),
        "confidence": round(score / 100, 2),
    }