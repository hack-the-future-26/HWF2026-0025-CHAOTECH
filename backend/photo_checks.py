"""
Workstream C -- photo checks and fake-complaint defence.

Three separate questions, answered by separate checks (build plan section 10):

  Layer 2 · is the photo genuine?
      C1  capture provenance   live camera vs file, GPS + time at capture
      C3  duplicate photo      perceptual hash against every stored photo
      C4  edit detection       editing-software metadata; error-level analysis
                               (ELA) score + heat map as an advisory hint
      C7  screen replay        moire spectrum + burst liveness (sensor noise)

  Layer 3 · does the photo show the problem?
      C5  road defect          the team's YOLOv8 model (classes: crack, pothole)
      C6  damage grade         crack < partial damage < collapse, for bridges
                               and buildings, from the photo and the wording

Nothing here rejects a complaint. Every check produces evidence and, at most,
a flag for an officer; `summarise()` turns them into an authenticity number
that can damp a report's confidence but never zero it (ground rule 5).

Honest limits, stated where they bite:
  * Client capture metadata (C1) can be forged by a determined attacker. It
    raises the cost of faking, it is not proof.
  * The defect model is a signal, not a verdict (see models/README.md for its
    measured accuracy).
  * No verified ground-level building/bridge damage classifier exists yet, so
    C6 grades "collapse" from wording only and says so. DAMAGE_MODEL_PATH is
    the slot for one.
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:  # optional: only the duplicate check needs it
    import imagehash
except ImportError:  # pragma: no cover
    imagehash = None

REPO_ROOT = Path(__file__).resolve().parents[1]

# ------------------------------------------------------------------ tunables --
# Every number that decides a flag lives here, with the reason for its value.

MODEL_PATH = Path(os.getenv("AWAAZIQ_POTHOLE_MODEL", REPO_ROOT / "models" / "pothole_best.pt"))
# The model was trained at 1024 px; evaluation (models/README.md) shows it
# finds noticeably more potholes at its native size than at 640.
MODEL_IMGSZ = int(os.getenv("AWAAZIQ_POTHOLE_IMGSZ", "1024"))
# Detections below this are not even recorded.
DETECTION_MIN_CONF = 0.25
# A pothole/crack counts as "seen in the photo" at or above this confidence.
# Chosen from the image-level evaluation (models/README.md) together with the
# two settings below as the best accuracy/F1 trade-off on real photos.
DEFECT_CONF_THRESHOLD = float(os.getenv("AWAAZIQ_DEFECT_CONF", "0.25"))
# Boxes smaller than this share of the frame are ignored for the decision.
# Nearly every false positive in evaluation was a tiny box on a distant, clean
# road; a pothole worth reporting fills more of a citizen's photo than that.
MIN_BOX_AREA = float(os.getenv("AWAAZIQ_MIN_BOX_AREA", "0.005"))
# Test-time augmentation (flip + multi-scale). Inference-only, the weights are
# untouched; it lifted recall on held-out photos at ~2.5x the CPU time.
MODEL_TTA = os.getenv("AWAAZIQ_POTHOLE_TTA", "1") == "1"

# Optional future classifier for building/bridge damage (C6). Unset = none.
DAMAGE_MODEL_PATH = os.getenv("AWAAZIQ_DAMAGE_MODEL")

# C1: a photo captured further than this from the chosen village is flagged.
# Same radius the intake form already allows for a map pin.
CAPTURE_MAX_DISTANCE_KM = 5.0
# GPS worse than this is recorded but not trusted for the distance test.
CAPTURE_MAX_ACCURACY_M = 1000.0
# Minutes between capture and upload before we call the timing suspicious.
# Generous: people take a photo and then type the complaint.
CAPTURE_MAX_AGE_MIN = 120.0

# C3: perceptual-hash distances (64-bit hashes) that count as "same photo".
# phash survives resizing and recompression; dhash guards against two
# different scenes that happen to share a phash.
PHASH_MAX_DISTANCE = 8
DHASH_MAX_DISTANCE = 12

# C4: ELA re-save quality and the score at which a photo is flagged.
ELA_RESAVE_QUALITY = 90
ELA_FLAG_SCORE = 0.55
# Share of the frame (largest suspicious region) that maps to score 0.63.
ELA_KNEE = 0.0526

# C7: moire / static-burst scores at which a photo is flagged. Calibrated on
# 70 genuine photos (none above 0.3) against simulated screen recaptures
# (69 % flagged).
MOIRE_FLAG_SCORE = 0.6
MOIRE_ENERGY_SCALE = 8.0
# Mean absolute difference (0-255 grey levels) between burst frames below
# which the frames are "identical": a real sensor always adds noise, a still
# image fed through a virtual camera does not.
BURST_STATIC_DIFF = 0.35

# How much each flag lowers authenticity (multiplicative, so flags compound
# but can never reach zero). Strong = evidence of deliberate deception.
FLAG_WEIGHTS = {
    "reused_photo": 0.45,
    "static_burst": 0.35,
    "screen_replay_suspected": 0.35,
    "captured_far_from_village": 0.30,
    # Advisory only. On a fresh sample the ELA flag fired on 12 % of genuine
    # photos and 12 % of spliced ones -- no discrimination -- so it must not
    # cost an honest citizen anything. It is shown to the officer as a hint
    # alongside the heat map; the editing-software check carries the weight.
    "possibly_edited": 0.0,
    "editing_software": 0.25,
    "not_live_capture": 0.20,
    "capture_time_mismatch": 0.10,
    "no_capture_gps": 0.05,
}
AUTHENTICITY_FLOOR = 0.2
# Flags that send the report to an officer's review queue.
REVIEW_FLAGS = {
    "reused_photo", "static_burst", "screen_replay_suspected",
    "captured_far_from_village", "editing_software",
}

FLAG_TEXT = {
    "reused_photo": "Same photo already used on another report",
    "static_burst": "Camera frames identical — possible still image fed to camera",
    "screen_replay_suspected": "Looks like a photo of a screen (moiré pattern)",
    "captured_far_from_village": "Photo taken far from the chosen village",
    "possibly_edited": "Error-level hint: one region re-compresses unlike the rest (advisory)",
    "editing_software": "Image metadata names editing software",
    "not_live_capture": "Uploaded file, not captured live in the app",
    "capture_time_mismatch": "Capture time does not match upload time",
    "no_capture_gps": "No GPS reading at the moment of capture",
    "same_photo_resubmitted": "Same photo resubmitted by the same reporter",
    "no_road_damage_seen": "Road complaint, but no pothole or crack detected in the photo",
}


# ================================================================== helpers ==

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value) / (1000.0 if value > 1e11 else 1.0), timezone.utc)
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _finite(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def open_image(data: bytes) -> Image.Image:
    """Decode, apply EXIF orientation, return RGB. Raises on non-images."""
    img = Image.open(io.BytesIO(data))
    img.load()
    return ImageOps.exif_transpose(img).convert("RGB")


# ======================================================= C1 · capture checks ==

_EDITING_SOFTWARE = re.compile(
    r"photoshop|lightroom|gimp|snapseed|picsart|facetune|canva|pixlr|"
    r"affinity|meitu|photoscape|paint\.net|screenshot|screen ?capture",
    re.IGNORECASE,
)


def read_exif(data: bytes) -> dict:
    """The EXIF fields that matter for provenance. Empty dict when none."""
    try:
        img = Image.open(io.BytesIO(data))
        exif = img.getexif()
    except Exception:
        return {}
    if not exif:
        return {}
    out = {}
    tags = {271: "make", 272: "model", 305: "software", 306: "datetime"}
    for tag, name in tags.items():
        if exif.get(tag):
            out[name] = str(exif.get(tag)).strip("\x00 ")[:80]
    try:
        sub = exif.get_ifd(0x8769)
        if sub.get(36867):
            out["datetime_original"] = str(sub.get(36867)).strip("\x00 ")
    except Exception:
        pass
    try:
        gps = exif.get_ifd(0x8825)
        if gps and 2 in gps and 4 in gps:
            def dms(v):
                d, m, s = (float(x) for x in v)
                return d + m / 60 + s / 3600
            lat, lon = dms(gps[2]), dms(gps[4])
            if gps.get(1) == "S":
                lat = -lat
            if gps.get(3) == "W":
                lon = -lon
            out["gps_lat"], out["gps_lon"] = round(lat, 6), round(lon, 6)
    except Exception:
        pass
    return out


def check_capture(
    meta: dict | None,
    exif: dict,
    *,
    village_lat: float | None,
    village_lon: float | None,
    received_at: datetime | None = None,
) -> dict:
    """
    C1. Where and when the photo was taken, and whether the app's live camera
    took it. `meta` is what report.js sends for each photo; a file uploaded
    any other way arrives with no meta and is labelled so.

    The exact capture coordinates are kept for storage but the returned
    `public` block carries only a distance -- pins are never exposed.
    """
    meta = meta or {}
    received_at = received_at or _now()
    flags: list[str] = []

    method = "live_camera" if meta.get("method") == "live_camera" else "file_upload"
    if method != "live_camera":
        flags.append("not_live_capture")

    lat = _finite(meta.get("lat"))
    lon = _finite(meta.get("lon"))
    accuracy = _finite(meta.get("accuracy_m"))
    source = "capture_gps"
    if (lat is None or lon is None) and exif.get("gps_lat") is not None:
        lat, lon, source = exif["gps_lat"], exif["gps_lon"], "exif_gps"
    if lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
        lat = lon = None

    distance_km = None
    if lat is None or lon is None:
        flags.append("no_capture_gps")
        source = None
    elif village_lat is not None and village_lon is not None:
        distance_km = round(_haversine_km(lat, lon, village_lat, village_lon), 3)
        precise_enough = accuracy is None or accuracy <= CAPTURE_MAX_ACCURACY_M
        if precise_enough and distance_km > CAPTURE_MAX_DISTANCE_KM:
            flags.append("captured_far_from_village")

    captured_at = _parse_time(meta.get("captured_at"))
    age_min = None
    if captured_at is not None:
        age_min = round((received_at - captured_at).total_seconds() / 60.0, 2)
        # Negative beyond clock drift means a capture "from the future".
        if age_min > CAPTURE_MAX_AGE_MIN or age_min < -10:
            flags.append("capture_time_mismatch")
    elif method == "live_camera":
        flags.append("capture_time_mismatch")

    software = exif.get("software") or ""
    if _EDITING_SOFTWARE.search(software):
        flags.append("editing_software")

    return {
        "method": method,
        "captured_at": captured_at.isoformat() if captured_at else None,
        "minutes_before_upload": age_min,
        "lat": lat,
        "lon": lon,
        "accuracy_m": accuracy,
        "gps_source": source,
        "distance_to_village_km": distance_km,
        "burst_frames": int(meta.get("burst_count") or 0),
        "exif": {k: v for k, v in exif.items() if not k.startswith("gps_")},
        "flags": flags,
    }


# ===================================================== C3 · duplicate photos ==

def photo_hashes(img: Image.Image) -> dict:
    if imagehash is None:
        return {"phash": None, "dhash": None}
    return {"phash": str(imagehash.phash(img)), "dhash": str(imagehash.dhash(img))}


def hamming(hex_a: str | None, hex_b: str | None) -> int | None:
    if not hex_a or not hex_b or len(hex_a) != len(hex_b):
        return None
    return bin(int(hex_a, 16) ^ int(hex_b, 16)).count("1")


def find_duplicates(hashes: dict, existing: list[dict]) -> list[dict]:
    """
    C3. `existing` rows: {attachment_id, request_id, phash, dhash, village,
    user_id}. Returns matches, closest first. Deciding whether a match is
    suspicious (other village / other account) is done by the caller, which
    knows who is filing.
    """
    matches = []
    for row in existing:
        dp = hamming(hashes.get("phash"), row.get("phash"))
        dd = hamming(hashes.get("dhash"), row.get("dhash"))
        if dp is None or dd is None:
            continue
        if dp <= PHASH_MAX_DISTANCE and dd <= DHASH_MAX_DISTANCE:
            matches.append({**row, "phash_distance": dp, "dhash_distance": dd})
    matches.sort(key=lambda m: (m["phash_distance"], m["dhash_distance"]))
    return matches


def classify_duplicates(matches: list[dict], *, village: str | None, user_id: int | None) -> tuple[list[str], dict | None]:
    if not matches:
        return [], None
    best = matches[0]
    same_person = user_id is not None and best.get("user_id") == user_id
    same_place = (village or "").lower() == (best.get("village") or "").lower()
    flag = "same_photo_resubmitted" if (same_person and same_place) else "reused_photo"
    summary = {
        "attachment_id": best.get("attachment_id"),
        "request_id": best.get("request_id"),
        "phash_distance": best["phash_distance"],
        "same_reporter": same_person,
        "same_village": same_place,
        "matches": len(matches),
    }
    return [flag], summary


# ==================================================== C4 · error level (ELA) ==

def _blocks(arr: np.ndarray, size: int) -> np.ndarray:
    h, w = arr.shape[:2]
    h2, w2 = h - h % size, w - w % size
    a = arr[:h2, :w2]
    return a.reshape(h2 // size, size, w2 // size, size).mean(axis=(1, 3))


def _largest_component(mask: np.ndarray) -> int:
    """Size of the largest 4-connected True region (iterative flood fill)."""
    seen = np.zeros_like(mask, dtype=bool)
    best = 0
    rows, cols = mask.shape
    for r0 in range(rows):
        for c0 in range(cols):
            if not mask[r0, c0] or seen[r0, c0]:
                continue
            stack, size = [(r0, c0)], 0
            seen[r0, c0] = True
            while stack:
                r, c = stack.pop()
                size += 1
                for rr, cc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                    if 0 <= rr < rows and 0 <= cc < cols and mask[rr, cc] and not seen[rr, cc]:
                        seen[rr, cc] = True
                        stack.append((rr, cc))
            best = max(best, size)
    return best


def error_level_analysis(img: Image.Image, *, was_jpeg: bool = True) -> dict:
    """
    C4. Re-save at a known JPEG quality and look for a *region* whose error
    level is out of line with the rest of the photo.

    Raw error level mostly tracks texture (gravel re-compresses worse than
    sky), so each 16 px block's error is compared with what its own texture
    predicts (a robust linear fit across the image). A pasted or retouched
    patch shows up as a connected run of blocks far off that line; isolated
    outliers are normal and ignored. Returns a 0-1 "possibly edited" score.
    """
    work = img
    longest = max(img.size)
    if longest > 1600:  # keep the 8x8 grid meaningful but bound the cost
        scale = 1600 / longest
        work = img.resize((int(img.width * scale), int(img.height * scale)), Image.BILINEAR)
        buf0 = io.BytesIO()
        work.save(buf0, "JPEG", quality=95)
        work = Image.open(io.BytesIO(buf0.getvalue())).convert("RGB")

    buf = io.BytesIO()
    work.save(buf, "JPEG", quality=ELA_RESAVE_QUALITY)
    resaved = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    a = np.asarray(work, dtype=np.float32)
    b = np.asarray(resaved, dtype=np.float32)
    err = np.abs(a - b).mean(axis=2)
    gray = a.mean(axis=2)

    size = 16
    e_blk = _blocks(err, size)
    g = gray[: gray.shape[0] - gray.shape[0] % size, : gray.shape[1] - gray.shape[1] % size]
    tex = g.reshape(e_blk.shape[0], size, e_blk.shape[1], size).std(axis=(1, 3))
    if e_blk.size < 16:
        return {"score": None, "note": "image too small for error-level analysis"}

    x, y = tex.ravel(), e_blk.ravel()
    # Robust line: fit, drop the worst 20 %, refit.
    coef = np.polyfit(x, y, 1)
    resid = y - np.polyval(coef, x)
    keep = np.abs(resid) <= np.percentile(np.abs(resid), 80)
    if keep.sum() >= 8:
        coef = np.polyfit(x[keep], y[keep], 1)
    resid = (y - np.polyval(coef, x)).reshape(e_blk.shape)
    mad = np.median(np.abs(resid - np.median(resid))) * 1.4826 + 1e-3
    z = (resid - np.median(resid)) / mad

    suspicious = np.abs(z) > 4.0
    largest = _largest_component(suspicious)
    frac_largest = largest / e_blk.size
    # A tampered region is contiguous. The knee was set so ELA_FLAG_SCORE sat
    # at the 95th percentile of 60 genuine road photos. Re-tested on a fresh
    # sample it could not tell genuine from spliced photos (12 % vs 12 %), so
    # the score is advisory: shown with the heat map, never used for trust.
    # See models/README.md.
    score = float(1.0 - math.exp(-frac_largest / ELA_KNEE))
    return {
        "score": round(score, 3),
        "suspicious_region_pct": round(frac_largest * 100, 2),
        "mean_error_level": round(float(err.mean()), 3),
        "compressed_source": was_jpeg,
        "note": None if was_jpeg else "PNG/WebP source: error level is less informative",
    }


def ela_heatmap(img: Image.Image) -> Image.Image:
    """Amplified error-level image for an officer to eyeball."""
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=ELA_RESAVE_QUALITY)
    resaved = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    diff = np.abs(np.asarray(img, dtype=np.int16) - np.asarray(resaved, dtype=np.int16)).astype(np.float32)
    peak = max(1.0, float(np.percentile(diff, 99.5)))
    out = np.clip(diff * (255.0 / peak), 0, 255).astype(np.uint8)
    return Image.fromarray(out)


# ================================================== C7 · screen replay checks ==

def moire_score(img: Image.Image) -> dict:
    """
    C7a. Photographing a display makes the screen's sub-pixel grid interfere
    with the camera sensor's grid. In the spectrum that is a set of sharp,
    isolated peaks standing well above their local neighbourhood -- a natural
    scene's spectrum is smooth. The peaks are strongest in the colour-opponent
    channels because the grid is made of red, green and blue stripes.

    Each channel's log spectrum is compared with an 11x11 median-filtered
    copy of itself; local maxima more than 6 sigma above it count. The axes
    (kerbs, building edges) and the JPEG 8x8 lattice are masked out.
    """
    from scipy import ndimage

    rgb = np.asarray(img, dtype=np.float32)
    h, w = rgb.shape[:2]
    if min(h, w) < 128:
        return {"score": None, "note": "image too small"}
    n = min(512, 1 << int(math.log2(min(h, w))))
    y0, x0 = (h - n) // 2, (w - n) // 2
    c = rgb[y0:y0 + n, x0:x0 + n]
    win = np.outer(np.hanning(n), np.hanning(n))
    yy, xx = np.indices((n, n))
    r = np.hypot(yy - n / 2, xx - n / 2)
    band = (r > n * 0.03) & (r < n * 0.47)
    axis = (np.abs(yy - n / 2) <= 1) | (np.abs(xx - n / 2) <= 1)
    lattice = np.zeros((n, n), dtype=bool)
    for k in range(-4, 5):
        p = int(round(n / 2 + k * n / 8))
        lattice[max(0, p - 1):p + 2, :] = True
        lattice[:, max(0, p - 1):p + 2] = True
    valid = band & ~axis & ~lattice

    channels = {
        "red_green": c[..., 0] - c[..., 1],
        "blue_green": c[..., 2] - c[..., 1],
        "luma": 0.299 * c[..., 0] + 0.587 * c[..., 1] + 0.114 * c[..., 2],
    }
    energy, peaks = 0.0, {}
    for name, ch in channels.items():
        spec = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2((ch - ch.mean()) * win))))
        prominence = spec - ndimage.median_filter(spec, size=11)
        sd = float(np.std(prominence[valid])) + 1e-6
        pk = (prominence > 6 * sd) & valid & (spec == ndimage.maximum_filter(spec, size=5))
        peaks[name] = int(pk.sum())
        if pk.any():
            energy = max(energy, float(prominence[pk].sum() / sd))
    score = float(1.0 - math.exp(-energy / MOIRE_ENERGY_SCALE))
    return {"score": round(score, 3), "peak_energy": round(energy, 2), "peaks": peaks}


def burst_liveness(frames: list[Image.Image]) -> dict:
    """
    C7b. The capture sends a short burst. Two things a real handheld camera
    always shows: sensor noise (frames never identical) and a little motion.
    A still image pushed through a virtual camera, or a replayed file, has
    neither. A screen held in front of a real camera still has noise, so
    this complements the moire check rather than replacing it.
    """
    if len(frames) < 2:
        return {"frames": len(frames), "static": None, "note": "no burst captured"}
    small = []
    for f in frames:
        g = f.convert("L")
        g = g.resize((320, max(1, int(320 * g.height / g.width))), Image.BILINEAR)
        small.append(np.asarray(g, dtype=np.float32))
    shape = small[0].shape
    small = [s for s in small if s.shape == shape]
    diffs, shifts = [], []
    for a, b in zip(small, small[1:]):
        diffs.append(float(np.abs(a - b).mean()))
        fa, fb = np.fft.fft2(a), np.fft.fft2(b)
        cross = fa * np.conj(fb)
        corr = np.abs(np.fft.ifft2(cross / (np.abs(cross) + 1e-6)))
        py, px = np.unravel_index(np.argmax(corr), corr.shape)
        py = py - shape[0] if py > shape[0] // 2 else py
        px = px - shape[1] if px > shape[1] // 2 else px
        shifts.append(float(math.hypot(py, px)))
    mean_diff = sum(diffs) / len(diffs)
    return {
        "frames": len(small),
        "mean_frame_difference": round(mean_diff, 3),
        "max_shift_px": round(max(shifts), 2),
        "static": mean_diff < BURST_STATIC_DIFF,
    }


# ============================================== C5 · road defect detection ==

_model = None
_model_lock = threading.Lock()
_model_error: str | None = None


def model_info() -> dict:
    return {
        "path": str(MODEL_PATH.relative_to(REPO_ROOT)) if MODEL_PATH.is_relative_to(REPO_ROOT) else str(MODEL_PATH),
        "available": MODEL_PATH.is_file(),
        "imgsz": MODEL_IMGSZ,
        "threshold": DEFECT_CONF_THRESHOLD,
        "min_box_area_pct": MIN_BOX_AREA * 100,
        "tta": MODEL_TTA,
        "classes": {0: "crack", 1: "pothole"},
        "architecture": "YOLOv8 detector (Ultralytics 8.4.7), 25.9M parameters",
        "load_error": _model_error,
    }


def get_model():
    """Load the team's detector once. None (with a reason) when unavailable."""
    global _model, _model_error
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        if not MODEL_PATH.is_file():
            _model_error = f"model file not found at {MODEL_PATH}"
            return None
        try:
            from ultralytics import YOLO
            _model = YOLO(str(MODEL_PATH))
            _model_error = None
        except Exception as exc:  # pragma: no cover - depends on environment
            _model_error = f"{type(exc).__name__}: {exc}"
            _model = None
    return _model


_predict_lock = threading.Lock()


def detect_defects(img: Image.Image) -> dict:
    """
    C5. Run the pothole/crack detector. Boxes are normalised 0-1 so they can
    be drawn over any rendition of the photo.
    """
    model = get_model()
    if model is None:
        return {"available": False, "reason": _model_error, "detections": []}
    with _predict_lock:  # Ultralytics predictors are not thread-safe
        result = model.predict(img, imgsz=MODEL_IMGSZ, conf=DETECTION_MIN_CONF, device="cpu",
                               augment=MODEL_TTA, verbose=False)[0]
    w, h = img.size
    dets = []
    for box, conf, cls in zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist(), result.boxes.cls.tolist()):
        x1, y1, x2, y2 = box
        area = max(0.0, (x2 - x1) * (y2 - y1)) / float(w * h)
        dets.append({
            "label": model.names.get(int(cls), str(int(cls))),
            "confidence": round(float(conf), 4),
            "box": [round(x1 / w, 4), round(y1 / h, 4), round(x2 / w, 4), round(y2 / h, 4)],
            "area_pct": round(area * 100, 2),
            "too_small": area < MIN_BOX_AREA,
        })
    dets.sort(key=lambda d: -d["confidence"])
    counted = [d for d in dets if not d["too_small"]]

    def best(label):
        return max((d["confidence"] for d in counted if d["label"] == label), default=0.0)

    strong = [d for d in counted if d["confidence"] >= DEFECT_CONF_THRESHOLD]
    return {
        "available": True,
        "detections": dets,
        "pothole_confidence": round(best("pothole"), 4),
        "crack_confidence": round(best("crack"), 4),
        "potholes": sum(1 for d in strong if d["label"] == "pothole"),
        "cracks": sum(1 for d in strong if d["label"] == "crack"),
        "damage_area_pct": round(_union_area(strong) * 100, 1),
        "defect_seen": bool(strong),
        "ignored_small_boxes": len(dets) - len(counted),
        "threshold": DEFECT_CONF_THRESHOLD,
        "min_box_area_pct": MIN_BOX_AREA * 100,
        "tta": MODEL_TTA,
    }


def _union_area(dets: list[dict], grid: int = 200) -> float:
    if not dets:
        return 0.0
    mask = np.zeros((grid, grid), dtype=bool)
    for d in dets:
        x1, y1, x2, y2 = d["box"]
        mask[int(y1 * grid):max(int(y1 * grid) + 1, int(y2 * grid)),
             int(x1 * grid):max(int(x1 * grid) + 1, int(x2 * grid))] = True
    return float(mask.mean())


def draw_detections(img: Image.Image, dets: list[dict], max_side: int = 1280) -> Image.Image:
    """The photo with boxes and labels, for the receipt and the officer panel."""
    out = img.copy()
    if max(out.size) > max_side:
        out.thumbnail((max_side, max_side))
    w, h = out.size
    draw = ImageDraw.Draw(out, "RGBA")
    stroke = max(2, int(round(min(w, h) / 220)))
    try:
        font = ImageFont.truetype("arial.ttf", max(12, int(min(w, h) / 34)))
    except OSError:
        font = ImageFont.load_default()
    colours = {"pothole": (192, 120, 30), "crack": (46, 107, 69)}
    for d in sorted(dets, key=lambda d: d["confidence"]):
        if d["confidence"] < DETECTION_MIN_CONF or d.get("too_small"):
            continue
        col = colours.get(d["label"], (27, 42, 74))
        x1, y1, x2, y2 = d["box"][0] * w, d["box"][1] * h, d["box"][2] * w, d["box"][3] * h
        draw.rectangle([x1, y1, x2, y2], outline=col + (255,), width=stroke, fill=col + (38,))
        text = f"{d['label']} {d['confidence'] * 100:.0f}%"
        tb = draw.textbbox((0, 0), text, font=font)
        tw, th = tb[2] - tb[0] + 10, tb[3] - tb[1] + 8
        ty = y1 - th if y1 - th > 0 else y1
        draw.rectangle([x1, ty, x1 + tw, ty + th], fill=col + (235,))
        draw.text((x1 + 5, ty + 3), text, fill=(255, 255, 255), font=font)
    return out


# ================================================= C6 · emergency damage grade ==

GRADE_VALUE = {"crack": 1 / 3, "partial": 2 / 3, "collapse": 1.0}

_STRUCTURE_WORDS = {
    "bridge": r"bridge|culvert|causeway|\bpul\b|\bpool\b|\bpuliya\b|\bsakav\b|पूल|पुल|पुलिया|साकव|मोरी",
    "building": r"building|wall|roof|ceiling|classroom|\bimarat\b|\bbhint\b|\bdeewar\b|\bdiwar\b|\bchhat\b|"
                r"इमारत|भिंत|दीवार|दिवार|छत|छप्पर|वर्ग ?खोली",
}
_GRADE_WORDS = {
    "collapse": r"collaps|caved in|fell down|washed away|gir gaya|gir gayi|dhah gay|toot gaya|tut gaya|"
                r"bah gaya|koslal|कोसळ|ढह|गिर गय|टूट गय|बह गय|वाहून गेल|पडल[ाे]",
    "partial": r"damag|broken|partly|partial|sinking|subsid|dhasal|khachal|tootla|tutla|"
               r"क्षतिग्रस्त|नुकसान|तुटल|खचल|धसल|टूटा|टूटी|धंस",
    "crack": r"crack|\bdarar\b|\btada\b|\btade\b|\bbheg\b|दरार|तडा|तडे|भेग",
}


def _match(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def grade_damage(text: str | None, defects: dict | None) -> dict:
    """
    C6. Owner's definition: urgency is for bridge or building damage only,
    graded crack (1/3) < partial damage (2/3) < collapse (1). Potholes are
    road condition, not emergencies.

    Signals, none decisive alone, every one optional:
      photo  - the detector's crack class; many/large detections -> partial
      words  - catastrophic wording in the complaint (en / hi / mr / Hinglish)
    The detector was trained on road surfaces, so on a wall or bridge photo it
    is a proxy, and it cannot see a collapse. A collapse grade therefore
    always rests on wording and is labelled unconfirmed by photo.
    """
    text = text or ""
    structure = next((name for name, pat in _STRUCTURE_WORDS.items() if _match(pat, text)), None)
    word_grade = next((g for g in ("collapse", "partial", "crack") if _match(_GRADE_WORDS[g], text)), None)

    photo_grade, photo_conf = None, 0.0
    if defects and defects.get("available"):
        crack = defects.get("crack_confidence", 0.0)
        pothole = defects.get("pothole_confidence", 0.0)
        n_strong = defects.get("cracks", 0) + defects.get("potholes", 0)
        if n_strong >= 2 and defects.get("damage_area_pct", 0) >= 20:
            photo_grade, photo_conf = "partial", max(crack, pothole)
        elif crack >= DEFECT_CONF_THRESHOLD:
            photo_grade, photo_conf = "crack", crack

    signals = []
    if photo_grade:
        signals.append({"signal": "photo_damage_model", "grade": photo_grade,
                        "confidence": round(photo_conf, 3), "weight": 0.45})
    if word_grade:
        signals.append({"signal": "complaint_wording", "grade": word_grade, "confidence": 1.0, "weight": 0.35})

    grades = [s["grade"] for s in signals]
    grade = max(grades, key=lambda g: GRADE_VALUE[g]) if grades else None
    confidence = sum(s["weight"] * s["confidence"] for s in signals)
    if photo_grade and word_grade:
        confidence += 0.20  # two independent sources agree that something is broken
    confidence = round(min(1.0, confidence), 3)

    return {
        "structure": structure,
        "grade": grade,
        "grade_value": round(GRADE_VALUE[grade], 3) if grade else 0.0,
        "confidence": confidence if grade else 0.0,
        "emergency_candidate": bool(structure and grade),
        "photo_grade": photo_grade,
        "wording_grade": word_grade,
        "signals": signals,
        "collapse_confirmed_by_photo": False,
        "damage_model": "building/bridge classifier not installed" if not DAMAGE_MODEL_PATH else DAMAGE_MODEL_PATH,
    }


# ===================================================== combining the layers ==

def summarise(capture: dict, duplicate_flags: list[str], ela: dict, moire: dict,
              burst: dict, defects: dict, category: str | None) -> dict:
    flags = list(dict.fromkeys(capture.get("flags", []) + duplicate_flags))
    if (ela.get("score") or 0) >= ELA_FLAG_SCORE:
        flags.append("possibly_edited")
    if (moire.get("score") or 0) >= MOIRE_FLAG_SCORE:
        flags.append("screen_replay_suspected")
    if burst.get("static"):
        flags.append("static_burst")
    if category == "road" and defects.get("available") and not defects.get("defect_seen"):
        flags.append("no_road_damage_seen")

    authenticity = 1.0
    for f in flags:
        authenticity *= 1.0 - FLAG_WEIGHTS.get(f, 0.0)
    authenticity = round(max(AUTHENTICITY_FLOOR, authenticity), 3)

    # Does the (genuine) photo support the complaint? Only answerable where the
    # model has something to look for.
    support = None
    if defects.get("available") and category == "road":
        support = round(max(defects.get("pothole_confidence", 0), defects.get("crack_confidence", 0)), 3)

    review = sorted(f for f in flags if f in REVIEW_FLAGS)
    if authenticity >= 0.85 and (support is None or support >= DEFECT_CONF_THRESHOLD):
        verdict = "verified"
    elif review:
        verdict = "needs_review"
    else:
        verdict = "partly_verified"

    return {
        "flags": flags,
        "flag_text": {f: FLAG_TEXT.get(f, f) for f in flags},
        "review_flags": review,
        "authenticity": authenticity,
        "support": support,
        "verdict": verdict,
    }


def confidence_adjustment(confidence: float | None, summaries: list[dict]) -> float | None:
    """
    How photo checks move a report's confidence. Damp for doubt, lift for a
    genuine live photo that shows the problem; never below half the original
    and never above 1 (ground rule 5: damped, never zeroed).
    """
    if confidence is None or not summaries:
        return confidence
    worst = min(s["authenticity"] for s in summaries)
    new = confidence * (0.7 + 0.3 * worst)
    supported = any(s["verdict"] == "verified" and (s["support"] or 0) >= DEFECT_CONF_THRESHOLD for s in summaries)
    if worst >= 0.85 and supported:
        new = confidence + (1.0 - confidence) * 0.25
    return round(min(1.0, max(confidence * 0.5, new)), 4)


def analyze_photo(
    data: bytes,
    *,
    content_type: str = "image/jpeg",
    capture_meta: dict | None = None,
    burst: list[bytes] | None = None,
    village_lat: float | None = None,
    village_lon: float | None = None,
    village: str | None = None,
    user_id: int | None = None,
    category: str | None = None,
    text: str | None = None,
    existing_hashes: list[dict] | None = None,
    received_at: datetime | None = None,
) -> tuple[dict, Image.Image]:
    """Run C1, C3, C4, C5, C6 and C7 on one photo. Returns (result, image)."""
    img = open_image(data)
    exif = read_exif(data)
    capture = check_capture(capture_meta, exif, village_lat=village_lat,
                            village_lon=village_lon, received_at=received_at)
    hashes = photo_hashes(img)
    matches = find_duplicates(hashes, existing_hashes or [])
    dup_flags, dup_summary = classify_duplicates(matches, village=village, user_id=user_id)

    was_jpeg = content_type in ("image/jpeg", "image/jpg")
    ela = error_level_analysis(img, was_jpeg=was_jpeg)
    moire = moire_score(img)
    frames = []
    for raw in burst or []:
        try:
            frames.append(open_image(raw))
        except Exception:
            continue
    live = burst_liveness(frames)
    screen = {
        "moire": moire,
        "burst": live,
        "score": round(max(moire.get("score") or 0.0, 0.9 if live.get("static") else 0.0), 3),
    }
    defects = detect_defects(img)
    damage = grade_damage(text, defects)
    summary = summarise(capture, dup_flags, ela, moire, live, defects, category)

    result = {
        "checked_at": _now().isoformat(),
        "image": {"width": img.width, "height": img.height, "content_type": content_type},
        "capture": capture,                       # C1
        "hashes": hashes,                         # C3
        "duplicate": dup_summary,                 # C3
        "edit_detection": ela,                    # C4
        "defects": defects,                       # C5
        "damage": damage,                         # C6
        "screen_replay": screen,                  # C7
        "summary": summary,
        "model": {"imgsz": MODEL_IMGSZ, "threshold": DEFECT_CONF_THRESHOLD},
    }
    return result, img


def public_view(result: dict) -> dict:
    """What may leave the server: no capture coordinates, no hashes."""
    capture = dict(result.get("capture") or {})
    capture.pop("lat", None)
    capture.pop("lon", None)
    out = {k: v for k, v in result.items() if k not in ("hashes", "capture")}
    out["capture"] = capture
    return out


def to_data_url(img: Image.Image, quality: int = 85) -> str:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def dumps(obj) -> str:
    return json.dumps(obj, default=str)
