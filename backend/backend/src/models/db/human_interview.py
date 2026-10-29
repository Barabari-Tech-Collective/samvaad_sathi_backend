"""External human appointments; never synthetic AI interview attempts."""

import datetime
import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column
from src.repository.table import Base


class HumanInterview(Base):
    __tablename__ = "human_interview"
    __table_args__ = (
        sa.UniqueConstraint(
            "host_id", "request_key", name="uq_human_interview_request"
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'completed', 'cancelled', 'no_show', 'technical_issue')",
            name="ck_human_interview_status",
        ),
        sa.CheckConstraint(
            "ends_at > starts_at AND ends_at <= starts_at + interval '3 hours'",
            name="ck_human_interview_duration",
        ),
        sa.CheckConstraint("version >= 1", name="ck_human_interview_version"),
        sa.CheckConstraint(
            "result IS NULL OR result IN ('selected', 'next_round', 'on_hold', 'not_selected')",
            name="ck_human_interview_result",
        ),
        sa.CheckConstraint(
            "student_id <> host_id", name="ck_human_interview_participants"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[str] = mapped_column(sa.String(128), index=True)
    student_id: Mapped[int] = mapped_column(
        sa.ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    host_id: Mapped[int] = mapped_column(
        sa.ForeignKey("user.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(sa.String(160))
    meet_url: Mapped[str] = mapped_column(sa.String(100))
    starts_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), index=True
    )
    ends_at: Mapped[datetime.datetime] = mapped_column(sa.DateTime(timezone=True))
    request_key: Mapped[str] = mapped_column(sa.String(36))
    request_hash: Mapped[str] = mapped_column(sa.String(64))
    status: Mapped[str] = mapped_column(sa.String(24), server_default="scheduled")
    version: Mapped[int] = mapped_column(server_default="1")
    close_reason: Mapped[str | None] = mapped_column(sa.Text)
    closed_by: Mapped[int | None] = mapped_column(
        sa.ForeignKey("user.id", ondelete="SET NULL")
    )
    closed_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    role_name: Mapped[str | None] = mapped_column(sa.String(160))
    instructions: Mapped[str | None] = mapped_column(sa.Text)
    interviewer_name: Mapped[str | None] = mapped_column(sa.String(120))
    reschedule_count: Mapped[int] = mapped_column(server_default="0")
    # Host-entered outcome. Students see it only once result_released_at is set.
    result: Mapped[str | None] = mapped_column(sa.String(24))
    feedback: Mapped[str | None] = mapped_column(sa.Text)
    result_released_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
