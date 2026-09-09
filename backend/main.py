import sys
from pathlib import Path

# The intelligence package (P3) lives at the repo root, one level above this
# file, so the API can import it whichever directory uvicorn was started from.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import models
from database import Base, engine
from migrate import run_migrations
from routes_auth import router as auth_router
from routes_gazetteer import router as gazetteer_router
from routes_citizen_report import router as citizen_report_router
from routes_dashboard import router as dashboard_router
from routes_intelligence import router as intelligence_router
from routes_test import router as test_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(citizen_report_router)
app.include_router(dashboard_router)
app.include_router(intelligence_router)
app.include_router(gazetteer_router)
app.include_router(test_router)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    # create_all adds missing tables but never alters existing ones, so a
    # column added to a model that already has rows would be missing at
    # runtime. See migrate.py.
    applied = run_migrations(engine)
    if applied:
        print(f"[migrate] added columns: {', '.join(applied)}")


@app.get("/")
def read_root():
    return {"status": "ok"}
