"""A teacher asks a learner to take one mock interview (separate from speaking drills)."""

import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.repository.table import Base


class MockInterviewAssignment(Base):
    __tablename__ = "mock_interview_assignment"
    __table_args__ = (
        sa.UniqueConstraint("assigned_by", "request_key", name="uq_mock_assignment_request"),
        sa.CheckConstraint(
            "status IN ('assigned', 'completed', 'cancelled')", name="ck_mock_assignment_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="CASCADE"), index=True)
    assigned_by: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="RESTRICT"))
    course_id: Mapped[str] = mapped_column(sa.String(128), index=True)
    # The published mock interview template to take.
    job_profile_id: Mapped[int] = mapped_column(sa.ForeignKey("job_profile.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(sa.Text)
    note: Mapped[str | None] = mapped_column(sa.Text)
    due_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    request_key: Mapped[str] = mapped_column(sa.String(36))
    request_hash: Mapped[str] = mapped_column(sa.String(64))
    status: Mapped[str] = mapped_column(sa.String(24), server_default="assigned")
    version: Mapped[int] = mapped_column(server_default="1")
    # The finished mock that completed it.
    interview_id: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    closed_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
