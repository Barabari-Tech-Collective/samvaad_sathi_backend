import datetime

import sqlalchemy
from sqlalchemy.orm import Mapped as SQLAlchemyMapped, mapped_column as sqlalchemy_mapped_column
from sqlalchemy.sql import functions as sqlalchemy_functions

from src.repository.table import Base


class StudentPlacement(Base):  # type: ignore
    """One course and batch a student chose for themselves.

    The list of choices lives in Sampark (a batch must be open for selection there). A row ends
    when the student changes their mind, so the table is also the history of what they chose
    and when. Only one batch per course can be active at a time."""

    __tablename__ = "student_placement"
    __table_args__ = (
        sqlalchemy.Index(
            "uq_student_placement_batch",
            "student_id",
            "batch_key",
            unique=True,
            postgresql_where=sqlalchemy.text("ended_at IS NULL"),
        ),
        sqlalchemy.Index(
            "uq_student_placement_course",
            "student_id",
            "course_id",
            unique=True,
            postgresql_where=sqlalchemy.text("ended_at IS NULL"),
        ),
    )

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True)
    student_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    # Sampark's stable id for the batch (its unique id, or the batch id when that is missing).
    batch_key: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(128))
    batch_name: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(200))
    # Sampark's numeric course id as text: the same value staff course grants use.
    course_id: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(128))
    course_name: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(200))
    selected_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), server_default=sqlalchemy_functions.now()
    )
    ended_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True)
    )
