"""A student's report that a transcript or feedback looks wrong (architecture pack F11).

The original evidence is never changed by a report. A reviewer resolves it with a reason and the
student is told the outcome."""

import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.repository.table import Base


class FeedbackIssue(Base):
    __tablename__ = "feedback_issue"
    __table_args__ = (
        sa.CheckConstraint(
            "kind IN ('transcription', 'feedback', 'language', 'other')", name="ck_feedback_issue_kind"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'dismissed')", name="ck_feedback_issue_status"
        ),
        sa.UniqueConstraint("reporter_id", "request_key", name="uq_feedback_issue_request"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reporter_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="CASCADE"), index=True)
    interview_id: Mapped[int] = mapped_column(sa.ForeignKey("interview.id", ondelete="CASCADE"), index=True)
    question_attempt_id: Mapped[int | None] = mapped_column(sa.Integer)
    kind: Mapped[str] = mapped_column(sa.String(16))
    explanation: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(16), server_default="open")
    request_key: Mapped[str] = mapped_column(sa.String(36))
    resolution: Mapped[str | None] = mapped_column(sa.Text)
    resolved_by: Mapped[int | None] = mapped_column(sa.Integer)
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
