import datetime

import sqlalchemy
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped as SQLAlchemyMapped, mapped_column as sqlalchemy_mapped_column
from sqlalchemy.sql import functions as sqlalchemy_functions

from src.repository.table import Base


class AssessmentScoreCorrection(Base):  # type: ignore
    """One version of a corrected AI assessment score (architecture pack F16).

    The report itself carries the current score, so every reader sees the correction; each
    row here keeps what it was before, who changed it and why, so no version is lost."""

    __tablename__ = "assessment_score_correction"
    __table_args__ = (
        sqlalchemy.UniqueConstraint("interview_id", "version", name="uq_score_correction_version"),
        sqlalchemy.CheckConstraint(
            "speech_pct BETWEEN 0 AND 100 AND (knowledge_pct IS NULL OR knowledge_pct BETWEEN 0 AND 100)",
            name="ck_score_correction_range",
        ),
    )

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True)
    interview_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("interview.id", ondelete="CASCADE"), index=True
    )
    version: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(sqlalchemy.Integer)
    knowledge_pct: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(sqlalchemy.Integer)
    speech_pct: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(sqlalchemy.Integer)
    # {"knowledge": int | None, "speech": int | None} as it stood before this version.
    previous: SQLAlchemyMapped[dict] = sqlalchemy_mapped_column(JSONB)
    # Internal: why the score changed.
    reason: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.Text)
    # Optional words the student sees with the corrected score.
    student_note: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.Text)
    corrected_by: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(sqlalchemy.Integer)
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), server_default=sqlalchemy_functions.now()
    )
