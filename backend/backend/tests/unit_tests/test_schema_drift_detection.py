"""Schema drift must be named at startup, not discovered as a 500 in production.

A column declared on a model and absent from the table makes every query
touching that table fail, because SQLAlchemy names each column in its SELECT.
That has already cost this product twice: user.is_admin broke login on staging,
and interview.difficulty broke interview creation. Neither Alembic nor
create_all catches it - no migration adds interview.difficulty at all, and
create_all(checkfirst=True) only creates missing tables, never columns.
"""

import sqlalchemy
import pytest

from src.repository.events import _missing_columns


@pytest.fixture
def database():
    """A real in-memory database, so the inspector is genuinely exercised."""
    engine = sqlalchemy.create_engine("sqlite://")
    with engine.connect() as connection:
        yield connection


def _metadata_with(columns: list[sqlalchemy.Column]) -> sqlalchemy.MetaData:
    metadata = sqlalchemy.MetaData()
    sqlalchemy.Table("interview", metadata, *columns)
    return metadata


def test_reports_a_column_the_model_declares_but_the_table_lacks(database):
    database.execute(sqlalchemy.text("CREATE TABLE interview (id INTEGER PRIMARY KEY)"))
    metadata = _metadata_with(
        [
            sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
            sqlalchemy.Column("difficulty", sqlalchemy.String(16)),
        ]
    )
    assert _missing_columns(sqlalchemy.inspect(database), metadata) == {
        "interview": ["difficulty"]
    }


def test_reports_nothing_when_the_table_matches(database):
    database.execute(
        sqlalchemy.text("CREATE TABLE interview (id INTEGER PRIMARY KEY, difficulty VARCHAR(16))")
    )
    metadata = _metadata_with(
        [
            sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
            sqlalchemy.Column("difficulty", sqlalchemy.String(16)),
        ]
    )
    assert _missing_columns(sqlalchemy.inspect(database), metadata) == {}


def test_absent_table_is_not_reported_as_column_drift(database):
    """A missing table is a separate problem; listing its columns is only noise."""
    metadata = _metadata_with([sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True)])
    assert _missing_columns(sqlalchemy.inspect(database), metadata) == {}


def test_extra_database_columns_are_not_drift(database):
    """Columns the models no longer use are harmless and must not raise alarms."""
    database.execute(
        sqlalchemy.text("CREATE TABLE interview (id INTEGER PRIMARY KEY, legacy_notes TEXT)")
    )
    metadata = _metadata_with([sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True)])
    assert _missing_columns(sqlalchemy.inspect(database), metadata) == {}
