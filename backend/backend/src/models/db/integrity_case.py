"""Assessment-only human review. No model verdicts or automatic score edits."""

import datetime as dt
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from src.repository.table import Base


class IntegrityCase(Base):
    __tablename__ = "integrity_case"
    __table_args__ = (
        sa.UniqueConstraint("interview_id", name="uq_integrity_case_interview"),
        sa.CheckConstraint(
            "state IN ('open','decided','appealed','resolved')",
            name="ck_integrity_case_state",
        ),
        sa.CheckConstraint("version >= 1", name="ck_integrity_case_version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(
        sa.ForeignKey("interview.id", ondelete="RESTRICT"), index=True
    )
    student_id: Mapped[int] = mapped_column(
        sa.ForeignKey("user.id", ondelete="RESTRICT"), index=True
    )
    course_id: Mapped[str] = mapped_column(sa.String(128), index=True)
    college: Mapped[str | None] = mapped_column(sa.String(512))
    opened_by: Mapped[int] = mapped_column(
        sa.ForeignKey("user.id", ondelete="RESTRICT")
    )
    request_key: Mapped[str] = mapped_column(sa.String(36))
    request_hash: Mapped[str] = mapped_column(sa.String(64))
    state: Mapped[str] = mapped_column(sa.String(16), server_default="open")
    version: Mapped[int] = mapped_column(server_default="1")
    outcome: Mapped[str | None] = mapped_column(sa.String(24))
    decided_by: Mapped[int | None] = mapped_column(
        sa.ForeignKey("user.id", ondelete="RESTRICT")
    )
    appeal_deadline: Mapped[dt.datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    history: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
