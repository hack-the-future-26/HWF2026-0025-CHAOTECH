import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Anchored to this file's directory, NOT the working directory. The previous
# default ("sqlite:///./hackathon.db") resolved relative to wherever the
# process happened to start, so running anything from the repo root instead
# of backend/ silently created and used a second, empty database -- which
# looks exactly like "all my data disappeared". P3 runs as a package from the
# repo root, so this had to be fixed before it could read P2's data at all.
_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "hackathon.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
