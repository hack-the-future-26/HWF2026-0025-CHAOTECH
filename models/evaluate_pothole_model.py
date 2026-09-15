"""
Re-run the image-level accuracy check behind models/evaluation.json.

    python models/evaluate_pothole_model.py            # held-out set
    python models/evaluate_pothole_model.py --set tuning

Downloads the hand-labelled Wikimedia photos listed in eval_manifest.json into
models/eval_data/ (git-ignored, politely: one request at a time with a real
User-Agent), then scores the model two ways:

  default          conf >= 0.25, no box filter, no test-time augmentation
  operating point  what backend/photo_checks.py uses (TTA, box >= 0.5 %)

The model file is only read, never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
MODEL = HERE / "pothole_best.pt"
DATA = HERE / "eval_data"
UA = {"User-Agent": "AwaazIQ-model-eval/1.0 (hackathon project; model accuracy check)"}


def fetch(rows: list[dict]) -> list[tuple[Path, int, str]]:
    DATA.mkdir(exist_ok=True)
    out = []
    for row in rows:
        path = DATA / (hashlib.sha1(row["url"].encode()).hexdigest()[:16] + ".jpg")
        if not path.exists():
            resp = requests.get(row["url"], headers=UA, timeout=60)
            if resp.status_code != 200:
                print(f"  skipped (HTTP {resp.status_code}): {row['title']}")
                continue
            path.write_bytes(resp.content)
            time.sleep(0.3)
        out.append((path, row["label"], row["kind"]))
    return out


def score(model, items, *, tta: bool, min_area: float, thr: float) -> dict:
    scores = []
    for path, label, kind in items:
        r = model.predict(str(path), imgsz=1024, conf=0.05, device="cpu", augment=tta, verbose=False)[0]
        h, w = r.orig_shape
        best = max(
            (float(c) for b, c, k in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist())
             if int(k) == 1 and (b[2] - b[0]) * (b[3] - b[1]) / (w * h) >= min_area),
            default=0.0,
        )
        scores.append((label, best, kind))
    pos = [s for y, s, _ in scores if y == 1]
    neg = [s for y, s, _ in scores if y == 0]
    tp = sum(s >= thr for s in pos)
    fp = sum(s >= thr for s in neg)
    tn, fn = len(neg) - fp, len(pos) - tp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / len(pos) if pos else 0.0
    auc = sum((1.0 if a > b else 0.5 if a == b else 0.0) for a in pos for b in neg) / max(1, len(pos) * len(neg))
    return {
        "n": len(scores), "accuracy": round((tp + tn) / max(1, len(scores)), 4),
        "precision": round(precision, 4), "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
        "roc_auc": round(auc, 4), "tp": tp, "fp": fp, "tn": tn, "fn": fn,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["heldout", "tuning"], default="heldout")
    args = ap.parse_args()
    if not MODEL.is_file():
        sys.exit(f"Model not found at {MODEL}")

    from ultralytics import YOLO

    manifest = json.loads((HERE / "eval_manifest.json").read_text(encoding="utf-8"))
    items = fetch(manifest[args.set])
    print(f"{args.set}: {len(items)} photos "
          f"({sum(1 for _, y, _ in items if y)} potholes, {sum(1 for _, y, _ in items if not y)} without)")
    model = YOLO(str(MODEL))
    print("default         ", score(model, items, tta=False, min_area=0.0, thr=0.25))
    print("operating point ", score(model, items, tta=True, min_area=0.005, thr=0.25))


if __name__ == "__main__":
    main()
