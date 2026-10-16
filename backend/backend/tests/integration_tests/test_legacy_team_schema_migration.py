"""Production-like legacy team tables must not block Alembic upgrades."""

import os
import pathlib

import pytest
import sqlalchemy

DATABASE_URL = os.environ.get("SCHEMA_TEST_DATABASE_URL")
BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set SCHEMA_TEST_DATABASE_URL to a throwaway PostgreSQL database to run",
)


def test_upgrade_adopts_verified_legacy_team_tables():
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    engine = sqlalchemy.create_engine(DATABASE_URL)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(BACKEND_ROOT / "src/repository/migrations"),
    )
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    expected_head = ScriptDirectory.from_config(config).get_current_head()

    with engine.begin() as connection:
        connection.execute(sqlalchemy.text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(sqlalchemy.text("CREATE SCHEMA public"))

    command.upgrade(config, "f1b2c3d4e5a6")

    with engine.begin() as connection:
        connection.execute(sqlalchemy.text("""
                CREATE TABLE team (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(128) NOT NULL,
                    description TEXT,
                    status VARCHAR(32) DEFAULT 'active' NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
                )
                """))
        connection.execute(sqlalchemy.text("CREATE INDEX ix_team_name ON team (name)"))
        connection.execute(sqlalchemy.text("""
                CREATE TABLE team_member (
                    id SERIAL PRIMARY KEY,
                    team_id INTEGER NOT NULL REFERENCES team(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                    access_role VARCHAR(128) NOT NULL,
                    purposes JSONB,
                    domain VARCHAR(128),
                    colleges JSONB,
                    status VARCHAR(32) DEFAULT 'active' NOT NULL,
                    removed_date TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
                )
                """))
        connection.execute(
            sqlalchemy.text(
                "CREATE INDEX ix_team_member_team_id ON team_member (team_id)"
            )
        )
        connection.execute(
            sqlalchemy.text(
                "CREATE INDEX ix_team_member_user_id ON team_member (user_id)"
            )
        )
        connection.execute(
            sqlalchemy.text(
                'ALTER TABLE "user" ADD COLUMN is_super_admin BOOLEAN '
                "DEFAULT false NOT NULL"
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        inspector = sqlalchemy.inspect(connection)
        unique_constraints = inspector.get_unique_constraints("team_member")
        assert any(
            constraint.get("column_names") == ["team_id", "user_id"]
            for constraint in unique_constraints
        )
        revision = connection.execute(
            sqlalchemy.text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert revision == expected_head

    engine.dispose()
