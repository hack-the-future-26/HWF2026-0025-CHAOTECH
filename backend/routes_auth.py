"""
Citizen Authentication & Grievance Tracking API.

Provides registration, login, session management, and the "My Complaints"
tracking portal for citizens filing grievances on AwaazIQ.
"""

import hashlib
import re
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import (
    CitizenIdentity,
    CitizenRequest,
    CitizenUser,
    DemandCluster,
    PriorityScore,
    ReportAttachment,
    UserSession,
)

router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
PINCODE_RE = re.compile(r"^\d{6}$")
GENDERS = {"male", "female", "transgender"}


# ----------------------------------------------------------- password utils --

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 100000
    )
    return f"{salt}${key.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, key = hashed.split("$", 1)
        test_key = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), 100000
        )
        return secrets.compare_digest(test_key.hex(), key)
    except Exception:
        return False


def serialize_user(user: CitizenUser) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "gender": user.gender,
        "mobile": user.mobile,
        "address": user.address,
        "pincode": user.pincode,
        "state": user.state,
        "district": user.district,
        "block": user.block,
        "village": user.village,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


# ------------------------------------------------------------- auth helpers --

def get_current_user_from_token(token: str | None, db: Session) -> CitizenUser | None:
    if not token:
        return None
    session = db.query(UserSession).filter(UserSession.token == token).first()
    if not session:
        return None
    return db.query(CitizenUser).filter(CitizenUser.id == session.user_id).first()


def get_current_user(
    request: Request,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> CitizenUser:
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    if not token:
        token = request.query_params.get("token")

    user = get_current_user_from_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required. Please sign in.")
    return user


def get_optional_user(
    request: Request,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> CitizenUser | None:
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    if not token:
        token = request.query_params.get("token")
    return get_current_user_from_token(token, db)


# ------------------------------------------------------------ request schemas --

class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str
    gender: str
    mobile: str
    address: str
    pincode: str
    state: str = "Maharashtra"
    district: str | None = None
    block: str | None = None
    village: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


# ---------------------------------------------------------------- endpoints --

@router.post("/auth/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    email = req.email.strip().lower()
    full_name = req.full_name.strip()
    mobile = req.mobile.strip()
    gender = req.gender.strip().lower()
    pincode = req.pincode.strip()
    address = req.address.strip()
    password = req.password

    errors = []
    if not full_name:
        errors.append("Full name is required")
    if not email or not EMAIL_RE.match(email):
        errors.append("A valid email address is required")
    if len(password) < 6:
        errors.append("Password must be at least 6 characters")
    if not mobile or not MOBILE_RE.match(mobile):
        errors.append("Mobile number must be 10 digits starting with 6-9")
    if gender not in GENDERS:
        errors.append("Gender must be male, female, or transgender")
    if not pincode or not PINCODE_RE.match(pincode):
        errors.append("Pincode must be 6 digits")
    if not address:
        errors.append("Address is required")

    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    existing = db.query(CitizenUser).filter(CitizenUser.email == email).first()
    if existing:
        raise HTTPException(
            status_code=400, detail="An account with this email already exists"
        )

    user = CitizenUser(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        gender=gender,
        mobile=mobile,
        address=address,
        pincode=pincode,
        state=req.state or "Maharashtra",
        district=req.district,
        block=req.block,
        village=req.village,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = secrets.token_urlsafe(32)
    session = UserSession(token=token, user_id=user.id)
    db.add(session)
    db.commit()

    return {
        "token": token,
        "user": serialize_user(user),
        "message": "Account created successfully",
    }


@router.post("/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    email = req.email.strip().lower()
    user = db.query(CitizenUser).filter(CitizenUser.email == email).first()

    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=401, detail="Invalid email address or password"
        )

    token = secrets.token_urlsafe(32)
    session = UserSession(token=token, user_id=user.id)
    db.add(session)
    db.commit()

    return {
        "token": token,
        "user": serialize_user(user),
        "message": "Signed in successfully",
    }


@router.get("/auth/me")
def get_me(user: CitizenUser = Depends(get_current_user)):
    return {"user": serialize_user(user)}


@router.post("/auth/logout")
def logout(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        db.query(UserSession).filter(UserSession.token == token).delete()
        db.commit()
    return {"status": "ok", "message": "Signed out successfully"}


@router.get("/citizen/my-reports")
def get_my_reports(
    user: CitizenUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns all grievances filed by the authenticated citizen, ordered by most recent,
    with their live cluster status and rank.
    """
    reports = (
        db.query(CitizenRequest)
        .filter(CitizenRequest.user_id == user.id)
        .order_by(CitizenRequest.created_at.desc())
        .all()
    )

    results = []
    for r in reports:
        # Determine status
        status = "awaiting_corroboration"
        cluster_info = None

        if r.village is None:
            status = "unresolved"
        elif r.cluster_id is not None:
            status = "clustered"
            cl = db.query(DemandCluster).filter(DemandCluster.id == r.cluster_id).first()
            if cl:
                latest_score = (
                    db.query(PriorityScore)
                    .filter(PriorityScore.cluster_id == cl.id)
                    .order_by(PriorityScore.computed_at.desc())
                    .first()
                )
                cluster_info = {
                    "cluster_id": cl.id,
                    "title": cl.title,
                    "priority_rank": latest_score.rank if latest_score else None,
                    "priority_score": latest_score.final_score if latest_score else None,
                }

        attachments_count = (
            db.query(ReportAttachment)
            .filter(ReportAttachment.linked_request_id == r.id)
            .count()
        )

        results.append({
            "id": r.id,
            "raw_text": r.raw_text,
            "issue_category": r.issue_category,
            "severity": r.severity,
            "department": r.department,
            "district": r.district,
            "block": r.block,
            "village": r.village,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "status": status,
            "cluster": cluster_info,
            "attachments_count": attachments_count,
        })

    return {"reports": results, "count": len(results)}
