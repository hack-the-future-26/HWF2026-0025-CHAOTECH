"""
Tests for backend/assign_synthetic_assets.py
"""

import math
import random
import unittest
from pathlib import Path
import sys

_BACKEND_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from assign_synthetic_assets import offset_coords, assign_synthetic_assets, undo_synthetic_assets
from intelligence.clustering import haversine_km


class MockReport:
    def __init__(self, id, district, block, village, issue_category, lat, lon, is_synthetic=1):
        self.id = id
        self.district = district
        self.block = block
        self.village = village
        self.issue_category = issue_category
        self.latitude = lat
        self.longitude = lon
        self.is_synthetic = is_synthetic
        self.precise_lat = None
        self.precise_lon = None
        self.facility_id = None
        self.pin_source = None


class MockFacility:
    def __init__(self, id, name, category, lat, lon):
        self.id = id
        self.name = name
        self.category = category
        self.latitude = lat
        self.longitude = lon


class MockQuery:
    def __init__(self, items):
        self.items = list(items)

    def filter(self, *args, **kwargs):
        # Very simple in-memory filtering for test mocks
        return self

    def all(self):
        return self.items


class MockSession:
    def __init__(self, reports, facilities):
        self.reports = reports
        self.facilities = facilities
        self.committed = False

    def query(self, model):
        from models import CitizenRequest, PublicFacility
        if model == CitizenRequest:
            # only synthetic without precise_lat
            matched = [
                r for r in self.reports
                if getattr(r, "is_synthetic", 0) == 1 and getattr(r, "precise_lat", None) is None
            ]
            return MockQuery(matched)
        elif model == PublicFacility:
            matched = [f for f in self.facilities if f.latitude is not None and f.longitude is not None]
            return MockQuery(matched)
        return MockQuery([])

    def commit(self):
        self.committed = True


class TestAssignSyntheticAssets(unittest.TestCase):
    def test_offset_coords_bounds(self):
        lat0, lon0 = 16.5, 74.5
        for dist_m in [10.0, 30.0, 80.0, 500.0, 1000.0]:
            for angle in [0.0, math.pi / 4, math.pi / 2, math.pi, 1.5 * math.pi]:
                lat1, lon1 = offset_coords(lat0, lon0, dist_m, angle)
                actual_km = haversine_km(lat0, lon0, lat1, lon1)
                expected_km = dist_m / 1000.0
                self.assertAlmostEqual(actual_km, expected_km, delta=0.015)

    def test_determinism(self):
        reports1 = [
            MockReport(101, "Kolhapur", "Ajra", "Ajara", "education", 16.116, 74.210),
            MockReport(102, "Kolhapur", "Ajra", "Ajara", "health", 16.116, 74.210),
            MockReport(103, "Kolhapur", "Ajra", "Ajara", "road", 16.116, 74.210),
        ]
        reports2 = [
            MockReport(101, "Kolhapur", "Ajra", "Ajara", "education", 16.116, 74.210),
            MockReport(102, "Kolhapur", "Ajra", "Ajara", "health", 16.116, 74.210),
            MockReport(103, "Kolhapur", "Ajra", "Ajara", "road", 16.116, 74.210),
        ]
        facilities = [
            MockFacility(1, "School A", "education", 16.117, 74.211),
            MockFacility(2, "PHC B", "health", 16.120, 74.215),
        ]

        db1 = MockSession(reports1, facilities)
        db2 = MockSession(reports2, facilities)

        counts1 = assign_synthetic_assets(db1, dry_run=False)
        counts2 = assign_synthetic_assets(db2, dry_run=False)

        self.assertEqual(counts1, counts2)
        for r1, r2 in zip(reports1, reports2):
            self.assertEqual(r1.precise_lat, r2.precise_lat)
            self.assertEqual(r1.precise_lon, r2.precise_lon)
            self.assertEqual(r1.facility_id, r2.facility_id)
            self.assertEqual(r1.pin_source, r2.pin_source)

    def test_real_citizen_reports_never_touched(self):
        real_report = MockReport(999, "Kolhapur", "Ajra", "Ajara", "road", 16.116, 74.210, is_synthetic=0)
        facilities = [MockFacility(1, "School A", "education", 16.117, 74.211)]
        db = MockSession([real_report], facilities)

        counts = assign_synthetic_assets(db, dry_run=False)
        self.assertEqual(counts["total_synthetic"], 0)
        self.assertIsNone(real_report.precise_lat)
        self.assertIsNone(real_report.precise_lon)
        self.assertIsNone(real_report.facility_id)
        self.assertIsNone(real_report.pin_source)

    def test_dry_run_does_not_commit(self):
        rep = MockReport(101, "Kolhapur", "Ajra", "Ajara", "education", 16.116, 74.210)
        fac = MockFacility(1, "School A", "education", 16.117, 74.211)
        db = MockSession([rep], [fac])

        counts = assign_synthetic_assets(db, dry_run=True)
        self.assertEqual(counts["education_assigned"], 1)
        self.assertFalse(db.committed)
        self.assertIsNone(rep.precise_lat)
        self.assertIsNone(rep.pin_source)


if __name__ == "__main__":
    unittest.main()

