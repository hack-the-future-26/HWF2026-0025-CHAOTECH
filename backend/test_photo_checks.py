"""
Workstream C tests: photo checks and fake-complaint defence (C1-C7).

    cd backend && python test_photo_checks.py

Runs against a throw-away SQLite database (never backend/hackathon.db) and
cleans up the upload files it creates. The model tests need
models/pothole_best.pt; without it they are reported as skipped, not passed.
"""

from __future__ import annotations

import io
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
_TMP_DB = Path(tempfile.gettempdir()) / "awaaziq_test_photo_checks.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(_TMP_DB) + suffix).unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import numpy as np  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

import photo_checks as pc  # noqa: E402

PHOTOS = HERE / "test_data" / "photos"
_passed, _failed, _skipped = 0, [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}  {detail}")


def skip(name: str, why: str) -> None:
    _skipped.append(name)
    print(f"  SKIP  {name}  ({why})")


def jpeg_bytes(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def scene(seed: int = 1, size=(640, 480)) -> Image.Image:
    """A textured, non-periodic synthetic 'road' scene."""
    rng = np.random.default_rng(seed)
    h, w = size[1], size[0]
    base = rng.normal(110, 25, (h // 8, w // 8, 1)).repeat(8, 0).repeat(8, 1)
    grain = rng.normal(0, 18, (h, w, 1))
    img = np.clip(np.concatenate([base + grain, base + grain * 0.9, base * 0.95 + grain], axis=2), 0, 255)
    return Image.fromarray(img.astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))


def screen_recapture(img: Image.Image, angle_deg: float = 3.0, pitch: float = 4.3) -> Image.Image:
    """Show `img` on an RGB-stripe display and photograph it (see models/README.md)."""
    from scipy import ndimage

    a = np.asarray(img, dtype=np.float32) / 255
    h, w, _ = a.shape
    s = 4
    up = np.repeat(np.repeat(a, s, 0), s, 1)
    mask = np.zeros((s, s, 3), np.float32)
    for c in range(3):
        mask[: s - 1, c, c] = 1.0
    disp = ndimage.gaussian_filter(up * np.tile(mask, (h, w, 1)), (1.0, 1.0, 0))
    ang = math.radians(angle_deg)
    oh, ow = int(h * s / pitch), int(w * s / pitch)
    yy, xx = np.mgrid[0:oh, 0:ow].astype(np.float32)
    cy, cx = oh / 2, ow / 2
    sy = ((yy - cy) * math.cos(ang) - (xx - cx) * math.sin(ang)) * pitch + h * s / 2
    sx = ((yy - cy) * math.sin(ang) + (xx - cx) * math.cos(ang)) * pitch + w * s / 2
    out = np.stack([ndimage.map_coordinates(disp[..., c], [sy, sx], order=1, mode="reflect") for c in range(3)], -1)
    out = out / max(1e-6, float(np.percentile(out, 99.5)))
    return Image.open(io.BytesIO(jpeg_bytes(Image.fromarray(np.clip(out * 255, 0, 255).astype(np.uint8))))).convert("RGB")


# ----------------------------------------------------------------- C1 --

def test_c1_capture() -> None:
    now = datetime.now(timezone.utc)
    live = {"method": "live_camera", "captured_at": (now - timedelta(minutes=2)).isoformat(),
            "lat": 16.7010, "lon": 74.2410, "accuracy_m": 12, "burst_count": 5}
    r = pc.check_capture(live, {}, village_lat=16.7050, village_lon=74.2433, received_at=now)
    check("C1: live capture near the village raises no flag", r["flags"] == [], str(r["flags"]))
    check("C1: distance to village is measured", r["distance_to_village_km"] is not None and r["distance_to_village_km"] < 1)

    far = dict(live, lat=16.90, lon=74.40)
    r = pc.check_capture(far, {}, village_lat=16.7050, village_lon=74.2433, received_at=now)
    check("C1: capture >5 km from village is flagged", "captured_far_from_village" in r["flags"])

    r = pc.check_capture(None, {}, village_lat=16.7, village_lon=74.2, received_at=now)
    check("C1: a plain file upload is labelled not live", "not_live_capture" in r["flags"] and "no_capture_gps" in r["flags"])

    stale = dict(live, captured_at=(now - timedelta(hours=5)).isoformat())
    r = pc.check_capture(stale, {}, village_lat=16.7050, village_lon=74.2433, received_at=now)
    check("C1: capture hours before upload is a timing mismatch", "capture_time_mismatch" in r["flags"])

    imprecise = dict(far, accuracy_m=5000)
    r = pc.check_capture(imprecise, {}, village_lat=16.7050, village_lon=74.2433, received_at=now)
    check("C1: a 5 km-accuracy GPS fix is not trusted for the distance flag", "captured_far_from_village" not in r["flags"])

    r = pc.check_capture(None, {"software": "Adobe Photoshop 25.0"}, village_lat=None, village_lon=None)
    check("C1/C4: editing software in EXIF is flagged", "editing_software" in r["flags"])

    view = pc.public_view({"capture": dict(live), "hashes": {"phash": "ab"}, "summary": {}})
    check("C1: public view never carries capture coordinates or hashes",
          "lat" not in view["capture"] and "lon" not in view["capture"] and "hashes" not in view)


# ----------------------------------------------------------------- C3 --

def test_c3_duplicates() -> None:
    if pc.imagehash is None:
        return skip("C3 duplicate photos", "imagehash not installed")
    original = scene(3)
    resized = original.resize((400, 300))
    recompressed = Image.open(io.BytesIO(jpeg_bytes(original, 55))).convert("RGB")
    other = scene(99)

    h0 = pc.photo_hashes(original)
    stored = [{"attachment_id": 1, "request_id": 10, "village": "Ajara", "user_id": 5, **h0}]
    check("C3: resized copy matches", bool(pc.find_duplicates(pc.photo_hashes(resized), stored)))
    check("C3: recompressed copy matches", bool(pc.find_duplicates(pc.photo_hashes(recompressed), stored)))
    check("C3: a different scene does not match", not pc.find_duplicates(pc.photo_hashes(other), stored))

    m = pc.find_duplicates(pc.photo_hashes(resized), stored)
    flags, summary = pc.classify_duplicates(m, village="Bidri", user_id=6)
    check("C3: same photo on another village/account -> reused_photo", flags == ["reused_photo"] and summary["request_id"] == 10)
    flags, _ = pc.classify_duplicates(m, village="Ajara", user_id=5)
    check("C3: same reporter resubmitting is not called reuse", flags == ["same_photo_resubmitted"])


# ----------------------------------------------------------------- C4 --

def test_c4_error_level() -> None:
    img = Image.open(io.BytesIO(jpeg_bytes(scene(5), 90))).convert("RGB")
    r = pc.error_level_analysis(img)
    check("C4: ELA returns a 0-1 score", r["score"] is not None and 0 <= r["score"] <= 1)
    heat = pc.ela_heatmap(img)
    check("C4: ELA heat map has the photo's size", heat.size == img.size)
    s = pc.summarise({"flags": []}, [], {"score": 0.99}, {"score": 0}, {}, {"available": False}, "road")
    check("C4: the advisory ELA hint alone never lowers authenticity", s["authenticity"] == 1.0 and not s["review_flags"])


# ----------------------------------------------------------------- C5 --

def test_c5_model() -> None:
    if not pc.MODEL_PATH.is_file():
        return skip("C5 pothole model", f"{pc.MODEL_PATH} not present")
    pothole = pc.detect_defects(pc.open_image((PHOTOS / "pothole.jpg").read_bytes()))
    clean = pc.detect_defects(pc.open_image((PHOTOS / "clean_road.jpg").read_bytes()))
    check("C5: model loads and runs", pothole["available"] and clean["available"], str(pc.model_info()))
    check("C5: real pothole photo -> pothole detected", pothole["defect_seen"] and pothole["pothole_confidence"] >= pc.DEFECT_CONF_THRESHOLD,
          f"conf={pothole.get('pothole_confidence')}")
    check("C5: clean national highway -> no damage detected", not clean["defect_seen"], f"dets={clean.get('detections')}")
    check("C5: boxes are normalised 0-1", all(0 <= v <= 1 for d in pothole["detections"] for v in d["box"]))
    drawn = pc.draw_detections(pc.open_image((PHOTOS / "pothole.jpg").read_bytes()), pothole["detections"])
    check("C5: annotated image is produced", drawn.size[0] > 0)
    tiny = {"label": "pothole", "confidence": 0.9, "box": [0.5, 0.5, 0.52, 0.52], "too_small": True}
    check("C5: tiny far-away boxes are not counted", pc._union_area([]) == 0.0 and tiny["too_small"])


# ----------------------------------------------------------------- C6 --

def test_c6_damage_grade() -> None:
    no_photo = {"available": False}
    g = pc.grade_damage("gavajavalcha पूल कोसळला आहे", no_photo)
    check("C6: Marathi 'bridge collapsed' -> bridge / collapse", g["structure"] == "bridge" and g["grade"] == "collapse" and g["emergency_candidate"])
    check("C6: collapse from wording is never 'confirmed by photo'", g["collapse_confirmed_by_photo"] is False)

    g = pc.grade_damage("school building ki deewar mein darar aa gayi", no_photo)
    check("C6: Hinglish wall crack -> building / crack", g["structure"] == "building" and g["grade"] == "crack")

    cracked = {"available": True, "crack_confidence": 0.7, "pothole_confidence": 0.0, "cracks": 1, "potholes": 0, "damage_area_pct": 4}
    g = pc.grade_damage("bridge is damaged", cracked)
    check("C6: photo + wording agree -> higher confidence than wording alone",
          g["confidence"] > pc.grade_damage("bridge is damaged", no_photo)["confidence"])
    check("C6: grade takes the worse of photo (crack) and wording (partial)", g["grade"] == "partial")

    g = pc.grade_damage("rasta madhe khadde", {"available": True, "crack_confidence": 0.0, "pothole_confidence": 0.8,
                                                 "cracks": 0, "potholes": 1, "damage_area_pct": 10})
    check("C6: potholes on a road are not an emergency", g["emergency_candidate"] is False)
    check("C6: grade values follow crack < partial < collapse",
          pc.GRADE_VALUE["crack"] < pc.GRADE_VALUE["partial"] < pc.GRADE_VALUE["collapse"] == 1.0)


# ----------------------------------------------------------------- C7 --

def test_c7_screen_replay() -> None:
    genuine = Image.open(io.BytesIO(jpeg_bytes(scene(7, (800, 600)), 90))).convert("RGB")
    screen = screen_recapture(genuine)
    g = pc.moire_score(genuine)["score"]
    s = pc.moire_score(screen)["score"]
    check("C7: genuine scene has no moire", g < pc.MOIRE_FLAG_SCORE, f"score={g}")
    check("C7: photo of a screen shows moire", s >= pc.MOIRE_FLAG_SCORE, f"score={s}")

    if (PHOTOS / "pothole.jpg").is_file():
        real = pc.moire_score(pc.open_image((PHOTOS / "pothole.jpg").read_bytes()))["score"]
        check("C7: real road photo is not called a screen", real < pc.MOIRE_FLAG_SCORE, f"score={real}")

    frame = scene(11)
    static = pc.burst_liveness([frame, frame.copy(), frame.copy()])
    rng = np.random.default_rng(0)
    noisy = [Image.fromarray(np.clip(np.asarray(frame, np.float32) + rng.normal(0, 3, (480, 640, 3)), 0, 255).astype(np.uint8))
             for _ in range(3)]
    live = pc.burst_liveness(noisy)
    check("C7: identical burst frames are static", static["static"] is True)
    check("C7: frames with sensor noise are live", live["static"] is False)
    check("C7: no burst -> not judged", pc.burst_liveness([frame])["static"] is None)


# ----------------------------------------------------------- combining --

def test_summary_never_rejects() -> None:
    worst = pc.summarise(
        {"flags": ["not_live_capture", "captured_far_from_village", "capture_time_mismatch", "editing_software"]},
        ["reused_photo"], {"score": 1.0}, {"score": 1.0}, {"static": True}, {"available": False}, "road")
    check("combine: many flags floor authenticity, never zero", worst["authenticity"] == pc.AUTHENTICITY_FLOOR)
    check("combine: strong flags go to review", worst["verdict"] == "needs_review" and "reused_photo" in worst["review_flags"])

    adjusted = pc.confidence_adjustment(0.8, [worst])
    check("combine: confidence damped but never below half", 0.4 <= adjusted < 0.8, str(adjusted))

    good = pc.summarise({"flags": []}, [], {"score": 0.1}, {"score": 0.0}, {"static": False},
                        {"available": True, "defect_seen": True, "pothole_confidence": 0.8, "crack_confidence": 0}, "road")
    check("combine: genuine live photo showing a pothole is verified", good["verdict"] == "verified")
    check("combine: verified supporting photo lifts confidence", pc.confidence_adjustment(0.6, [good]) > 0.6)
    check("combine: no photos -> confidence unchanged", pc.confidence_adjustment(0.7, []) == 0.7)


# ------------------------------------------------------------ endpoints --

def test_endpoints() -> None:
    from fastapi.testclient import TestClient

    import main
    from database import SessionLocal
    from models import CitizenRequest, Gazetteer, PhotoCheck, ReportAttachment
    import routes_photo_checks

    created_files: list[Path] = []
    with TestClient(main.app) as client:
        db = SessionLocal()
        db.add(Gazetteer(name="Ajara", admin_level="village", district="Kolhapur", block="Ajra",
                         population=18000, latitude=16.1167, longitude=74.2167))
        db.commit()
        db.close()

        r = client.post("/auth/register", json={
            "email": "photo_tester@example.com", "password": "password123", "full_name": "Photo Tester",
            "gender": "female", "mobile": "9876543211", "address": "Near Bazaar", "pincode": "416505",
            "district": "Kolhapur", "block": "Ajra", "village": "Ajara"})
        check("API: test citizen registered", r.status_code == 200, r.text[:200])
        token = r.json().get("token") if r.status_code == 200 else None

        m = client.get("/photo-checks/model")
        check("API: GET /photo-checks/model", m.status_code == 200 and "threshold" in m.json())

        photo = (PHOTOS / "pothole.jpg").read_bytes()
        a = client.post("/photo-checks/analyze", files={"photo": ("p.jpg", photo, "image/jpeg")},
                        data={"category": "road", "text": "pool toot gaya"})
        body = a.json() if a.status_code == 200 else {}
        check("API: POST /photo-checks/analyze runs every check",
              a.status_code == 200 and all(k in body for k in ("capture", "defects", "damage", "screen_replay", "edit_detection", "summary")),
              a.text[:200])
        check("API: analyze returns annotated + ELA images", str(body.get("annotated_image", "")).startswith("data:image/jpeg"))
        check("API: analyze rejects a non-image", client.post("/photo-checks/analyze",
              files={"photo": ("x.txt", b"hello", "text/plain")}).status_code == 400)

        if not token:
            return
        headers = {"Authorization": f"Bearer {token}"}
        now = datetime.now(timezone.utc).isoformat()
        burst = [jpeg_bytes(Image.open(io.BytesIO(photo)).convert("RGB").resize((640, 480)))] * 3  # identical frames
        meta = [{"method": "live_camera", "captured_at": now, "lat": 16.1170, "lon": 74.2170, "accuracy_m": 9, "burst_count": 3}]
        form = {"district": "Kolhapur", "block": "Ajra", "village": "Ajara", "department": "pwd",
                "text": "Ajara madhe rastyavar mothe khadde aahet", "capture_meta": json.dumps(meta)}
        files = [("attachments", ("camera-1.jpg", photo, "image/jpeg"))] + \
                [("burst_0", (f"b{i}.jpg", b, "image/jpeg")) for i, b in enumerate(burst)]
        r1 = client.post("/citizen-report", headers=headers, data=form, files=files)
        ok = r1.status_code == 200
        check("API: citizen report with a live photo is accepted", ok, r1.text[:300])
        if not ok:
            return
        rep = r1.json()
        pcs = rep.get("photo_checks") or []
        check("API: receipt carries one photo check", len(pcs) == 1)
        first = pcs[0] if pcs else {}
        check("API: live capture recorded with distance, without coordinates",
              first.get("capture", {}).get("method") == "live_camera"
              and first["capture"].get("distance_to_village_km") is not None and "lat" not in first["capture"])
        check("API: identical burst frames flagged static (C7)", "static_burst" in first.get("summary", {}).get("flags", []))

        # Second report by another account reusing the same photo, no capture meta.
        r = client.post("/auth/register", json={
            "email": "photo_tester2@example.com", "password": "password123", "full_name": "Second Tester",
            "gender": "male", "mobile": "9876543212", "address": "Main Road", "pincode": "416505",
            "district": "Kolhapur", "block": "Ajra", "village": "Ajara"})
        token2 = r.json().get("token")
        form2 = {k: v for k, v in form.items() if k != "capture_meta"}
        r2 = client.post("/citizen-report", headers={"Authorization": f"Bearer {token2}"}, data=form2,
                         files=[("attachments", ("old.jpg", jpeg_bytes(Image.open(io.BytesIO(photo)).convert("RGB").resize((700, 520)), 70), "image/jpeg"))])
        c2 = (r2.json().get("photo_checks") or [{}])[0] if r2.status_code == 200 else {}
        flags2 = c2.get("summary", {}).get("flags", [])
        check("API: same photo reused by another account is flagged (C3)", "reused_photo" in flags2, str(flags2))
        check("API: reused photo is still stored, never rejected", r2.status_code == 200 and r2.json().get("id"))

        db = SessionLocal()
        row = db.query(CitizenRequest).filter(CitizenRequest.id == r2.json()["id"]).first()
        check("API: report confidence damped, photo_trust and review flags stored",
              row.photo_trust is not None and row.photo_trust < 1 and "reused_photo" in json.loads(row.review_flags))
        check("API: photo_check rows stored", db.query(PhotoCheck).count() == 2)
        for att in db.query(ReportAttachment).all():
            created_files.append(routes_photo_checks.UPLOAD_DIR / att.stored_name)
        for chk in db.query(PhotoCheck).all():
            if chk.annotated_name:
                created_files.append(routes_photo_checks.UPLOAD_DIR / chk.annotated_name)
        att_id = first.get("attachment_id")
        db.close()

        rc = client.get(f"/photo-checks/report/{rep['id']}")
        check("API: GET /photo-checks/report/{id}", rc.status_code == 200 and len(rc.json()["checks"]) == 1)
        if pc.MODEL_PATH.is_file():
            an = client.get(f"/report-attachment/{att_id}/annotated")
            check("API: annotated image served", an.status_code == 200 and an.headers["content-type"] == "image/jpeg")
        el = client.get(f"/report-attachment/{att_id}/ela")
        check("API: ELA image served", el.status_code == 200)

    for f in created_files:
        f.unlink(missing_ok=True)


def main() -> None:
    print("\nWorkstream C -- photo checks test suite")
    print("-" * 65)
    for test in (test_c1_capture, test_c3_duplicates, test_c4_error_level, test_c5_model,
                 test_c6_damage_grade, test_c7_screen_replay, test_summary_never_rejects, test_endpoints):
        test()
    print("-" * 65)
    print(f"  Result: {_passed}/{_passed + len(_failed)} passed, {len(_failed)} failed, {len(_skipped)} skipped")
    try:
        from database import engine
        engine.dispose()
    except Exception:
        pass
    for suffix in ("", "-wal", "-shm"):
        Path(str(_TMP_DB) + suffix).unlink(missing_ok=True)
    if _failed:
        for name in _failed:
            print(f"    failed: {name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
