"""
gazetteer_mock.py

Standalone mock gazetteer for testing P1 without a live Postgres connection.

Districts: Kolhapur + Pune (Maharashtra)
This covers the agreed demo scope (2 districts, Marathi as 3rd language).

P2 will replace this with real rows from the gazetteer table.
Schema matches what geocode.resolve_location() expects:
    name, district, block, lat, lon
"""

MOCK_GAZETTEER: list[dict] = [
    # ── Kolhapur District ────────────────────────────────────────────────────
    {"name": "Karvir",       "district": "Kolhapur", "block": "Karvir",     "lat": 16.6912, "lon": 74.2432},
    {"name": "Hatkanangale", "district": "Kolhapur", "block": "Hatkanangale","lat": 16.8153, "lon": 74.5842},
    {"name": "Shirol",       "district": "Kolhapur", "block": "Shirol",      "lat": 16.7344, "lon": 74.5617},
    {"name": "Kagal",        "district": "Kolhapur", "block": "Kagal",       "lat": 16.5753, "lon": 74.3183},
    {"name": "Gadhinglaj",   "district": "Kolhapur", "block": "Gadhinglaj",  "lat": 16.2232, "lon": 74.3509},
    {"name": "Chandgad",     "district": "Kolhapur", "block": "Chandgad",    "lat": 15.9434, "lon": 74.2792},
    {"name": "Panhala",      "district": "Kolhapur", "block": "Panhala",     "lat": 16.8120, "lon": 74.1131},
    {"name": "Radhanagari",  "district": "Kolhapur", "block": "Radhanagari", "lat": 16.4062, "lon": 73.9799},
    {"name": "Bhudargad",    "district": "Kolhapur", "block": "Bhudargad",   "lat": 16.5253, "lon": 73.9672},
    {"name": "Ajara",        "district": "Kolhapur", "block": "Ajara",       "lat": 15.8939, "lon": 74.2079},
    {"name": "Bavada",       "district": "Kolhapur", "block": "Karvir",      "lat": 16.6782, "lon": 74.2612},
    {"name": "Kasaba Bawada","district": "Kolhapur", "block": "Karvir",      "lat": 16.6891, "lon": 74.2394},
    {"name": "Nagaon",       "district": "Kolhapur", "block": "Hatkanangale","lat": 16.8411, "lon": 74.5631},
    {"name": "Nerli",        "district": "Kolhapur", "block": "Shirol",      "lat": 16.7219, "lon": 74.5942},
    {"name": "Kowad",        "district": "Kolhapur", "block": "Kagal",       "lat": 16.5523, "lon": 74.3802},

    # ── Pune District ────────────────────────────────────────────────────────
    {"name": "Haveli",       "district": "Pune", "block": "Haveli",      "lat": 18.5204, "lon": 73.8567},
    {"name": "Maval",        "district": "Pune", "block": "Maval",       "lat": 18.7417, "lon": 73.5247},
    {"name": "Mulshi",       "district": "Pune", "block": "Mulshi",      "lat": 18.5393, "lon": 73.5309},
    {"name": "Bhor",         "district": "Pune", "block": "Bhor",        "lat": 18.1569, "lon": 73.8417},
    {"name": "Velhe",        "district": "Pune", "block": "Velhe",       "lat": 18.2453, "lon": 73.6217},
    {"name": "Purandar",     "district": "Pune", "block": "Purandar",    "lat": 18.2717, "lon": 73.9783},
    {"name": "Baramati",     "district": "Pune", "block": "Baramati",    "lat": 18.1518, "lon": 74.5797},
    {"name": "Indapur",      "district": "Pune", "block": "Indapur",     "lat": 18.1147, "lon": 75.0309},
    {"name": "Daund",        "district": "Pune", "block": "Daund",       "lat": 18.4612, "lon": 74.5858},
    {"name": "Shirur",       "district": "Pune", "block": "Shirur",      "lat": 18.8274, "lon": 74.3806},
    {"name": "Junnar",       "district": "Pune", "block": "Junnar",      "lat": 19.2053, "lon": 73.8777},
    {"name": "Ambegaon",     "district": "Pune", "block": "Ambegaon",    "lat": 19.1581, "lon": 73.7381},
    {"name": "Khed",         "district": "Pune", "block": "Khed",        "lat": 18.8526, "lon": 73.9959},
    {"name": "Khalapur",     "district": "Pune", "block": "Maval",       "lat": 18.8232, "lon": 73.3432},
    {"name": "Wai",          "district": "Pune", "block": "Bhor",        "lat": 17.9521, "lon": 73.9007},
]
