"""A human interview's scorecard (architecture pack F20).

The interviewer drafts evidence-linked criteria, private notes and the comments the student
will see, then submits. A different reviewer approves or returns it; only then can the result
be released. Student-visible comments and internal notes are separate fields."""

import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.repository.table import Base


class HumanScorecard(Base):
    __tablename__ = "human_scorecard"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'approved', 'returned')",
            name="ck_human_scorecard_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        sa.ForeignKey("human_interview.id", ondelete="CASCADE"), unique=True
    )
    # [{"name": str, "score": 0-5 | null, "evidence": str | null}]
    criteria: Mapped[list] = mapped_column(JSONB, server_default="[]")
    suggested_result: Mapped[str | None] = mapped_column(sa.String(24))
    internal_notes: Mapped[str | None] = mapped_column(sa.Text)
    student_comments: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(16), server_default="draft")
    version: Mapped[int] = mapped_column(server_default="1")
    submitted_by: Mapped[int | None] = mapped_column(sa.Integer)
    submitted_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(sa.Integer)
    reviewed_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )
