"""Compare a live database against the ORM models. Read-only.

Production's alembic_version points at a revision that is not in the chain, so
`alembic upgrade head` cannot run there at all. Before stamping production to a
real revision we have to know whether its schema actually matches the models -
stamping a database that is secretly behind would mark genuine migrations as
already applied and they would never run.

This issues only SELECTs against the system catalogue. It creates nothing,
alters nothing, and writes nothing.

    python ops/check_schema_drift.py                 # uses .env settings
    python ops/check_schema_drift.py --url "postgresql+psycopg2://..."
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/backend"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="SQLAlchemy URL; defaults to the configured database")
    arguments = parser.parse_args()

    import sqlalchemy

    from src.config.manager import settings
    from src.repository.events import _missing_columns
    from src.repository.table import Base
    import src.models.db  # noqa: F401  # registers every model on Base.metadata

    url = arguments.url
    if not url:
        password = urllib.parse.quote_plus(settings.DB_POSTGRES_PASSWORD)
        url = (
            f"postgresql+psycopg2://{settings.DB_POSTGRES_USERNAME}:{password}"
            f"@{settings.DB_POSTGRES_HOST}:{settings.DB_POSTGRES_PORT}/{settings.DB_POSTGRES_NAME}"
        )

    # Managed providers such as Aiven reject plaintext connections.
    engine = sqlalchemy.create_engine(url, connect_args={"sslmode": "require"})
    try:
        with engine.connect() as connection:
            inspector = sqlalchemy.inspect(connection)
            present = set(inspector.get_table_names())
            missing_tables = sorted(n for n in Base.metadata.tables if n not in present)
            missing_columns = _missing_columns(inspector, Base.metadata)
            stamped = connection.execute(
                sqlalchemy.text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
    finally:
        engine.dispose()

    print(f"alembic_version : {stamped}")
    print(f"tables in models: {len(Base.metadata.tables)}")
    print(f"tables present  : {len(present)}")
    print()

    if missing_tables:
        print(f"MISSING TABLES ({len(missing_tables)}):")
        for name in missing_tables:
            print(f"  - {name}")
        print()
    if missing_columns:
        total = sum(len(c) for c in missing_columns.values())
        print(f"MISSING COLUMNS ({total}):")
        for table, columns in sorted(missing_columns.items()):
            print(f"  - {table}: {', '.join(sorted(columns))}")
        print()

    if missing_tables or missing_columns:
        print("RESULT: schema is BEHIND the models. Do not stamp.")
        print("Every gap has to be reconciled first, or stamping will mark the")
        print("migrations that would have created them as already applied.")
        return 1

    print("RESULT: schema matches the models.")
    print("Safe to stamp to the current head so migrations can run again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
