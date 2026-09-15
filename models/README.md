# Pothole / crack model (Workstream C5)

The team's YOLOv8 detector, used by `backend/photo_checks.py` to answer "does
this photo actually show road damage?". Classes: `0 crack`, `1 pothole`.

## Getting the file

`pothole_best.pt` is 150 MB, over GitHub's 100 MB limit, so it is git-ignored.
Put the team's `best.pt` here as `models/pothole_best.pt`
(or point `AWAAZIQ_POTHOLE_MODEL` at it).

| | |
|---|---|
| SHA-256 | `1d1f3fd04b597e925f7dfc500a74ac67ee860f9e6640c74c72f20b37dacb1731` |
| Architecture | YOLOv8, 25.86 M parameters (m-size), Ultralytics 8.4.7 |
| Trained | 1024 px, batch 8, resumed Kaggle run, saved 2026-01-26 |
| Checkpoint's own validation | P 0.759 · R 0.708 · mAP50 0.773 · mAP50-95 0.468 |

**Repair note.** The copy we received had been unzipped into a folder, and one
entry was missing: `archive/data/634`, a 2-value momentum buffer of the
*optimizer* (used only to resume training). It was restored as zeros and the
archive re-zipped. Every model and EMA weight is byte-for-byte the original;
inference is unaffected.

## How AwaazIQ runs it

Weights are unchanged. Three inference settings, chosen by evaluation:

| Setting | Value | Why |
|---|---|---|
| Image size | 1024 | The size it was trained at |
| Confidence | ≥ 0.25 | Best F1 on real photos |
| Minimum box | 0.5 % of the frame | Almost every false alarm was a tiny box on a distant, clean road |
| Test-time augmentation | on | Raised recall on held-out photos; ~2.5× CPU time |

## Measured accuracy (15 September 2026)

"Does the photo show a pothole?", scored per image. Full numbers are in
`evaluation.json`; the lab page (`frontend/photo-lab.html`) shows them too.

| Test set | n | Setting | Accuracy | Precision | Recall | F1 | ROC-AUC |
|---|---|---|---|---|---|---|---|
| **Held-out real photos** (never used for tuning) | 88 | default | 81.8 % | 78.4 % | 78.4 % | 0.78 | 0.907 |
| | | **AwaazIQ** | **81.8 %** | **74.4 %** | **86.5 %** | **0.80** | **0.924** |
| Tuning set (chose the settings, optimistic) | 64 | default | 71.9 % | 52.0 % | 68.4 % | 0.59 | 0.844 |
| | | AwaazIQ | 84.4 % | 71.0 % | 79.0 % | 0.75 | 0.883 |
| taroii test split (224 px thumbnails) | 72 | default | 52.8 % | 100 % | 26.1 % | 0.41 | 0.711 |
| | | AwaazIQ | 66.7 % | 96.0 % | 50.0 % | 0.66 | 0.746 |

The held-out set: 37 clear potholes from 25 countries, 46 pothole-free roads
(44 of them in Kerala, Karnataka, Gujarat and Tamil Nadu) and 5 hard negatives
(natural river potholes, a diagram, a sign). Labels were checked by eye;
ambiguous photos (patched repairs, puddles) were left out. On it, AwaazIQ's
settings caught 32 of 37 potholes and raised 11 false alarms (8 on 46 clean
roads, 3 on 5 hard negatives).

Box-level, on the Roboflow "Potholes Detection" test split (100 images, 269
boxes): mAP50 0.311 default, **0.342 with TTA**. That dataset also boxes patched
repairs and tiny distant potholes, which this model does not call potholes, so
it understates the model.

Speed on the development laptop's CPU: 0.26 s per image at 640 px, 0.62 s at
1024 px; a complete photo check (every C-check, TTA on) takes about 2.9 s.

Re-run: `python models/evaluate_pothole_model.py` (add `--set tuning`).

## What it gets wrong

- **Misses**: water-filled potholes, holes full of debris or plants, very small
  or distant potholes, low-resolution photos.
- **False alarms**: dirt roads, busy junctions, painted road markings, natural
  rock "potholes" in rivers.
- **Structures**: the crack class was trained on road surfaces. On 8 photos of
  damaged bridges it fired once. C6 therefore grades bridge/building damage
  mostly from the complaint's wording and never claims a photo confirms a
  collapse.

That is why the model's output is a signal for an officer, never a verdict:
it can raise or lower a report's confidence, and nothing is rejected on it.

## The other photo checks

Calibrated in the same session (`evaluation.json → forensics`):

| Check | Result | How it is used |
|---|---|---|
| C7 moiré (photo of a screen) | 0 of 120 genuine photos flagged; 39 of 60 simulated screen recaptures flagged | Flag + review. Not yet measured on real phone photos of screens. |
| C7 burst liveness | Identical frames flagged, frames with sensor noise not | Flag + review |
| C3 duplicate photo | Resized/recompressed copies match, other scenes do not | Flag + review when another account or village |
| C4 error-level analysis | Could not tell genuine (6/50) from spliced (3/25) | **Advisory only**, heat map shown to officer, never lowers trust |
| C4 editing software in EXIF | Deterministic | Flag + review |
| C1 capture GPS / time | Deterministic; client-supplied, so raises the cost of faking rather than proving anything | Flag + review when > 5 km from the village |
