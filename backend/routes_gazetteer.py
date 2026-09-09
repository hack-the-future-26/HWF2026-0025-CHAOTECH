"""
Location lookups that back the complaint form's cascading dropdowns.

CPGRAMS makes a citizen pick country -> state -> district from dropdowns
rather than typing a place name, which is the difference between a location
you can act on and a string you have to guess at. This gives our form the
same thing, one level deeper (down to the village), fed from the same
gazetteer the geocoder already uses -- so anything a citizen can pick is by
construction something the pipeline can resolve.

That closes a real failure we could see in the data: a report whose location
never resolved can never join a cluster, and roughly a fifth of reports were
in that state.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import Gazetteer

router = APIRouter(prefix="/gazetteer")

# The pilot is two districts of one state. Saying so here keeps the form from
# offering a citizen a state we have no data for and then failing at submit.
PILOT_STATE = "Maharashtra"


@router.get("/states")
def list_states():
    return [{"name": PILOT_STATE, "active": True}]


@router.get("/districts")
def list_districts(db: Session = Depends(get_db)):
    rows = (
        db.query(Gazetteer.district)
        .filter(Gazetteer.district.isnot(None))
        .distinct()
        .order_by(Gazetteer.district)
        .all()
    )
    return [{"name": r[0]} for r in rows if r[0]]


@router.get("/blocks")
def list_blocks(district: str = Query(...), db: Session = Depends(get_db)):
    rows = (
        db.query(Gazetteer.block)
        .filter(Gazetteer.district == district, Gazetteer.block.isnot(None))
        .distinct()
        .order_by(Gazetteer.block)
        .all()
    )
    return [{"name": r[0]} for r in rows if r[0]]


@router.get("/villages")
def list_villages(
    district: str = Query(...),
    block: str = Query(...),
    q: str | None = Query(None, description="Optional name filter"),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    query = db.query(
        Gazetteer.name, Gazetteer.latitude, Gazetteer.longitude
    ).filter(
        Gazetteer.district == district,
        Gazetteer.block == block,
        Gazetteer.name.isnot(None),
    )
    if q:
        query = query.filter(func.lower(Gazetteer.name).like(f"%{q.lower()}%"))

    rows = query.order_by(Gazetteer.name).limit(limit).all()
    return [
        {"name": r[0], "lat": r[1], "lon": r[2]}
        for r in rows
        if r[0]
    ]


# ---------------------------------------------------------------------------
# Department routing
# ---------------------------------------------------------------------------

# CPGRAMS routes a grievance down Ministry -> Department -> Sub-organisation.
# At district level in Maharashtra the equivalent is the line department that
# actually holds the budget and the works file for that kind of asset, which
# is what makes routing useful rather than decorative: each of these is also
# the owner of the scheme our Priority Engine scores the cluster against.
DEPARTMENTS = {
    "road": [
        {
            "id": "pwd",
            "name": "Public Works Department (PWD)",
            "note": "District and state roads, bridges, culverts",
        },
        {
            "id": "zp-works",
            "name": "Zilla Parishad — Works Department",
            "note": "Village roads, internal roads, PMGSY connectivity",
        },
    ],
    "water": [
        {
            "id": "zp-water",
            "name": "Zilla Parishad — Water Supply & Sanitation",
            "note": "Village water supply, Jal Jeevan Mission tap connections",
        },
        {
            "id": "mjp",
            "name": "Maharashtra Jeevan Pradhikaran",
            "note": "Regional and piped water schemes",
        },
    ],
    "health": [
        {
            "id": "zp-health",
            "name": "Zilla Parishad — Health Department",
            "note": "Sub-centres, primary health centres, ASHA services",
        },
        {
            "id": "dho",
            "name": "District Health Office",
            "note": "Rural and sub-district hospitals, staffing",
        },
    ],
    "education": [
        {
            "id": "zp-education",
            "name": "Zilla Parishad — Education Department",
            "note": "Primary and upper-primary schools, teacher posting",
        },
        {
            "id": "deo",
            "name": "District Education Office",
            "note": "Secondary schools, RTE compliance",
        },
    ],
}


@router.get("/departments")
def list_departments(category: str | None = Query(None)):
    """
    Departments for one issue category, or all of them.

    Returned keyed by category so the form can re-filter without another
    round-trip when the citizen changes their mind about the problem type.
    """
    if category:
        return {category: DEPARTMENTS.get(category, [])}
    return DEPARTMENTS


def valid_department(department_id: str | None) -> bool:
    if not department_id:
        return True                      # optional field
    return any(
        d["id"] == department_id
        for group in DEPARTMENTS.values()
        for d in group
    )
