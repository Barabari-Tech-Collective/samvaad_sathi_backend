"""When a learner counts as ready to be put forward, per course (shown, never hidden)."""

import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.repository.table import Base


class ReadinessRule(Base):
    __tablename__ = "readiness_rule"
    __table_args__ = (
        sa.UniqueConstraint("course_id", name="uq_readiness_rule_course"),
        sa.CheckConstraint("window_days BETWEEN 7 AND 365", name="ck_readiness_window"),
        sa.CheckConstraint("min_mock_interviews BETWEEN 1 AND 20", name="ck_readiness_mocks"),
        sa.CheckConstraint("min_average_score BETWEEN 0 AND 100", name="ck_readiness_score"),
        sa.CheckConstraint("min_drills BETWEEN 0 AND 200", name="ck_readiness_drills"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[str] = mapped_column(sa.String(128))
    window_days: Mapped[int] = mapped_column(server_default="60")
    min_mock_interviews: Mapped[int] = mapped_column(server_default="3")
    min_average_score: Mapped[int] = mapped_column(server_default="60")
    min_drills: Mapped[int] = mapped_column(server_default="5")
    require_improving: Mapped[bool] = mapped_column(server_default=sa.false())
    version: Mapped[int] = mapped_column(server_default="1")
    updated_by: Mapped[int | None] = mapped_column(sa.Integer)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
