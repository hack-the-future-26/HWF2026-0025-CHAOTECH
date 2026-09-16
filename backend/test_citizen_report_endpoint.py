import math
import sqlite3
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")

# Ensure backend directory is in path
sys.path.insert(0, str(Path(__file__).resolve().parent))

API_URL = "http://localhost:8001/citizen-report"
DB_PATH = Path(__file__).resolve().parent / "hackathon.db"

TEST_CASES = [
    {
        "text": "sadak bahut kharab hai, call me at 9876543210",
        "should_contain": "9876543210",
        "note": "phone number should be redacted",
    },
    {
        "text": "Mr. Sharma reported this, paani ki samasya hai",
        "should_contain": "Mr. Sharma",
        "note": "name prefix should be redacted",
    },
    {
        "text": "mera naam Ravi Patil hai, sadak kharab hai near Ajra",
        "should_contain": "Ravi Patil",
        "note": "self-stated name should be redacted",
    },
    {
        "text": "माझं नाव रवी आहे, पाणी येत नाही",
        "should_contain": "रवी",
        "note": "self-stated name in Devanagari should be redacted",
    },
    {
        "text": "gaavache naav Bidri aahe, pani nahi aata",
        "should_contain": None,
        "note": "a village's name is not a person's name -- must survive, "
                "because clustering embeds this text",
    },
    {
        "text": "gaon mein bijli nahi hai",
        "should_contain": None,
        "note": "no PII present, should pass through unchanged",
    },
]

PIN_TEST_CASES = [
    {
        "name": "near pin saved",
        "report_lat": "16.1165",
        "report_lon": "74.2105",
        "expect_saved": True,
    },
    {
        "name": "far pin dropped (>5km from village)",
        "report_lat": "17.0",
        "report_lon": "74.2",
        "expect_saved": False,
    },
    {
        "name": "partial pin ignored (lat only)",
        "report_lat": "16.1165",
        "report_lon": None,
        "expect_saved": False,
    },
    {
        "name": "non-numeric pin ignored",
        "report_lat": "abc",
        "report_lon": "def",
        "expect_saved": False,
    },
    {
        "name": "NaN pin ignored",
        "report_lat": "nan",
        "report_lon": "nan",
        "expect_saved": False,
    },
]


class HttpClient:
    """Wrapper that talks to a live server if running, or falls back to TestClient."""
    def __init__(self):
        try:
            r = requests.get("http://localhost:8001/", timeout=0.8)
            if r.status_code == 200:
                self.mode = "live"
                self.base_url = "http://localhost:8001"
                self.session = requests.Session()
                print("[HttpClient] Connected to live server at http://localhost:8001")
                return
        except Exception:
            pass
        from fastapi.testclient import TestClient
        from main import app
        self.mode = "testclient"
        self.client = TestClient(app)
        print("[HttpClient] Using in-process FastAPI TestClient")

    def post(self, path, **kwargs):
        if self.mode == "live":
            url = f"{self.base_url}{path}"
            return self.session.post(url, **kwargs)
        else:
            return self.client.post(path, **kwargs)

    def get(self, path, **kwargs):
        if self.mode == "live":
            url = f"{self.base_url}{path}"
            return self.session.get(url, **kwargs)
        else:
            return self.client.get(path, **kwargs)


def fetch_rows(request_id):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    request_row = con.execute(
        "SELECT * FROM citizen_request WHERE id = ?", (request_id,)
    ).fetchone()
    raw_row = con.execute(
        "SELECT * FROM citizen_request_raw WHERE linked_request_id = ?", (request_id,)
    ).fetchone()
    con.close()
    return dict(request_row) if request_row else None, dict(raw_row) if raw_row else None


def get_auth_token(client: HttpClient) -> str:
    email = "pin_tester@example.com"
    pwd = "password123"
    login_resp = client.post("/auth/login", json={"email": email, "password": pwd})
    if login_resp.status_code == 200:
        return login_resp.json()["token"]

    reg_resp = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": pwd,
            "full_name": "Pin Test Citizen",
            "gender": "female",
            "mobile": "9876543210",
            "address": "Near Bazaar, Ajara",
            "pincode": "416505",
            "district": "Kolhapur",
            "block": "Ajra",
            "village": "Ajara",
        },
    )
    if reg_resp.status_code == 200:
        return reg_resp.json()["token"]
    raise RuntimeError(f"Could not authenticate: {reg_resp.text}")


def run():
    passed = 0
    total = 0
    client = HttpClient()

    # --- 1. Original PII redaction test cases (JSON path) ---
    print("\n=== PII Redaction / JSON path tests ===")
    for case in TEST_CASES:
        print(f"--- {case['note']} ---")
        print(f"input: {case['text']!r}")

        resp = client.post("/citizen-report", json={"text": case["text"]})
        if resp.status_code != 200:
            print(f"FAIL: request failed with HTTP {resp.status_code}: {resp.text}")
            print()
            continue

        data = resp.json()
        request_id = data["id"]
        print(f"saved as id={request_id}, stored raw_text: {data['raw_text']!r}")

        stored_request, stored_raw = fetch_rows(request_id)

        if case["should_contain"] is not None:
            total += 1
            if case["should_contain"] not in stored_request["raw_text"]:
                print(f"PASS: {case['should_contain']!r} was redacted from citizen_request")
                passed += 1
            else:
                print(f"FAIL: {case['should_contain']!r} still present in citizen_request")

            total += 1
            if stored_raw and case["should_contain"] in stored_raw["original_text"]:
                print(f"PASS: {case['should_contain']!r} preserved in citizen_request_raw")
                passed += 1
            else:
                print(f"FAIL: {case['should_contain']!r} missing from citizen_request_raw")
        else:
            total += 1
            if stored_request["raw_text"] == case["text"]:
                print("PASS: clean text passed through unchanged")
                passed += 1
            else:
                print(f"FAIL: clean text was altered -> {stored_request['raw_text']!r}")

        total += 1
        if stored_raw and stored_raw["original_text"] == case["text"]:
            print("PASS: citizen_request_raw holds the exact original text")
            passed += 1
        else:
            print("FAIL: citizen_request_raw does not match the original text")

        # Verify JSON path leaves precise coordinates null
        total += 1
        if stored_request.get("precise_lat") is None and stored_request.get("precise_lon") is None:
            print("PASS: JSON path does not set precise coordinates")
            passed += 1
        else:
            print("FAIL: JSON path unexpectedly set precise coordinates")

        print()

    # --- 2. GPS Pin intake tests ---
    print("\n=== Feature 3: GPS Pin validation and storage tests ===")
    token = get_auth_token(client)
    auth_header = {"Authorization": f"Bearer {token}"}

    for case in PIN_TEST_CASES:
        print(f"--- {case['name']} ---")
        form_data = {
            "district": "Kolhapur",
            "block": "Ajra",
            "village": "Ajara",
            "department": "pwd",
            "category": "road",
            "text": "Sadak par bada khadda hai",
        }
        if case["report_lat"] is not None:
            form_data["report_lat"] = case["report_lat"]
        if case["report_lon"] is not None:
            form_data["report_lon"] = case["report_lon"]

        resp = client.post("/citizen-report", data=form_data, headers=auth_header)
        if resp.status_code != 200:
            print(f"FAIL: form submission failed with HTTP {resp.status_code}: {resp.text}")
            continue

        resp_data = resp.json()
        req_id = resp_data["id"]
        stored_request, _ = fetch_rows(req_id)

        if case["expect_saved"]:
            expected_lat = float(case["report_lat"])
            expected_lon = float(case["report_lon"])

            # 1. Saved in DB
            total += 1
            lat_ok = stored_request["precise_lat"] is not None and math.isclose(
                stored_request["precise_lat"], expected_lat, abs_tol=0.0001
            )
            lon_ok = stored_request["precise_lon"] is not None and math.isclose(
                stored_request["precise_lon"], expected_lon, abs_tol=0.0001
            )
            if lat_ok and lon_ok:
                print(f"PASS: precise coordinates saved in DB: ({stored_request['precise_lat']}, {stored_request['precise_lon']})")
                passed += 1
            else:
                print(f"FAIL: expected ({expected_lat}, {expected_lon}) in DB, got ({stored_request['precise_lat']}, {stored_request['precise_lon']})")

            # 2. Echoed in POST response payload
            total += 1
            if resp_data.get("precise_lat") is not None and resp_data.get("precise_lon") is not None:
                print("PASS: precise coordinates echoed in POST response payload")
                passed += 1
            else:
                print("FAIL: precise coordinates missing from POST response payload")

            # 3. Privacy requirement: GET /citizen-report/{id} must NOT leak precise coordinates
            total += 1
            get_resp = client.get(f"/citizen-report/{req_id}")
            if get_resp.status_code == 200:
                report_data = get_resp.json().get("report", {})
                if "precise_lat" not in report_data and "precise_lon" not in report_data:
                    print("PASS (Privacy): precise_lat/lon excluded from public serialize_citizen_request()")
                    passed += 1
                else:
                    print("FAIL (Privacy): precise_lat/lon leaked in public serialize_citizen_request()!")
            else:
                print(f"FAIL: GET /citizen-report/{req_id} failed with {get_resp.status_code}")
        else:
            total += 1
            if stored_request["precise_lat"] is None and stored_request["precise_lon"] is None:
                print("PASS: invalid/distant/partial pin was discarded (stored as None)")
                passed += 1
            else:
                print(f"FAIL: pin should have been None, but got ({stored_request['precise_lat']}, {stored_request['precise_lon']})")

            total += 1
            if resp_data.get("precise_lat") is None and resp_data.get("precise_lon") is None:
                print("PASS: invalid/distant/partial pin returned as None in POST response")
                passed += 1
            else:
                print("FAIL: invalid pin returned non-None in POST response")

        print()

    fac_passed, fac_total = test_facility_id_intake(client, token)
    passed += fac_passed
    total += fac_total

    va_passed, va_total = test_village_and_asset_endpoints(client)
    passed += va_passed
    total += va_total

    mr_passed, mr_total = test_my_reports_endpoint(client)
    passed += mr_passed
    total += mr_total

    print(f"Result: {passed}/{total} checks passed")
    if passed != total:
        sys.exit(1)


def test_facility_id_intake(client: HttpClient, token: str) -> tuple[int, int]:
    print("=== Feature 5: Facility ID intake validation tests ===")
    passed = 0
    total = 0
    auth_header = {"Authorization": f"Bearer {token}"}

    def assert_check(name: str, cond: bool, fail_msg: str = ""):
        nonlocal passed, total
        total += 1
        if cond:
            print(f"PASS: {name}")
            passed += 1
        else:
            print(f"FAIL: {name} - {fail_msg}")

    # Case 1: Valid pick saved (facility 343 is a school near Ajara, education)
    form_valid = {
        "district": "Kolhapur",
        "block": "Ajra",
        "village": "Ajara",
        "department": "zp-education",
        "category": "education",
        "text": "School roof is leaking badly",
        "facility_id": "343",
    }
    resp = client.post("/citizen-report", data=form_valid, headers=auth_header)
    assert_check("valid facility: POST succeeds", resp.status_code == 200, f"got {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        assert_check("valid facility: facility_id echoed in POST response", data.get("facility_id") == 343, f"got {data.get('facility_id')}")
        stored_req, _ = fetch_rows(data["id"])
        assert_check("valid facility: facility_id saved in DB", stored_req and stored_req.get("facility_id") == 343, f"got {stored_req.get('facility_id') if stored_req else None}")
        # Also check GET /citizen-report/{id} does NOT leak facility_id in public view
        get_resp = client.get(f"/citizen-report/{data['id']}")
        if get_resp.status_code == 200:
            rep = get_resp.json().get("report", {})
            assert_check("privacy: facility_id not leaked in public serialize_citizen_request()", "facility_id" not in rep)

    # Case 2: Wrong category dropped (9507 is a health center near Ajara, submitted for education)
    form_wrong_cat = {
        "district": "Kolhapur",
        "block": "Ajra",
        "village": "Ajara",
        "department": "zp-education",
        "category": "education",
        "text": "School roof is leaking badly",
        "facility_id": "9507",
    }
    resp = client.post("/citizen-report", data=form_wrong_cat, headers=auth_header)
    assert_check("wrong category: POST succeeds", resp.status_code == 200, f"got {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        assert_check("wrong category: facility_id dropped (None in response)", data.get("facility_id") is None, f"got {data.get('facility_id')}")
        stored_req, _ = fetch_rows(data["id"])
        assert_check("wrong category: facility_id dropped (None in DB)", stored_req and stored_req.get("facility_id") is None, f"got {stored_req.get('facility_id') if stored_req else None}")

    # Case 3: Distant facility dropped (facility 1 is ~40km away from Ajara)
    form_distant = {
        "district": "Kolhapur",
        "block": "Ajra",
        "village": "Ajara",
        "department": "zp-education",
        "category": "education",
        "text": "School roof is leaking badly",
        "facility_id": "1",
    }
    resp = client.post("/citizen-report", data=form_distant, headers=auth_header)
    assert_check("distant facility: POST succeeds", resp.status_code == 200, f"got {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        assert_check("distant facility: facility_id dropped (None in response)", data.get("facility_id") is None, f"got {data.get('facility_id')}")
        stored_req, _ = fetch_rows(data["id"])
        assert_check("distant facility: facility_id dropped (None in DB)", stored_req and stored_req.get("facility_id") is None, f"got {stored_req.get('facility_id') if stored_req else None}")

    # Case 4: Nonexistent facility dropped (99999999)
    form_nonexistent = {
        "district": "Kolhapur",
        "block": "Ajra",
        "village": "Ajara",
        "department": "zp-education",
        "category": "education",
        "text": "School roof is leaking badly",
        "facility_id": "99999999",
    }
    resp = client.post("/citizen-report", data=form_nonexistent, headers=auth_header)
    assert_check("nonexistent facility: POST succeeds", resp.status_code == 200, f"got {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        assert_check("nonexistent facility: facility_id dropped (None in response)", data.get("facility_id") is None, f"got {data.get('facility_id')}")
        stored_req, _ = fetch_rows(data["id"])
        assert_check("nonexistent facility: facility_id dropped (None in DB)", stored_req and stored_req.get("facility_id") is None, f"got {stored_req.get('facility_id') if stored_req else None}")

    # Case 5: Non-numeric facility dropped ("abc")
    form_non_num = {
        "district": "Kolhapur",
        "block": "Ajra",
        "village": "Ajara",
        "department": "zp-education",
        "category": "education",
        "text": "School roof is leaking badly",
        "facility_id": "abc",
    }
    resp = client.post("/citizen-report", data=form_non_num, headers=auth_header)
    assert_check("non-numeric facility: POST succeeds", resp.status_code == 200, f"got {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        assert_check("non-numeric facility: facility_id dropped (None in response)", data.get("facility_id") is None, f"got {data.get('facility_id')}")
        stored_req, _ = fetch_rows(data["id"])
        assert_check("non-numeric facility: facility_id dropped (None in DB)", stored_req and stored_req.get("facility_id") is None, f"got {stored_req.get('facility_id') if stored_req else None}")

    # Case 6: JSON path unaffected (POST json with facility_id: 343 ignores facility_id)
    resp_json = client.post("/citizen-report", json={"text": "School problem in Ajara", "facility_id": 343})
    assert_check("JSON path: POST succeeds", resp_json.status_code == 200, f"got {resp_json.status_code}")
    if resp_json.status_code == 200:
        data = resp_json.json()
        assert_check("JSON path: facility_id is None in response", data.get("facility_id") is None, f"got {data.get('facility_id')}")
        stored_req, _ = fetch_rows(data["id"])
        assert_check("JSON path: facility_id is None in DB", stored_req and stored_req.get("facility_id") is None, f"got {stored_req.get('facility_id') if stored_req else None}")

    print()
    return passed, total


def test_village_and_asset_endpoints(client) -> tuple[int, int]:
    print("=== Village & Asset Priority endpoint tests ===")
    passed = 0
    total = 0

    def assert_check(name: str, cond: bool, fail_msg: str = ""):
        nonlocal passed, total
        total += 1
        if cond:
            print(f"PASS: {name}")
            passed += 1
        else:
            print(f"FAIL: {name} - {fail_msg}")

    # 1. GET /villages?district=Kolhapur
    res = client.get("/villages?district=Kolhapur")
    assert_check("GET /villages returns 200", res.status_code == 200, f"got {res.status_code}")
    villages = res.json() if res.status_code == 200 else []
    assert_check("GET /villages returns non-empty list", len(villages) > 0)
    if villages:
        v0 = villages[0]
        expected_keys = {
            "gazetteer_id", "name", "block", "district", "lat", "lon",
            "priority_score", "rank_in_district", "report_count",
            "counts_by_category", "asset_count", "top_asset", "is_demo"
        }
        assert_check("GET /villages shape matches specification", expected_keys.issubset(v0.keys()))

        # 2. GET /villages/{gazetteer_id}
        gid = v0["gazetteer_id"]
        v_res = client.get(f"/villages/{gid}")
        assert_check(f"GET /villages/{gid} returns 200", v_res.status_code == 200, f"got {v_res.status_code}")
        v_data = v_res.json() if v_res.status_code == 200 else {}
        detail_keys = {"assets", "top_asset", "reports"}
        assert_check("GET /villages/{id} contains assets, top_asset, reports", detail_keys.issubset(v_data.keys()))
        if v_data.get("top_asset"):
            assert_check("top_asset contains breakdown and evidence", "breakdown" in v_data["top_asset"] and "evidence" in v_data["top_asset"])

        # Check no precise_lat in reports
        reports = v_data.get("reports", [])
        no_pin_in_reports = all("precise_lat" not in r and "precise_lon" not in r for r in reports)
        assert_check("no precise_lat/lon leaked in village reports", no_pin_in_reports)

        # 3. GET /assets/{asset_id}
        if v_data.get("assets"):
            a0 = v_data["assets"][0]
            aid = a0["id"]
            a_res = client.get(f"/assets/{aid}")
            assert_check(f"GET /assets/{aid} returns 200", a_res.status_code == 200, f"got {a_res.status_code}")
            a_data = a_res.json() if a_res.status_code == 200 else {}
            asset_expected_keys = {
                "id", "asset_type", "name", "name_basis", "facility_id", "source", "external_id",
                "lat", "lon", "location_basis", "primary_gazetteer_id", "village", "block", "district",
                "villages_served", "report_count", "distinct_reporters", "priority_score",
                "rank_in_village", "breakdown", "evidence", "candidates", "is_demo", "created_at", "reports"
            }
            assert_check("GET /assets/{id} shape matches specification", asset_expected_keys.issubset(a_data.keys()))
            a_reports = a_data.get("reports", [])
            assert_check("no precise_lat/lon leaked in asset reports", all("precise_lat" not in r and "precise_lon" not in r for r in a_reports))

    # 4. Privacy: Pin-based asset coordinates are rounded to at most 3 decimals
    res_all_v = client.get("/villages?district=Kolhapur")
    found_pin_asset = False
    for v in (res_all_v.json() if res_all_v.status_code == 200 else []):
        v_full = client.get(f"/villages/{v['gazetteer_id']}").json()
        for a in v_full.get("assets", []):
            if a.get("location_basis") != "register_coordinates" and a.get("lat") is not None:
                lat, lon = a["lat"], a["lon"]
                lat_rounded = round(lat, 3) == lat
                lon_rounded = round(lon, 3) == lon
                assert_check(f"pin-based asset coordinates rounded to <=3 decimals", lat_rounded and lon_rounded, f"got ({lat}, {lon})")
                found_pin_asset = True
                break
        if found_pin_asset:
            break
    if not found_pin_asset:
        assert_check("found at least one pin-based asset to verify coordinate rounding", False)

    # 5. Unknown IDs return 404
    res_404_v = client.get("/villages/99999999")
    assert_check("unknown village ID returns 404", res_404_v.status_code == 404, f"got {res_404_v.status_code}")
    res_404_a = client.get("/assets/99999999")
    assert_check("unknown asset ID returns 404", res_404_a.status_code == 404, f"got {res_404_a.status_code}")

    print()
    return passed, total


def test_my_reports_endpoint(client: HttpClient) -> tuple[int, int]:
    """
    Real bug, confirmed by reading routes_auth.py and fixed 2026-09-16:
    get_my_reports() referenced DemandCluster.title, PriorityScore.rank and
    PriorityScore.final_score -- none of which exist -- so GET
    /citizen/my-reports raised AttributeError for any signed-in citizen with
    a clustered report. Exercises the exact crash scenario against the real
    live database rather than a mock, using an existing clustered report
    (this repo's own README/test convention: run against the populated
    hackathon.db, back it up first).
    """
    print("=== GET /citizen/my-reports tests ===")
    passed = 0
    total = 0

    def assert_check(name: str, cond: bool, fail_msg: str = ""):
        nonlocal passed, total
        total += 1
        if cond:
            print(f"PASS: {name}")
            passed += 1
        else:
            print(f"FAIL: {name} - {fail_msg}")

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT user_id FROM citizen_request "
        "WHERE user_id IS NOT NULL AND cluster_id IS NOT NULL LIMIT 1"
    ).fetchone()
    if row is None:
        con.close()
        assert_check(
            "found a real clustered report with a signed-in user to test against",
            False,
            "no such row in the live database -- run intelligence.recompute first",
        )
        return passed, total

    import secrets
    token = secrets.token_urlsafe(32)
    con.execute(
        "INSERT INTO user_session (token, user_id, created_at) VALUES (?, ?, datetime('now'))",
        (token, row["user_id"]),
    )
    con.commit()
    con.close()

    try:
        res = client.get("/citizen/my-reports", headers={"Authorization": f"Bearer {token}"})
        assert_check(
            "GET /citizen/my-reports does not crash for a signed-in citizen with a clustered report",
            res.status_code == 200,
            f"got {res.status_code}: {res.text[:300]}",
        )
        data = res.json() if res.status_code == 200 else {}
        reports = data.get("reports", [])
        clustered = [r for r in reports if r.get("status") == "clustered"]
        assert_check("at least one report comes back clustered", len(clustered) > 0)
        if clustered:
            cluster_info = clustered[0].get("cluster") or {}
            assert_check(
                "cluster info uses real fields (issue_category, priority_rank, priority_score)",
                {"cluster_id", "issue_category", "priority_rank", "priority_score"} <= cluster_info.keys(),
                f"got keys {list(cluster_info.keys())}",
            )
            assert_check(
                "priority_rank is a real positive integer, not null",
                isinstance(cluster_info.get("priority_rank"), int) and cluster_info["priority_rank"] > 0,
                f"got {cluster_info.get('priority_rank')!r}",
            )
    finally:
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM user_session WHERE token = ?", (token,))
        con.commit()
        con.close()

    print()
    return passed, total


if __name__ == "__main__":
    run()
