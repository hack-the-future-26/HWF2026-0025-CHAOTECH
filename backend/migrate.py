"""
Additive column migrations for the SQLite database.

`Base.metadata.create_all` creates missing *tables* but never alters existing
ones, so a column added to a model that already has rows in the database is
silently absent at runtime -- every read of it fails with "no such column".
This module closes that gap for the columns we have added since the schema
first shipped.

Deliberately additive only. Nothing here drops or rewrites a column, so it is
safe to run on every startup and safe to run twice.
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine

# table -> column -> SQLite column definition
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "citizen_request": {
        "cluster_id": "INTEGER",
        # Line-department routing, added with the CPGRAMS-level intake form.
        "department": "TEXT",
        # Registered citizen who filed the report.
        "user_id": "INTEGER",
    },
    "demand_cluster": {
        "district": "TEXT",
        "block": "TEXT",
        "avg_confidence": "FLOAT",
        "settlement_count": "INTEGER",
    },
    "village_amenities": {
        "conn_bharatnet_status": "TEXT",
        "conn_bharatnet_gp": "TEXT",
        "conn_bharatnet_distance_km": "FLOAT",
        "jjm_households": "INTEGER",
        "jjm_households_with_tap": "INTEGER",
        "jjm_tap_coverage_pct": "FLOAT",
        "jjm_habitation_count": "INTEGER",
        "jjm_quality_status": "TEXT",
    },
    "work_group": {
        # The named asset a work group is about, added with the UDISE school
        # and PMGSY work registers.
        "asset_label": "TEXT",
        "asset_source": "TEXT",
        "asset_external_id": "TEXT",
        "asset_candidates": "TEXT",
    },
    "priority_score": {
        # The working behind each score term, so a number can be interrogated
        # rather than only read.
        "evidence": "TEXT",
    },
}


def existing_columns(connection, table: str) -> set[str]:
    rows = connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def run_migrations(engine: Engine) -> list[str]:
    """Add any missing columns. Returns what it changed, for logging."""
    applied: list[str] = []

    with engine.begin() as connection:
        for table, columns in ADDED_COLUMNS.items():
            # A table that does not exist yet will be built by create_all with
            # the column already present, so there is nothing to migrate.
            if not connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
                {"t": table},
            ).fetchone():
                continue

            present = existing_columns(connection, table)
            for column, ddl in columns.items():
                if column not in present:
                    connection.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                    )
                    applied.append(f"{table}.{column}")

    return applied
