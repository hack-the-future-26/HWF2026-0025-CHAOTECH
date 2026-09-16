"""
Workstream C endpoints and the glue that runs photo checks on a filed report.

  check_report_photos()                 called by POST /citizen-report
  POST /photo-checks/analyze            Photo Lab: run every check, store nothing
  GET  /photo-checks/model              model details + measured accuracy
  GET  /photo-checks/report/{id}        checks for one report (no coordinates)
  GET  /report-attachment/{id}/annotated  the photo with detected defects boxed
  GET  /report-attachment/{id}/ela        amplified error-level image (C4)

Privacy follows the rest of the API: exact capture coordinates are stored but
never returned; responses carry the distance to the village instead.
"""

from __future__ import annotations

import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

import photo_checks as pc
from database import get_db
from models import CitizenRequest, PhotoCheck, ReportAttachment

router = APIRouter()

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
EVAL_PATH = pc.REPO_ROOT / "models" / "evaluation.json"

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_BURST_FRAMES = 5
MAX_BURST_BYTES = 1024 * 1024
MAX_LAB_BYTES = 8 * 1024 * 1024


# ---------------------------------------------------------------- helpers --

def _existing_hashes(db: Session, exclude_request_id: int | None = None) -> list[dict]:
    rows = (
        db.query(PhotoCheck.attachment_id, PhotoCheck.linked_request_id, PhotoCheck.phash,
                 PhotoCheck.dhash, CitizenRequest.village, CitizenRequest.user_id)
        .join(CitizenRequest, CitizenRequest.id == PhotoCheck.linked_request_id)
        .filter(PhotoCheck.phash.isnot(None))
        .all()
    )
    return [
        {"attachment_id": a, "request_id": r, "phash": p, "dhash": d, "village": v, "user_id": u}
        for a, r, p, d, v, u in rows
        if exclude_request_id is None or r != exclude_request_id
    ]


def _parse_capture_meta(form) -> list:
    raw = form.get("capture_meta") if form is not None else None
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    return value if isinstance(value, list) else []


async def _burst_frames(form, index: int) -> list[bytes]:
    frames = []
    for f in form.getlist(f"burst_{index}")[:MAX_BURST_FRAMES]:
        if hasattr(f, "read"):
            data = await f.read()
            if data and len(data) <= MAX_BURST_BYTES:
                frames.append(data)
    return frames


def category_for_department(department_id: str | None) -> str | None:
    """
    The problem type the citizen chose, recovered from the department they
    routed to. Used when the text pipeline could not classify the description
    (e.g. Marathi typed in Latin script), so a road photo is still judged as a
    road photo -- and, in routes_citizen_report.create_citizen_report, so the
    report itself gets a real issue_category instead of one a text
    classifier failed to produce.
    """
    from routes_gazetteer import DEPARTMENTS

    for category, departments in DEPARTMENTS.items():
        if any(d["id"] == department_id for d in departments):
            return category
    return None


def serialize_check(row: PhotoCheck) -> dict:
    """Officer/citizen view of one stored check. No coordinates, no hashes."""
    result = json.loads(row.result) if row.result else {}
    view = pc.public_view(result) if result else {}
    view.update({
        "id": row.id,
        "attachment_id": row.attachment_id,
        "request_id": row.linked_request_id,
        "photo_url": f"/report-attachment/{row.attachment_id}",
        "annotated_url": f"/report-attachment/{row.attachment_id}/annotated" if row.annotated_name else None,
        "ela_url": f"/report-attachment/{row.attachment_id}/ela",
    })
    return view


async def check_report_photos(
    form,
    attachments: list[ReportAttachment],
    citizen_request: CitizenRequest,
    *,
    village_lat: float | None,
    village_lon: float | None,
    text: str | None,
    db: Session,
) -> list[dict]:
    """
    Run C1, C3, C4, C5, C6 and C7 on every photo just saved for a report,
    store the results, and nudge the report's confidence. Never raises for a
    bad photo: a check that cannot run is recorded as such.
    """
    images = [a for a in attachments if (a.content_type or "") in IMAGE_TYPES]
    if not images:
        return []

    metas = _parse_capture_meta(form)
    existing = _existing_hashes(db, exclude_request_id=citizen_request.id)
    category = citizen_request.issue_category or category_for_department(citizen_request.department)
    received_at = datetime.now(timezone.utc)
    views, summaries = [], []

    for att in images:
        index = getattr(att, "form_index", None)
        meta = metas[index] if index is not None and index < len(metas) and isinstance(metas[index], dict) else None
        burst = await _burst_frames(form, index) if index is not None else []
        data = (UPLOAD_DIR / att.stored_name).read_bytes()

        try:
            result, img = await run_in_threadpool(
                pc.analyze_photo,
                data,
                content_type=att.content_type,
                capture_meta=meta,
                burst=burst,
                village_lat=village_lat,
                village_lon=village_lon,
                village=citizen_request.village,
                user_id=citizen_request.user_id,
                category=category,
                text=text,
                existing_hashes=existing,
                received_at=received_at,
            )
        except Exception as exc:  # undecodable image: keep the report, say why
            result = {"error": f"photo could not be analysed: {type(exc).__name__}",
                      "summary": {"flags": [], "flag_text": {}, "review_flags": [],
                                  "authenticity": None, "support": None, "verdict": "not_checked"}}
            img = None

        annotated_name = None
        defects = result.get("defects") or {}
        if img is not None and defects.get("available"):
            annotated_name = f"{Path(att.stored_name).stem}_annotated.jpg"
            pc.draw_detections(img, defects.get("detections", [])).save(UPLOAD_DIR / annotated_name, "JPEG", quality=86)

        capture = result.get("capture") or {}
        damage = result.get("damage") or {}
        summary = result["summary"]
        row = PhotoCheck(
            attachment_id=att.id,
            linked_request_id=citizen_request.id,
            capture_method=capture.get("method"),
            captured_at=pc._parse_time(capture.get("captured_at")),
            capture_lat=capture.get("lat"),
            capture_lon=capture.get("lon"),
            capture_accuracy_m=capture.get("accuracy_m"),
            capture_distance_km=capture.get("distance_to_village_km"),
            phash=(result.get("hashes") or {}).get("phash"),
            dhash=(result.get("hashes") or {}).get("dhash"),
            duplicate_of_attachment_id=(result.get("duplicate") or {}).get("attachment_id"),
            ela_score=(result.get("edit_detection") or {}).get("score"),
            screen_replay_score=(result.get("screen_replay") or {}).get("score"),
            pothole_confidence=defects.get("pothole_confidence"),
            crack_confidence=defects.get("crack_confidence"),
            defect_seen=defects.get("defect_seen"),
            damage_grade=damage.get("grade"),
            damage_confidence=damage.get("confidence"),
            damage_structure=damage.get("structure"),
            authenticity=summary.get("authenticity"),
            verdict=summary.get("verdict"),
            flags=json.dumps(summary.get("flags", [])),
            annotated_name=annotated_name,
            result=pc.dumps(result),
        )
        db.add(row)
        db.flush()
        # Later photos in the same report are compared against earlier ones.
        if row.phash:
            existing.append({"attachment_id": att.id, "request_id": citizen_request.id, "phash": row.phash,
                             "dhash": row.dhash, "village": citizen_request.village,
                             "user_id": citizen_request.user_id})
        if summary.get("authenticity") is not None:
            summaries.append(summary)
        views.append(serialize_check(row))

    if summaries:
        citizen_request.photo_trust = min(s["authenticity"] for s in summaries)
        review = sorted({f for s in summaries for f in s["review_flags"]})
        citizen_request.review_flags = json.dumps(review)
        citizen_request.confidence = pc.confidence_adjustment(citizen_request.confidence, summaries)
    return views


# -------------------------------------------------------------- endpoints --

@router.post("/photo-checks/analyze")
async def analyze(request: Request):
    """
    Photo Lab. Upload one photo (field `photo`), optionally `burst` frames,
    `capture_meta` JSON, `category`, `text`, `village_lat`/`village_lon`.
    Every check runs; nothing is stored, nothing is compared with citizens'
    photos (the duplicate check is exercised by the intake path and tests).
    """
    form = await request.form()
    upload = form.get("photo")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="Attach a photo in the `photo` field")
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if content_type not in IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG or WebP photos can be analysed")
    data = await upload.read()
    if not data or len(data) > MAX_LAB_BYTES:
        raise HTTPException(status_code=400, detail="Photo must be between 1 byte and 8 MB")

    meta = None
    if isinstance(form.get("capture_meta"), str):
        try:
            meta = json.loads(form.get("capture_meta"))
        except ValueError:
            meta = None
    burst = []
    for f in form.getlist("burst")[:MAX_BURST_FRAMES]:
        if hasattr(f, "read"):
            b = await f.read()
            if b and len(b) <= MAX_BURST_BYTES:
                burst.append(b)

    def num(key):
        v = form.get(key)
        try:
            f = float(v)
            return f if math.isfinite(f) else None
        except (TypeError, ValueError):
            return None

    try:
        result, img = await run_in_threadpool(
            pc.analyze_photo, data, content_type=content_type,
            capture_meta=meta if isinstance(meta, dict) else None, burst=burst,
            village_lat=num("village_lat"), village_lon=num("village_lon"),
            category=(form.get("category") or None), text=(form.get("text") or None),
            existing_hashes=[],
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read that image ({type(exc).__name__})")

    defects = result.get("defects") or {}
    view = pc.public_view(result)
    view["annotated_image"] = pc.to_data_url(pc.draw_detections(img, defects.get("detections", [])))
    ela = pc.ela_heatmap(img if max(img.size) <= 1600 else img.resize(
        (int(img.width * 1600 / max(img.size)), int(img.height * 1600 / max(img.size)))))
    view["ela_image"] = pc.to_data_url(ela, quality=80)
    return view


@router.get("/photo-checks/model")
def model_details():
    info = pc.model_info()
    try:
        info["evaluation"] = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info["evaluation"] = None
    return info


@router.get("/photo-checks/report/{report_id}")
def report_checks(report_id: int, db: Session = Depends(get_db)):
    report = db.query(CitizenRequest).filter(CitizenRequest.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
    rows = db.query(PhotoCheck).filter(PhotoCheck.linked_request_id == report_id).order_by(PhotoCheck.id).all()
    return {
        "report_id": report_id,
        "photo_trust": report.photo_trust,
        "review_flags": json.loads(report.review_flags) if report.review_flags else [],
        "checks": [serialize_check(r) for r in rows],
    }


def _attachment_or_404(db: Session, attachment_id: int) -> ReportAttachment:
    row = db.query(ReportAttachment).filter(ReportAttachment.id == attachment_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return row


@router.get("/report-attachment/{attachment_id}/annotated")
def annotated(attachment_id: int, db: Session = Depends(get_db)):
    _attachment_or_404(db, attachment_id)
    check = db.query(PhotoCheck).filter(PhotoCheck.attachment_id == attachment_id).first()
    if check is None or not check.annotated_name:
        raise HTTPException(status_code=404, detail="No annotated image for this attachment")
    path = UPLOAD_DIR / check.annotated_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Annotated image file is missing")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/report-attachment/{attachment_id}/ela")
def ela_image(attachment_id: int, db: Session = Depends(get_db)):
    row = _attachment_or_404(db, attachment_id)
    if (row.content_type or "") not in IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Error-level images exist only for photos")
    path = UPLOAD_DIR / row.stored_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file is missing")
    img = pc.open_image(path.read_bytes())
    if max(img.size) > 1600:
        img.thumbnail((1600, 1600))
    buf = io.BytesIO()
    pc.ela_heatmap(img).save(buf, "JPEG", quality=80)
    return Response(buf.getvalue(), media_type="image/jpeg")
