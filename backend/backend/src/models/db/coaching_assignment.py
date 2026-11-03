"""Course-bound follow-ups; legacy practice_assignment rows remain separate."""

import datetime
import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column
from src.repository.table import Base


class CoachingAssignment(Base):
    __tablename__ = "coaching_assignment"
    __table_args__ = (
        sa.UniqueConstraint(
            "assigned_by", "request_key", name="uq_coaching_assignment_request"
        ),
        sa.CheckConstraint(
            "status IN ('assigned', 'completed', 'cancelled')",
            name="ck_coaching_assignment_status",
        ),
        sa.CheckConstraint(
            "module IN ('warm_up', 'think_fast')", name="ck_coaching_assignment_module"
        ),
        sa.CheckConstraint("version >= 1", name="ck_coaching_assignment_version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        sa.ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    assigned_by: Mapped[int] = mapped_column(
        sa.ForeignKey("user.id", ondelete="RESTRICT")
    )
    course_id: Mapped[str] = mapped_column(sa.String(128), index=True)
    module: Mapped[str] = mapped_column(sa.String(32))
    reason: Mapped[str] = mapped_column(sa.Text)
    note: Mapped[str | None] = mapped_column(sa.Text)
    due_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    request_key: Mapped[str] = mapped_column(sa.String(36))
    request_hash: Mapped[str] = mapped_column(sa.String(64))
    status: Mapped[str] = mapped_column(sa.String(24), server_default="assigned")
    version: Mapped[int] = mapped_column(server_default="1")
    completion_id: Mapped[int | None] = mapped_column(
        sa.ForeignKey("practice_drill_completion.id", ondelete="RESTRICT"), unique=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    closed_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
