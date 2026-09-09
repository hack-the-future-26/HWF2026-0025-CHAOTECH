"""
POST /citizen-report — the real ingestion endpoint. Accepts either typed
text or an uploaded audio file, runs it through the full NLP pipeline
(pipeline.main.process_report), redacts PII from the text before storing
it, and keeps the untouched original in a separate table.

Two request shapes, deliberately:

  JSON  {"text": "..."}          the ingest path. No identity. This is what
                                 the seeding scripts and the endpoint tests
                                 use, and it is unchanged.

  multipart/form-data            the citizen intake form. Carries the
                                 CPGRAMS-level field set -- who is
                                 complaining, where exactly, which
                                 department, and any evidence files. Sending
                                 `full_name` switches this validation on, and
                                 then the whole mandatory set is required.

Identity never lands in `citizen_request`. It goes to `citizen_identity`,
which nothing in the analytics or dashboard path reads. See models.py.
"""

import re
import secrets
import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.main import process_report  # noqa: E402

from database import get_db  # noqa: E402
from models import (  # noqa: E402
    CitizenIdentity,
    CitizenRequest,
    CitizenRequestRaw,
    Gazetteer,
    ReportAttachment,
)
from routes_auth import get_current_user_from_token  # noqa: E402
from routes_gazetteer import PILOT_STATE, valid_department  # noqa: E402

router = APIRouter()

# ---------------------------------------------------------------- uploads --

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"

# CPGRAMS caps attachments at 4 MB each and 5 files per grievance; we match it.
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024

# Photographs are the evidence a villager actually has, plus PDF for anyone
# forwarding an official letter. Nothing executable, and the extension we
# write is derived from this map -- never from the uploader's filename.
ALLOWED_ATTACHMENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "application/pdf": ".pdf",
}

GENDERS = {"male", "female", "transgender"}
MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
PINCODE_RE = re.compile(r"^\d{6}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Indian mobile numbers: 10 digits, starting 6-9.
PHONE_RE = re.compile(r"\b[6-9]\d{9}\b")

# Common name-prefixes followed by a capitalized word, e.g. "Mr. Sharma",
# "Shri Kumar".
NAME_PREFIX_RE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Dr|Shri|Smt)\.?\s+[A-Z][a-zA-Z]*\b"
)


# Self-identification, which is how a citizen actually signs a complaint:
# "mera naam Ravi Patil", "my name is Ravi", "माझं नाव रवी". The possessive is
# required on purpose -- matching a bare "naam"/"नाव" would also eat
# "गावाचे नाव Bidri" ("the village's name is Bidri"), and the village name is
# the one piece of the text the clustering embedding genuinely needs to keep.
SELF_NAME_RE = re.compile(
    r"((?i:\b(?:mera|meraa|maza|majha|mazha|my)\b)\s+"
    r"|मेरा\s+|मेरे\s+|माझं\s+|माझे\s+|माझ्या\s+)"
    r"((?i:\b(?:naam|naav|nam|name)\b)|नाम|नाव)"
    r"((?i:\s+(?:is|hai|aahe|ahe))|\s+है|\s+आहे)?\s+"
    # One or two name tokens. The lookahead stops the second token from
    # eating the sentence's verb, so "\u092E\u093E\u091D\u0902 \u0928\u093E\u0935 \u0930\u0935\u0940 \u0906\u0939\u0947" redacts the name and
    # keeps the "\u0906\u0939\u0947".
    r"(?:[A-Z][a-zA-Z]*|(?!\u0906\u0939\u0947|\u0906\u0939\u0947\u0924|\u0939\u0948|\u0939\u0948\u0902)[\u0900-\u097F]+)"
    r"(?:\s+(?:[A-Z][a-zA-Z]*|(?!\u0906\u0939\u0947|\u0906\u0939\u0947\u0924|\u0939\u0948|\u0939\u0948\u0902)[\u0900-\u097F]+))?"
)


def redact_pii(text: str) -> str:
    """
    Strip the two identifiers a citizen complaint realistically carries: an
    Indian mobile number, and a name the writer states about themselves --
    either after a title ("Shri Kumar") or after a possessive ("mera naam
    Ravi Patil", "my name is Ravi").

    This is deliberately not a general-purpose name detector; no regex can be
    one. It removes the forms people actually write.

    Note what this is now for. Identity IS collected -- CitizenIdentity holds
    the name, mobile and address the intake form requires. Redaction is about
    the *analytical* copy: `raw_text` is what clustering embeds and what the
    officials' dashboard renders, so a name left inside it would travel into
    a screen that is supposed to show what was reported and not by whom. The
    untouched original stays in citizen_request_raw.
    """
    redacted = PHONE_RE.sub("[PHONE]", text)
    redacted = NAME_PREFIX_RE.sub("[NAME]", redacted)
    redacted = SELF_NAME_RE.sub(r"\1\2\3 [NAME]", redacted)
    return redacted


_gazetteer_cache: list[dict] | None = None
_gazetteer_cache_count: int | None = None


def _load_gazetteer_rows(db: Session) -> list[dict]:
    """Gazetteer rows for geocoding, cached across requests.

    This used to materialise all ~1000 gazetteer rows from the database on
    every single POST /citizen-report. The gazetteer is static reference
    data during normal operation, so it's cached and re-read only when the
    row count changes (i.e. someone re-ran load_gazetteer.py) -- one cheap
    COUNT(*) per request instead of a full table load.

    Caveat: in-place edits that don't change the row count won't invalidate
    the cache. That's fine today because the only such writer is
    load_demographic_data.py, which updates `population`, and geocoding
    never reads that column -- but restart the server if that changes.
    """
    global _gazetteer_cache, _gazetteer_cache_count

    count = db.query(Gazetteer).count()
    if _gazetteer_cache is None or count != _gazetteer_cache_count:
        # Keys must be "lat"/"lon" -- that is what pipeline.geocode reads, and
        # what the build plan's Interface Contract (Section 2) specifies for
        # location_resolved. Naming them latitude/longitude here silently threw
        # away every coordinate: geocode's row.get("lat") returned None for all
        # 1,000+ reports, leaving /map-data permanently empty.
        _gazetteer_cache = [
            {
                "name": row.name,
                "district": row.district,
                "block": row.block,
                "lat": row.latitude,
                "lon": row.longitude,
            }
            for row in db.query(Gazetteer).all()
        ]
        _gazetteer_cache_count = count

    return _gazetteer_cache


def serialize_citizen_request(citizen_request: CitizenRequest) -> dict:
    return {
        "id": citizen_request.id,
        "raw_text": citizen_request.raw_text,
        "language_detected": citizen_request.language_detected,
        "issue_category": citizen_request.issue_category,
        "severity": citizen_request.severity,
        "location_raw": citizen_request.location_raw,
        "district": citizen_request.district,
        "block": citizen_request.block,
        "village": citizen_request.village,
        "latitude": citizen_request.latitude,
        "longitude": citizen_request.longitude,
        "confidence": citizen_request.confidence,
        "is_synthetic": citizen_request.is_synthetic,
        # Routing, not identity: which line department owns this kind of
        # asset. Safe for the dashboard, unlike anything in citizen_identity.
        "department": citizen_request.department,
        # Which demand cluster this report was folded into, once P3 has run.
        # This is what powers the citizen feedback loop: "your report
        # contributed to Cluster N, currently ranked #M".
        "cluster_id": citizen_request.cluster_id,
        "created_at": citizen_request.created_at,
    }


def _clean(form, key: str) -> str | None:
    value = form.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def validate_intake(form, user=None) -> dict:
    """
    Validate the CPGRAMS-level field set and return it normalised.

    Identity comes from the signed-in account, not from the form. A citizen
    who has already registered their name, gender, mobile, e-mail, address
    and pincode should never be asked to type them again to file their second
    complaint -- retyping is not verification, and every retyped field is
    another chance for the same person to be recorded under two spellings and
    counted as two corroborating voices.

    So when `user` is present those six fields are read off the account and
    the form's own copies are ignored outright. What the form still has to
    supply is what changes per complaint: which village, and which department.

    `user` is None only on the legacy path where a form posts identity
    directly; that path still validates every field, so nothing that worked
    before stops working.

    Every message names the field and says what is wrong with it, because a
    form that just says "invalid input" is a form people give up on.
    """
    errors: list[str] = []

    if user is not None:
        full_name = user.full_name
        gender = (user.gender or "").lower() or None
        mobile = user.mobile
        email = user.email
        address = user.address
        pincode = user.pincode
        state = user.state or PILOT_STATE
    else:
        full_name = _clean(form, "full_name")
        gender = (_clean(form, "gender") or "").lower() or None
        mobile = _clean(form, "mobile")
        email = _clean(form, "email")
        address = _clean(form, "address")
        pincode = _clean(form, "pincode")
        state = _clean(form, "state") or PILOT_STATE
    district = _clean(form, "district")
    block = _clean(form, "block")
    village = _clean(form, "village")
    department = _clean(form, "department")

    if not full_name:
        errors.append("Name is required")
    if gender not in GENDERS:
        errors.append("Gender must be male, female or transgender")
    if not mobile or not MOBILE_RE.match(mobile):
        errors.append("Mobile must be 10 digits starting 6-9")
    if not email or not EMAIL_RE.match(email):
        errors.append("A valid e-mail address is required")
    if not address:
        errors.append("Address is required")
    if not pincode or not PINCODE_RE.match(pincode):
        errors.append("Pincode must be 6 digits")
    if not district:
        errors.append("District is required")
    if not block:
        errors.append("Block is required")
    if not village:
        errors.append("Village is required")
    if not valid_department(department):
        errors.append("Unknown department")

    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    return {
        "full_name": full_name,
        "gender": gender,
        "mobile": mobile,
        "email": email,
        "address": address,
        "pincode": pincode,
        "state": state,
        "district": district,
        "block": block,
        "village": village,
        "department": department,
    }


def resolve_picked_location(db: Session, district: str, block: str, village: str) -> dict:
    """
    Coordinates for a village the citizen chose from the dropdowns.

    Picked locations beat anything extracted from free text: the citizen knows
    where they live, and the dropdown only ever offers villages the gazetteer
    already contains, so this cannot produce a place the pipeline can't place.
    A report that resolves here can never end up permanently unclusterable
    because its location was a string nobody could match.
    """
    row = (
        db.query(Gazetteer)
        .filter(
            Gazetteer.district == district,
            Gazetteer.block == block,
            Gazetteer.name == village,
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=400,
            detail=f"{village} is not a known village in {block}, {district}",
        )
    return {
        "district": row.district,
        "block": row.block,
        "village": row.name,
        "lat": row.latitude,
        "lon": row.longitude,
    }


async def save_attachments(form, request_id: int, db: Session) -> list[ReportAttachment]:
    """Persist evidence files, enforcing the CPGRAMS limits."""
    uploads = [
        f for f in form.getlist("attachments")
        if f is not None and hasattr(f, "read")
    ]
    if not uploads:
        return []

    if len(uploads) > MAX_ATTACHMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_ATTACHMENTS} files may be attached",
        )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[ReportAttachment] = []

    for upload in uploads:
        content_type = (upload.content_type or "").split(";")[0].strip().lower()
        if content_type not in ALLOWED_ATTACHMENT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"{upload.filename or 'file'}: only photos and PDFs are accepted",
            )

        data = await upload.read()
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"{upload.filename or 'file'} is larger than 4 MB",
            )
        if not data:
            continue

        # Named from a random token plus the extension implied by the declared
        # content type. The uploader's filename is stored for display only and
        # never touches the path, so "../../x" cannot escape UPLOAD_DIR.
        stored_name = f"{secrets.token_hex(16)}{ALLOWED_ATTACHMENT_TYPES[content_type]}"
        (UPLOAD_DIR / stored_name).write_bytes(data)

        row = ReportAttachment(
            linked_request_id=request_id,
            original_filename=(upload.filename or "")[:200],
            stored_name=stored_name,
            content_type=content_type,
            size_bytes=len(data),
        )
        db.add(row)
        saved.append(row)

    return saved


@router.post("/citizen-report")
async def create_citizen_report(request: Request, db: Session = Depends(get_db)):
    content_type = request.headers.get("content-type", "")
    tmp_path = None
    intake = None
    form = None

    # A form POST carrying files is multipart; the same form with no file
    # attached is urlencoded. Matching only on multipart sent every
    # attachment-less submission down the JSON branch, where it failed with
    # "Body must be JSON" and the field validation below never ran at all.
    is_form = (
        "multipart/form-data" in content_type
        or "application/x-www-form-urlencoded" in content_type
    )

    try:
        user = None
        auth_header = request.headers.get("authorization")
        token = None
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()

        if is_form:
            form = await request.form()
            if not token:
                token = _clean(form, "token") or _clean(form, "auth_token")

            if token:
                user = get_current_user_from_token(token, db)

            # What marks a submission as the full citizen intake rather than
            # the bare {"text": ...} path. `village` and `department` are the
            # fields the form always sends now that identity is read off the
            # account instead of retyped -- keying this on `full_name` alone
            # would silently downgrade every signed-in grievance to an
            # anonymous text report, losing its department, its chosen
            # village, and its link to the citizen.
            if _clean(form, "department") or _clean(form, "village") or _clean(form, "full_name"):
                if not user:
                    raise HTTPException(
                        status_code=401,
                        detail="Please sign in or create an account to file a grievance",
                    )
                intake = validate_intake(form, user)

            text = _clean(form, "text")
            upload = form.get("file")
            has_audio = upload is not None and hasattr(upload, "read")

            if has_audio:
                suffix = Path(upload.filename or "").suffix or ".wav"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(await upload.read())
                    tmp_path = tmp.name
                input_data = {"audio_path": tmp_path}
            elif text:
                input_data = {"text": text}
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Describe the problem, or attach an audio recording",
                )
        else:
            # Previously an unparseable or empty body raised straight out of
            # request.json() as a 500; a malformed request is the client's
            # error, so report it as one.
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail='Body must be JSON of the form {"text": "..."}',
                )

            if not isinstance(body, dict):
                raise HTTPException(
                    status_code=400,
                    detail='Body must be a JSON object of the form {"text": "..."}',
                )

            text = body.get("text")
            if not isinstance(text, str) or not text.strip():
                raise HTTPException(
                    status_code=400, detail="No text provided"
                )
            input_data = {"text": text}

        gazetteer_rows = _load_gazetteer_rows(db)
        result = process_report(input_data, gazetteer_rows)

        original_text = result["raw_text"]
        redacted_text = redact_pii(original_text)
        location_resolved = result.get("location_resolved") or {}
        location_raw = result["location_raw"]
        confidence = result["confidence_overall"]

        if intake:
            # A village chosen from the dropdown outranks one guessed from the
            # text. This is the fix for reports that used to be stored with no
            # coordinates at all and could therefore never join a cluster.
            location_resolved = resolve_picked_location(
                db, intake["district"], intake["block"], intake["village"]
            )
            # The picked village replaces whatever the extractor scraped out
            # of the sentence. Left as-is, a complaint that never named a
            # place stored fragments like "din se, bahut samasya" as its
            # location and showed that back to the citizen as "the place you
            # named" -- nonsense, and no longer needed for anything, since
            # coordinates now come from the dropdown.
            location_raw = intake["village"]
            # Location is no longer inferred, so it is no longer a source of
            # doubt. Language and category still are, so the pipeline's own
            # confidence is raised toward certainty rather than replaced by it.
            confidence = round(min(1.0, confidence + (1.0 - confidence) * 0.5), 2)

        citizen_request = CitizenRequest(
            raw_text=redacted_text,
            language_detected=result["language_detected"],
            issue_category=result["issue_category"],
            severity=result["severity"],
            location_raw=location_raw,
            district=location_resolved.get("district"),
            block=location_resolved.get("block"),
            village=location_resolved.get("village"),
            latitude=location_resolved.get("lat"),
            longitude=location_resolved.get("lon"),
            confidence=confidence,
            is_synthetic=result["is_synthetic"],
            department=intake["department"] if intake else None,
            user_id=user.id if user else None,
        )
        db.add(citizen_request)
        db.commit()
        db.refresh(citizen_request)

        db.add(
            CitizenRequestRaw(
                original_text=original_text,
                linked_request_id=citizen_request.id,
            )
        )

        attachments = []
        if intake:
            # Separate table, never joined from the analytics path.
            db.add(
                CitizenIdentity(
                    linked_request_id=citizen_request.id,
                    full_name=intake["full_name"],
                    gender=intake["gender"],
                    mobile=intake["mobile"],
                    email=intake["email"],
                    address=intake["address"],
                    pincode=intake["pincode"],
                    state=intake["state"],
                )
            )
            attachments = await save_attachments(form, citizen_request.id, db)

        db.commit()

        payload = serialize_citizen_request(citizen_request)
        payload["attachments"] = [
            {
                "id": a.id,
                "filename": a.original_filename,
                "size_bytes": a.size_bytes,
                "content_type": a.content_type,
            }
            for a in attachments
        ]
        # Echoed back so the receipt can confirm what was recorded. Read from
        # the validated input, not from the database -- nothing reads identity
        # back out of storage, including this endpoint.
        payload["filed_by"] = intake["full_name"] if intake else None
        return payload
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


@router.get("/report-attachment/{attachment_id}")
def download_attachment(attachment_id: int, db: Session = Depends(get_db)):
    """
    Stream one evidence file back.

    Evidence nobody can open is evidence that does not exist, so the officials
    who act on a cluster need to be able to see the photograph of the broken
    culvert. The lookup is by database id, and the path is rebuilt from the
    stored name we generated -- the uploader's filename never reaches the
    filesystem, so this cannot be walked out of UPLOAD_DIR.
    """
    row = (
        db.query(ReportAttachment)
        .filter(ReportAttachment.id == attachment_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    path = UPLOAD_DIR / row.stored_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file is missing")

    return FileResponse(
        path,
        media_type=row.content_type or "application/octet-stream",
        filename=row.original_filename or row.stored_name,
    )
