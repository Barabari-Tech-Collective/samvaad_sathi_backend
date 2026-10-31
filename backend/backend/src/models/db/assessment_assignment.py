"""A staff member assigns one AI assessment to one learner (architecture pack F14).

Pins the template revision, sets when the learner may start it, and keeps the accommodation
labels that were confirmed at assignment time. Draft edits never touch an assigned round."""

import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.repository.table import Base


class AssessmentAssignment(Base):
    __tablename__ = "assessment_assignment"
    __table_args__ = (
        sa.UniqueConstraint("student_id", "request_key", name="uq_assessment_assignment_request"),
        sa.CheckConstraint(
            "status IN ('assigned', 'started', 'completed', 'cancelled')",
            name="ck_assessment_assignment_status",
        ),
        sa.CheckConstraint(
            "closes_at IS NULL OR closes_at > opens_at", name="ck_assessment_assignment_window"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="CASCADE"), index=True)
    assigned_by: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="RESTRICT"))
    course_id: Mapped[str] = mapped_column(sa.String(128), index=True)
    cohort_id: Mapped[int | None] = mapped_column(sa.Integer)
    job_profile_id: Mapped[int] = mapped_column(sa.ForeignKey("job_profile.id", ondelete="RESTRICT"), index=True)
    # JobProfile.content_revision when assigned: a later edit needs a new assignment.
    pinned_revision: Mapped[int] = mapped_column()
    difficulty: Mapped[str] = mapped_column(sa.String(16), server_default="medium")
    opens_at: Mapped[datetime.datetime] = mapped_column(sa.DateTime(timezone=True))
    closes_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    instructions: Mapped[str | None] = mapped_column(sa.Text)
    # [{"code", "label"}] confirmed when assigned. Labels only, never the student's own words.
    accommodations: Mapped[list | None] = mapped_column(JSONB)
    request_key: Mapped[str] = mapped_column(sa.String(36))
    status: Mapped[str] = mapped_column(sa.String(16), server_default="assigned")
    version: Mapped[int] = mapped_column(server_default="1")
    interview_id: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    closed_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
