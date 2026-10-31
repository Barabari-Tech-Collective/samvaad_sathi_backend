import datetime

import sqlalchemy
from sqlalchemy.orm import Mapped as SQLAlchemyMapped, mapped_column as sqlalchemy_mapped_column
from sqlalchemy.sql import functions as sqlalchemy_functions

from src.repository.table import Base


class Cohort(Base):  # type: ignore
    """A staff-managed group of learners inside one course (architecture pack sec 4).

    Grouping only: belonging to a cohort grants nobody access to anyone's evidence. Who may
    see a member is still decided by the course grant and the member's own sharing choices."""

    __tablename__ = "cohort"
    __table_args__ = (
        sqlalchemy.UniqueConstraint("course_id", "request_key", name="uq_cohort_request"),
    )

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True)
    course_id: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(128), index=True)
    name: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(120))
    description: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.Text)
    request_key: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(64))
    version: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, server_default="1"
    )
    created_by: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(sqlalchemy.Integer)
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), server_default=sqlalchemy_functions.now()
    )
    archived_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True)
    )


class CohortMember(Base):  # type: ignore
    __tablename__ = "cohort_member"
    __table_args__ = (
        sqlalchemy.UniqueConstraint("cohort_id", "student_id", name="uq_cohort_member"),
    )

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True)
    cohort_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("cohort.id", ondelete="CASCADE"), index=True
    )
    student_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    added_by: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(sqlalchemy.Integer)
    added_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), server_default=sqlalchemy_functions.now()
    )
