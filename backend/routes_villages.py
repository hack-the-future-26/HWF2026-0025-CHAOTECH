"""
Village & Asset Priority endpoints.

Serves the district village view, the village detail panel, and the specific
asset detail panel for prioritisation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import Asset, CitizenRequest, VillagePriority
from intelligence import config
from routes_citizen_report import serialize_citizen_request

router = APIRouter()


def _format_coord(coord: float | None, location_basis: str | None) -> float | None:
    """
    Privacy guard: for non-register assets (which can be a citizen's exact GPS
    pin), coordinates are rounded to ASSET_PUBLIC_COORD_DECIMALS (~110m).
    Register-based facilities keep public register coordinates.
    """
    if coord is None:
        return None
    if location_basis == "register_coordinates":
        return coord
    decimals = getattr(config, "ASSET_PUBLIC_COORD_DECIMALS", 3)
    return round(coord, decimals)


@router.get("/villages")
def list_villages(
    district: str | None = Query(None, description="District name filter"),
    db: Session = Depends(get_db),
):
    """
    District view: returns villages with their priority scores and highest-need asset.
    """
    query = db.query(VillagePriority)
    if district:
        query = query.filter(VillagePriority.district.ilike(district))

    rows = query.order_by(VillagePriority.rank_in_district.asc(), VillagePriority.priority_score.desc()).all()

    top_asset_ids = [r.top_asset_id for r in rows if r.top_asset_id is not None]
    assets_by_id = {}
    if top_asset_ids:
        assets = db.query(Asset).filter(Asset.id.in_(top_asset_ids)).all()
        assets_by_id = {a.id: a for a in assets}

    out = []
    for r in rows:
        top_asset = assets_by_id.get(r.top_asset_id)
        top_asset_data = None
        if top_asset:
            top_asset_data = {
                "id": top_asset.id,
                "name": top_asset.name,
                "asset_type": top_asset.asset_type,
                "priority_score": top_asset.priority_score,
            }
        counts_by_category = json.loads(r.counts_by_category) if r.counts_by_category else {}
        out.append({
            "gazetteer_id": r.gazetteer_id,
            "name": r.village,
            "block": r.block,
            "district": r.district,
            "lat": r.latitude,
            "lon": r.longitude,
            "priority_score": r.priority_score,
            "rank_in_district": r.rank_in_district,
            "report_count": r.report_count,
            "counts_by_category": counts_by_category,
            "asset_count": r.asset_count,
            "top_asset": top_asset_data,
            "is_demo": r.is_demo,
        })
    return out


@router.get("/villages/{gazetteer_id}")
def get_village(gazetteer_id: int, db: Session = Depends(get_db)):
    """
    Village detail: priority summary, ranked assets, top asset breakdown, and citizen reports.
    """
    vp = db.query(VillagePriority).filter(VillagePriority.gazetteer_id == gazetteer_id).first()
    if not vp:
        raise HTTPException(status_code=404, detail=f"Village {gazetteer_id} not found")

    village_reports = (
        db.query(CitizenRequest)
        .filter(
            CitizenRequest.village == vp.village,
            CitizenRequest.district == vp.district,
        )
        .order_by(CitizenRequest.created_at.desc())
        .all()
    )
    serialized_reports = [serialize_citizen_request(r) for r in village_reports]

    asset_ids = list({r.asset_id for r in village_reports if r.asset_id is not None})
    assets = []
    if asset_ids:
        assets = (
            db.query(Asset)
            .filter(Asset.id.in_(asset_ids))
            .order_by(Asset.priority_score.desc(), Asset.id.asc())
            .all()
        )

    assets_list = []
    for a in assets:
        ev = json.loads(a.evidence) if a.evidence else {}
        item = {
            "id": a.id,
            "name": a.name,
            "asset_type": a.asset_type,
            "priority_score": a.priority_score,
            "report_count": a.report_count,
            "name_basis": a.name_basis,
            "location_basis": a.location_basis,
            "lat": _format_coord(a.latitude, a.location_basis),
            "lon": _format_coord(a.longitude, a.location_basis),
            "is_demo": a.is_demo,
            "evidence": ev,
        }
        if "road_geometry" in ev:
            item["road_geometry"] = ev["road_geometry"]
        assets_list.append(item)

    top_asset = db.query(Asset).filter(Asset.id == vp.top_asset_id).first() if vp.top_asset_id else None
    top_asset_data = None
    if top_asset:
        top_ev = json.loads(top_asset.evidence) if top_asset.evidence else {}
        top_asset_data = {
            "id": top_asset.id,
            "name": top_asset.name,
            "asset_type": top_asset.asset_type,
            "priority_score": top_asset.priority_score,
            "name_basis": top_asset.name_basis,
            "location_basis": top_asset.location_basis,
            "lat": _format_coord(top_asset.latitude, top_asset.location_basis),
            "lon": _format_coord(top_asset.longitude, top_asset.location_basis),
            "breakdown": json.loads(top_asset.breakdown) if top_asset.breakdown else {},
            "evidence": top_ev,
            "is_demo": top_asset.is_demo,
        }
        if "road_geometry" in top_ev:
            top_asset_data["road_geometry"] = top_ev["road_geometry"]

    counts_by_category = json.loads(vp.counts_by_category) if vp.counts_by_category else {}

    return {
        "gazetteer_id": vp.gazetteer_id,
        "name": vp.village,
        "block": vp.block,
        "district": vp.district,
        "lat": vp.latitude,
        "lon": vp.longitude,
        "priority_score": vp.priority_score,
        "rank_in_district": vp.rank_in_district,
        "report_count": vp.report_count,
        "counts_by_category": counts_by_category,
        "asset_count": vp.asset_count,
        "is_demo": vp.is_demo,
        "assets": assets_list,
        "top_asset": top_asset_data,
        "reports": serialized_reports,
    }


@router.get("/assets/{asset_id}")
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    """
    Asset detail: 9-term priority breakdown, evidence, facts, provenance, and member reports.
    """
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")

    asset_reports = (
        db.query(CitizenRequest)
        .filter(CitizenRequest.asset_id == asset.id)
        .order_by(CitizenRequest.created_at.desc())
        .all()
    )
    serialized_reports = [serialize_citizen_request(r) for r in asset_reports]

    rank_in_village = 1
    if asset.village:
        v_asset_ids = (
            db.query(CitizenRequest.asset_id)
            .filter(
                CitizenRequest.village == asset.village,
                CitizenRequest.district == asset.district,
                CitizenRequest.asset_id.isnot(None),
            )
            .distinct()
            .all()
        )
        a_ids = [r[0] for r in v_asset_ids if r[0] is not None]
        if a_ids:
            v_assets = (
                db.query(Asset.id, Asset.priority_score)
                .filter(Asset.id.in_(a_ids))
                .order_by(Asset.priority_score.desc(), Asset.id.asc())
                .all()
            )
            for rank, (aid, _) in enumerate(v_assets, start=1):
                if aid == asset.id:
                    rank_in_village = rank
                    break

    ev = json.loads(asset.evidence) if asset.evidence else {}
    asset_data = {
        "id": asset.id,
        "asset_type": asset.asset_type,
        "name": asset.name,
        "name_basis": asset.name_basis,
        "facility_id": asset.facility_id,
        "source": asset.source,
        "external_id": asset.external_id,
        "lat": _format_coord(asset.latitude, asset.location_basis),
        "lon": _format_coord(asset.longitude, asset.location_basis),
        "location_basis": asset.location_basis,
        "primary_gazetteer_id": asset.primary_gazetteer_id,
        "village": asset.village,
        "block": asset.block,
        "district": asset.district,
        "villages_served": json.loads(asset.villages_served) if asset.villages_served else [],
        "report_count": asset.report_count,
        "distinct_reporters": asset.distinct_reporters,
        "priority_score": asset.priority_score,
        "rank_in_village": rank_in_village,
        "breakdown": json.loads(asset.breakdown) if asset.breakdown else {},
        "evidence": ev,
        "candidates": json.loads(asset.candidates) if asset.candidates else None,
        "is_demo": asset.is_demo,
        "created_at": asset.created_at,
        "reports": serialized_reports,
    }
    if "road_geometry" in ev:
        asset_data["road_geometry"] = ev["road_geometry"]
    return asset_data
