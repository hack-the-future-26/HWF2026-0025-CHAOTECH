"""
Creates the tables, and adds any column the models declare that an existing
database is missing.

create_all() only ever CREATEs -- it will not ALTER a table that already
exists, so adding a column to models.py silently does nothing on a database
that has already been built. P3 added five such columns
(citizen_request.cluster_id, demand_cluster.district/block/avg_confidence/
settlement_count), which is what this migration pass exists to apply.
Idempotent: re-running it is a no-op once the columns are present.
"""

import models  # noqa: F401  (registers the models on Base.metadata)
from database import Base, engine
from sqlalchemy import inspect, text

# column name -> SQLite column type, per table
ADDED_COLUMNS = {
    "citizen_request": {
        "cluster_id": "INTEGER",
        # GPS pin from the citizen intake form's map control.
        "precise_lat": "FLOAT",
        "precise_lon": "FLOAT",
        # School or hospital picked by citizen on intake form.
        "facility_id": "INTEGER",
        # Pin source: citizen_gps or synthetic_seed.
        "pin_source": "TEXT",
        # Specific asset this report was grouped into.
        "asset_id": "INTEGER",
    },
    "demand_cluster": {
        "district": "TEXT",
        "block": "TEXT",
        "avg_confidence": "FLOAT",
        "settlement_count": "INTEGER",
    },
    # Added after village_amenities already existed, so create_all() alone
    # would silently not apply them.
    "village_amenities": {
        # BharatNet / BBNL 2022 connectivity
        "conn_bharatnet_status": "TEXT",
        "conn_bharatnet_gp": "TEXT",
        "conn_bharatnet_distance_km": "FLOAT",
        # Jal Jeevan Mission current tap-connection coverage
        "jjm_households": "INTEGER",
        "jjm_households_with_tap": "INTEGER",
        "jjm_tap_coverage_pct": "FLOAT",
        "jjm_habitation_count": "INTEGER",
        "jjm_quality_status": "TEXT",
    },
    "work_group": {
        # Whether work group position comes from citizen GPS pins or centroids.
        "location_basis": "TEXT",
    },
}


def add_missing_columns() -> list[str]:
    inspector = inspect(engine)
    applied: list[str] = []

    with engine.begin() as connection:
        for table, columns in ADDED_COLUMNS.items():
            if table not in inspector.get_table_names():
                continue
            existing = {col["name"] for col in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name in existing:
                    continue
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
                applied.append(f"{table}.{name}")

    return applied


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("Tables present:", list(Base.metadata.tables.keys()))

    added = add_missing_columns()
    if added:
        print("Added missing columns:", ", ".join(added))
    else:
        print("No missing columns -- schema already up to date.")
