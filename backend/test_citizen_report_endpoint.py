import sqlite3
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8")

API_URL = "http://localhost:8001/citizen-report"
DB_PATH = "hackathon.db"

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


def run():
    passed = 0
    total = 0

    for case in TEST_CASES:
        print(f"--- {case['note']} ---")
        print(f"input: {case['text']!r}")

        resp = requests.post(API_URL, json={"text": case["text"]})
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

        print()

    print(f"{passed}/{total} checks passed")


if __name__ == "__main__":
    run()
