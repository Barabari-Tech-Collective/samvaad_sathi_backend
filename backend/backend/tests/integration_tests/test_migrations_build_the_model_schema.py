"""Migrations must be able to build the schema the models declare.

This is the gate that makes Alembic authoritative. Without it the project has
three mechanisms that each hide the others' gaps: migrations (which could not
build the schema at all - nothing created interview.difficulty, yet a later
alter_column assumed it), create_all (which only ever creates missing tables,
never missing columns), and a hand-written ALTER block that grew by one entry
each time an incident happened.

Runs against a real, empty PostgreSQL database and fails on any difference
between what `alembic upgrade head` produces and what the models declare, so a
model field added without a migration is caught here rather than as an opaque
500 after deploy.

Set SCHEMA_TEST_DATABASE_URL to enable. CI always provides it; locally it is
skipped unless you point it at a throwaway database.
"""

import os
import pathlib

import pytest
import sqlalchemy

from src.repository.events import _missing_columns
from src.repository.table import Base
import src.models.db  # noqa: F401  # registers every model on Base.metadata

DATABASE_URL = os.environ.get("SCHEMA_TEST_DATABASE_URL")
BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _urls():
    """(sync, async) forms of the throwaway test database URL."""
    sync = DATABASE_URL.replace("+asyncpg", "+psycopg2")
    if "+" not in sync.split("://", 1)[0]:
        sync = sync.replace("postgresql://", "postgresql+psycopg2://", 1)
    return sync, sync.replace("+psycopg2", "+asyncpg")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set SCHEMA_TEST_DATABASE_URL to a throwaway PostgreSQL database to run",
)


@pytest.fixture(scope="module")
def migrated_schema():
    """An empty database brought up solely by `alembic upgrade head`."""
    from alembic import command
    from alembic.config import Config

    sync_url, async_url = _urls()
    engine = sqlalchemy.create_engine(sync_url)
    with engine.begin() as connection:
        connection.execute(sqlalchemy.text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(sqlalchemy.text("CREATE SCHEMA public"))

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "src/repository/migrations"))
    config.set_main_option("sqlalchemy.url", async_url)
    config.attributes["database_url"] = async_url  # env.py would otherwise use app settings
    # Fails the test rather than the fixture if the chain itself is broken.
    command.upgrade(config, "head")

    with engine.connect() as connection:
        yield sqlalchemy.inspect(connection)
    engine.dispose()


def test_every_model_table_exists(migrated_schema):
    present = set(migrated_schema.get_table_names())
    missing = sorted(name for name in Base.metadata.tables if name not in present)
    assert not missing, (
        "Migrations do not create these tables the models declare: "
        + ", ".join(missing)
    )


def test_every_model_column_exists(migrated_schema):
    drift = _missing_columns(migrated_schema, Base.metadata)
    assert not drift, (
        "Migrations do not create these columns the models declare:\n"
        + "\n".join(f"  {table}: {', '.join(sorted(columns))}" for table, columns in sorted(drift.items()))
        + "\nAdd a migration for them. Every query selecting such a column fails "
        "at runtime with UndefinedColumnError."
    )
