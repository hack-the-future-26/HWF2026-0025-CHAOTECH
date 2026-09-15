# Research: verifying a complaint's photo, not just corroborating its existence

Companion to `STALE_INFRA_DEFICIT_RESEARCH.md`. That doc's recency+trust-
blend mechanism answers one question: *"did enough independent people say
the same thing, recently?"* It was never meant to answer, and cannot
answer, a different question the user is now correctly pushing on:
**"does this specific photo actually show a real pothole / real structural
damage?"** These are genuinely two different problems needing two
different mechanisms. This doc researches the second one.

**Two different questions hide inside "verify the complaint," and they
need to be kept separate:**

1. **Is the photo itself genuine?** — not edited, not stolen from the
   internet, not reused from another complaint, not AI-generated.
2. **Does the (genuine) photo actually show what's claimed?** — is that
   really a pothole, a real crack, real structural damage — not a picture
   of a normal driveway or an unrelated object.

A photo can fail either test independently: a real, unedited photo of a
perfectly fine road fails test 2 but passes test 1. A genuine-looking but
digitally altered photo of a real pothole elsewhere fails test 1. Both
need answering; neither substitutes for the other.

## 1. Question 2 first: real, deployed models exist for this — with honest limits

### 1.1 Potholes / road surface damage — genuinely solved to a useful degree

**RDD2022** is the standard, and it matters specifically for this project:
it's a real multi-national benchmark dataset — 47,420 images, 55,000+
damage annotations, from **Japan, India, Czech Republic, Norway, USA, and
China** — meaning Indian road imagery is already part of what these models
are trained and evaluated on, not a US/European dataset being awkwardly
applied to Indian roads.
[arXiv: RDD2022 dataset paper](https://arxiv.org/abs/2209.08538),
[Wiley: RDD2022 in Geoscience Data Journal](https://rmets.onlinelibrary.wiley.com/doi/10.1002/gdj3.260)

**Real, honest accuracy numbers** — worth stating precisely rather than
implying this is solved: the 2022 international challenge (CRDDC'2022,
121 teams) saw its best cross-country model reach an **F1 score of 0.769**
(~77%); a more typical strong ensemble scored around **0.67**. That means
even the best published models still meaningfully mis-detect or
false-positive roughly a quarter of the time. **This is a real,
usable signal, not a solved oracle** — treat model output the way this
project already treats every other proxy: a confidence input that dampens
or boosts, never a binary accept/reject.
[CRDDC 2022 results discussion](https://arxiv.org/pdf/2209.08538)

**Deployment is genuinely lightweight.** YOLOv8-based models fine-tuned
for pothole detection report 90%+ precision/mAP@50 on curated test sets,
and the smallest variant (YOLOv8n) runs at **110+ FPS with only 4.1M
parameters** — light enough to run on ordinary CPU hardware for
single-image inference, no GPU cluster required for a project at this
scale.
[IEEE: YOLOv8 pothole detection](https://ieeexplore.ieee.org/document/10465038/),
[arXiv: Enhanced YOLOv8 pothole detection](https://arxiv.org/abs/2505.04207)

**Real-world proof this whole approach works in production, not just in
papers:** [RoadBotics](https://spectrum.ieee.org/roadbotics-ai-could-change-the-way-cities-maintain-roads)
(a Carnegie Mellon spinout) runs exactly this pipeline — smartphone photos
→ computer vision → per-road condition score — for **90+ real US cities,
counties, and state DOTs**, including Pennsylvania DOT (2,500+ miles
surveyed) and Detroit (4,200km network). Savannah, Georgia cites $80,000
saved from better-targeted maintenance. This is the strongest evidence in
this entire research thread that "photo → AI → actionable condition score"
is not a hackathon fantasy — it's a funded, operating product.
[RoadBotics deployment coverage](https://www.ri.cmu.edu/roadbotics-ai-could-change-the-way-cities-maintain-roads/)

### 1.2 Structural cracks — solved for "is there a crack," not for "is the building safe"

**SDNET2018** is the standard dataset: 56,000+ images of bridge decks,
walls, and pavements, labeled cracked/not-cracked, cracks from 0.06mm to
25mm. CNN models on this dataset report strong results — a lightweight
model (Lite-V2, 0.28M parameters) reaches 95.7% test accuracy; another
approach reports 92-96% depending on structure type (road/wall/bridge).
[SDNET2018 dataset paper](https://www.researchgate.net/publication/328771609_SDNET2018_An_annotated_image_dataset_for_non-contact_concrete_crack_detection_using_deep_convolutional_neural_networks),
[Lite-V2 crack detection](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11243917/)

**The honest gap, stated plainly:** SDNET2018 answers *"is there a crack
in this close-up photo of a concrete surface"* — a real, useful,
high-accuracy classifier. It does **not** answer *"is this whole building
about to collapse,"* which is a structural-safety judgment, not a
crack-presence judgment. A hairline crack and a building with a
compromised load-bearing wall can both show "crack detected: yes."
**There is no standard, off-the-shelf model for "assess this building's
structural safety from one ground-level citizen photo."** The nearest
comparable dataset, xBD (below), solves an adjacent but different problem.

### 1.3 Whole-building damage assessment — the closest dataset uses the wrong camera angle

**xBD** (from the xView2 challenge) is the standard for building damage
classification — 850,736 labeled buildings across 453,610 km², four damage
classes (no-damage/minor/major/destroyed). But it's built from
**bi-temporal satellite imagery** (<0.8m resolution, before/after pairs),
for disaster-response mapping at scale — not from ground-level citizen
phone photos.
[xBD dataset overview](https://www.tandfonline.com/doi/full/10.1080/17538947.2024.2302577)

**This connects to `STALE_INFRA_DEFICIT_RESEARCH.md` §2A (satellite-gated
verification), not to citizen photo verification.** If Planet Labs
imagery ever gets built for the catastrophic-collapse case, an xBD-style
classifier is the right model to run on it. For a citizen's own ground-
level photo of a specific building, no equivalent standard, benchmarked
dataset exists today. Building one would mean collecting and labeling
AwaazIQ's own ground-level photos — a real project, not a weekend task,
and out of scope until there's enough submitted photo volume to make it
possible at all.

## 2. Question 1: is the photo itself genuine — how a mature, at-scale industry actually does this

Insurance claims fraud detection is the closest real-world analogue: an
industry that has verified photo-based claims at massive scale for years,
specifically to catch reused, edited, and now AI-generated images. Its
current, disclosed technique stack — reported consistently across
multiple industry sources — has **four distinct layers**:
[Carpe: detecting AI-generated photos in insurance claims](https://carpe.io/resources/blog/how-to-detect-ai-generated-photos-in-insurance-claims/),
[VAARHAFT: insurance image fraud detection guide](https://www.vaarhaft.com/blog/insurance),
[Ethos Risk: forensic verification in claims](https://ethosrisk.com/blog/forensic-verification-in-claims-investigations/)

1. **Metadata forensics** — EXIF timestamp and GPS cross-checked against
   the claimed incident time and place, *plus* device-information checks
   for anomalies (e.g. a photo whose metadata says it came from
   screenshot software, not a camera). This project already has the EXIF
   half designed (`SESSION_LOG_2026-09-12.md`); the device-anomaly check
   is a small, genuinely new addition worth adding to the same step.
2. **Pixel-level / Error Level Analysis (ELA)** — detects whether *parts*
   of an image were edited (recompressed at a different quality than the
   rest), catching a manipulated photo even with clean metadata and even
   with no match anywhere else on the internet. **This is free and
   computable locally** — no paid API, no external service, just a
   well-established image-processing technique (Python libraries exist
   for this). It is a genuinely new layer this project hasn't considered
   yet, and it's the cheapest one to add.
3. **Reverse image search against the open web** — already flagged in the
   09-12 session as "real but needs a paid API (Google Vision, TinEye),
   not free, not built." Confirmed here as a standard part of how mature
   systems actually do this, not an edge case — it catches a stolen news
   or stock photo, which internal duplicate-hash checking (`imagehash`,
   already decided) cannot, since that only catches a photo reused
   *within this project's own database*, not one lifted from Google.
4. **AI-generated image detection** (Vision Transformers, current
   generation) — confirmed still an active, unsettled arms race even in
   a well-funded industry with dedicated fraud teams. This validates
   the 09-12 session's own decision *not* to build automated deepfake
   detection: even insurance fraud teams treat this as one signal that
   routes a claim to human review, never an automatic accept/reject.

## 2A. The screen-replay attack — someone photographing a photo, not the scene

Asked directly (2026-09-14): a citizen isn't required to upload a file —
they must be physically present and use the live camera. But what stops
someone from pointing that live camera at **another screen** displaying a
pothole/damage photo, so the YOLO/crack model sees a "real" defect that
isn't at their actual location? This is a well-studied problem with a
name: **recapture / presentation-attack detection**, not a gap this
project has to solve from scratch.

**The physical tell is real and well-documented: moiré patterns.**
Photographing any digital display produces interference between the
display's own pixel grid and the camera sensor's grid — a distinctive
high-frequency artifact that does not appear when photographing a real
physical scene. This is the foundation of an entire research area
("recaptured screen image identification"), with CNN and Vision
Transformer models built specifically to detect it, plus secondary tells:
specular reflections off the screen surface, visible screen bezel/frame
edges, and color/contrast compression from the display's own rendering.
[ScienceDirect: recaptured screen image identification via Vision Transformer](https://www.sciencedirect.com/science/article/abs/pii/S1047320322002127),
[Recapture Detection to Fight Deep Identity Theft (ACM)](https://dl.acm.org/doi/fullHtml/10.1145/3577164.3577170),
[PMC: detecting presentation attacks with a patch-based CNN](https://pmc.ncbi.nlm.nih.gov/articles/PMC7473524/)

**This exact problem is already solved commercially, at scale, in KYC/
identity verification** — an industry whose entire job is stopping someone
from holding up a photo or a screen instead of the real document/face.
Mitek's "Document Liveness Detection" product is the direct analogue:
same attack, same defense. Their disclosed technique stack — depth
analysis, texture/reflection analysis, moiré detection, and increasingly
multi-frame or short-video capture rather than a single still — maps
directly onto what a pothole-photo pipeline needs.
[Mitek: Document Liveness Detection](https://www.miteksystems.com/blog/document-liveness-detection),
[Facia.ai: liveness detection in KYC](https://facia.ai/blog/liveness-detection-in-kyc-changing-the-online-identity-verification/)

**Concrete design for this project, combining what's verified above with
what's already planned:**

1. **Force capture via `getUserMedia()` + the Image Capture API, not
   `<input type="file" capture="camera">`.** The file-input attribute is
   only a *hint* — many browsers/OSes still let the user back out to the
   gallery. `getUserMedia()` pulls a live video stream directly; there is
   no file picker at all, so there is nothing to select a saved image
   from. Requires HTTPS (or localhost) — already true for anything using
   the "Use my current location" geolocation button design (that also
   needs a secure origin).
   [MDN: taking still photos with getUserMedia()](https://developer.mozilla.org/en-US/docs/Web/API/Media_Capture_and_Streams_API/Taking_still_photos),
   [Chrome Developers: Image Capture API](https://developer.chrome.com/blog/imagecapture)
2. **Run a moiré/recapture check as a Layer 2 signal**, before or
   alongside the Layer 3 defect classifier — flag "this looks like a photo
   of a screen," and, matching this project's established philosophy
   everywhere else, treat it as a **confidence signal that dampens**, not
   a hard reject: real-world false positives exist (a photo taken through
   a rain-speckled window, unusual lighting) and the corroboration layer
   (Layer 1) still catches a genuinely fabricated report even if one photo
   slips through.
3. **A cheap, no-extra-hardware liveness cue**: capture a short burst (a
   handful of frames, not a single still) and check for natural
   frame-to-frame variation — a real handheld scene shows small parallax
   and hand-shake; a flat screen held in front of the camera is
   suspiciously static, and a monitor's bezel edge is detectable as a hard
   rectangular boundary that a real pothole never has.
4. **Tie GPS capture to the same live moment**, not a separately-set map
   pin from days earlier — the "Use my current location" button design
   from Task 1's review should fire *during* the same capture flow, so the
   coordinate, the timestamp, and the camera frame are all the same event,
   not three things a person could fake independently.

None of this makes spoofing impossible — nothing does, including in the
KYC industry this borrows from. It raises the cost of faking a report from
"screenshot a pothole photo from Google" to "print a photo, defeat moiré
detection, defeat liveness framing, and be at a GPS-plausible location
while doing it" — which is the same proportionate, layered-defense
standard the rest of this project already applies everywhere else, not a
promise of a perfect filter.

## 3. Putting it together — the actual architecture, three layers, not one

This resolves the user's concern precisely: recency+trust-blend was never
meant to be the whole answer, and shouldn't be treated as one. The full
picture has three layers, each answering a different question, none of
them a single point of failure:

```
Layer 1 — CORROBORATION (already designed, STALE_INFRA_DEFICIT_RESEARCH.md)
  Answers: "did enough independent people say the same thing, recently?"
  Covers: every category, including the ones no photo can ever verify
  (staffing absence, water pressure, anything that isn't a visible object).
  Mechanism: burst detection + record-freshness trust blend.

Layer 2 — IMAGE AUTHENTICITY (new, this doc §2)
  Answers: "is this specific photo genuine — not edited, not reused,
  not stolen from the web, not AI-generated?"
  Covers: any complaint with a photo attached, any category.
  Mechanism: EXIF/device metadata + local ELA (free) + imagehash duplicate
  check (already decided) + reverse image search (paid, optional) +
  AI-image-detection flag (advisory only, routes to human review, never
  auto-rejects — consistent with the earlier session's own decision).

Layer 3 — DEFECT CLASSIFICATION (new, this doc §1)
  Answers: "given a genuine photo, does it actually show a real pothole/
  crack, not an unrelated or normal-condition photo?"
  Covers: roads (RDD2022/YOLOv8 — real, benchmarked, ~77% F1 ceiling) and
  visible structural cracks (SDNET2018-trained CNN — high accuracy, but
  answers "is there a crack," not "is the building safe"). Does NOT cover
  staffing absence (no image exists to classify) or whole-building safety
  assessment (no standard ground-level model exists for this yet).
```

**Critical design rule, matching this project's own existing philosophy
in `scoring.py`**: Layer 3's model output (a 0.67–0.77 F1 classifier) must
feed the score as a **confidence input that dampens or boosts**, exactly
like the existing `confidence_gate` — never a binary "photo verified /
rejected" gate. A quarter of the time the best published model disagrees
with reality; treating it as an oracle would import a new, hidden failure
mode into a system whose whole design principle is "nothing is treated as
absolute ground truth."

## 4. What this means for scope and effort — stated plainly

This is a materially bigger lift than anything built in this project so
far. Everything to date — clustering, scoring, the trust-blend design — is
rule-based/statistical, running in plain Python with no model-serving
infrastructure. Layers 2 and 3 need:

- An actual model-inference step (a small local service or an in-process
  call — YOLOv8n's size makes CPU inference realistic, no GPU needed for
  single-image, non-real-time inference at this project's volume).
- Either a pretrained RDD2022/SDNET2018 model used as-is, or fine-tuning
  on this project's own submitted photos once there's enough volume to do
  so credibly.
- A decision on the paid reverse-image-search API (cost, and whether it's
  worth it given it only ever helps with the "stolen from the open web"
  fraud pattern, not the more likely "genuine photo, wrong location" or
  "no photo at all" cases Layer 1 already covers).

None of this is built. This doc, like the two before it, is research and
design only.

## Sources

- [arXiv: RDD2022 — multi-national road damage dataset](https://arxiv.org/abs/2209.08538)
- [Wiley Geoscience Data Journal: RDD2022](https://rmets.onlinelibrary.wiley.com/doi/10.1002/gdj3.260)
- [IEEE: YOLOv8-based pothole detection](https://ieeexplore.ieee.org/document/10465038/)
- [arXiv: Enhanced YOLOv8 pothole detection and measurement](https://arxiv.org/abs/2505.04207)
- [RoadBotics deployment — IEEE Spectrum](https://spectrum.ieee.org/roadbotics-ai-could-change-the-way-cities-maintain-roads)
- [RoadBotics — Carnegie Mellon Robotics Institute](https://www.ri.cmu.edu/roadbotics-ai-could-change-the-way-cities-maintain-roads/)
- [SDNET2018 dataset paper](https://www.researchgate.net/publication/328771609_SDNET2018_An_annotated_image_dataset_for_non-contact_concrete_crack_detection_using_deep_convolutional_neural_networks)
- [Lite-V2 lightweight crack detection on SDNET2018](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11243917/)
- [xBD building damage dataset (xView2)](https://www.tandfonline.com/doi/full/10.1080/17538947.2024.2302577)
- [Carpe: detecting AI-generated photos in insurance claims](https://carpe.io/resources/blog/how-to-detect-ai-generated-photos-in-insurance-claims/)
- [VAARHAFT: insurance image fraud detection guide](https://www.vaarhaft.com/blog/insurance)
- [Ethos Risk: forensic verification in claims investigations](https://ethosrisk.com/blog/forensic-verification-in-claims-investigations/)
