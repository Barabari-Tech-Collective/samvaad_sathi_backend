"""One scoped roster query shape for list, summary and bounded CSV export."""

import csv
import io
from typing import Literal

import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator
from src.models.db.interview import Interview
from src.models.db.user import User

Activity = Literal["all", "practised", "not_started"]
EXPORT_LIMIT = 1000


class RosterFilters(BaseModel):
    q: str = Field(default="", max_length=100)
    activity: Activity = "all"

    @field_validator("q", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class RosterExport(RosterFilters):
    reason: str = Field(min_length=10, max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


def completed_practice_count(course_id):
    return (
        sa.select(sa.func.count(Interview.id))
        .where(
            Interview.user_id == User.id,
            Interview.course_id == course_id,
            Interview.interview_mode == "practice",
            Interview.status == "completed",
        )
        .correlate(User)
        .scalar_subquery()
    )


def roster_predicate(authorized, completed, filters):
    predicate = authorized
    if filters.q:
        literal = (
            filters.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        predicate = sa.and_(
            predicate,
            sa.or_(
                User.name.ilike(f"%{literal}%", escape="\\"),
                User.university.ilike(f"%{literal}%", escape="\\"),
            ),
        )
    if filters.activity == "practised":
        predicate = sa.and_(predicate, completed > 0)
    elif filters.activity == "not_started":
        predicate = sa.and_(predicate, completed == 0)
    return predicate


def roster_rows(predicate, completed):
    return (
        sa.select(
            User.id, User.name, User.university, completed.label("completed_practice")
        )
        .where(predicate)
        .order_by(User.id)
    )


def csv_cell(value):
    text = "" if value is None else str(value)
    text = "".join(char for char in text if char >= " " or char in "\t\r\n")
    # Quoting alone does not stop spreadsheet formula execution. Also inspect
    # whitespace before a formula marker (names and colleges are untrusted).
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(
        ("\t", "\r", "\n")
    ):
        text = "'" + text
    return text


def roster_csv(rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        ["Student ID", "Name", "College", "Completed course practice rounds"]
    )
    for row in rows:
        writer.writerow(
            [
                row.id,
                csv_cell(row.name),
                csv_cell(row.university),
                row.completed_practice,
            ]
        )
    return "\ufeff" + output.getvalue()
