"""Formal placement selections, independent of private-practice shortlists."""

import datetime
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from src.repository.table import Base


class OpportunitySelection(Base):
    __tablename__ = "opportunity_selection"
    __table_args__ = (
        sa.UniqueConstraint(
            "opportunity_id", "revision", name="uq_opportunity_selection_revision"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'locked', 'released')", name="ck_selection_status"
        ),
        sa.CheckConstraint(
            "version >= 1 AND revision >= 1", name="ck_selection_versions"
        ),
        sa.UniqueConstraint("release_key", name="uq_selection_release_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        sa.ForeignKey("opportunity.id", ondelete="RESTRICT"), index=True
    )
    revision: Mapped[int] = mapped_column(server_default="1")
    version: Mapped[int] = mapped_column(server_default="1")
    status: Mapped[str] = mapped_column(sa.String(16), server_default="draft")
    rules: Mapped[dict] = mapped_column(JSONB, nullable=False)
    snapshot: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[int] = mapped_column(
        sa.ForeignKey("user.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    locked_by: Mapped[int | None] = mapped_column(
        sa.ForeignKey("user.id", ondelete="RESTRICT")
    )
    locked_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    released_by: Mapped[int | None] = mapped_column(
        sa.ForeignKey("user.id", ondelete="RESTRICT")
    )
    released_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    release_key: Mapped[str | None] = mapped_column(sa.String(36))
    release_reason: Mapped[str | None] = mapped_column(sa.Text)


class OpportunitySelectionEntry(Base):
    __tablename__ = "opportunity_selection_entry"
    __table_args__ = (
        sa.UniqueConstraint("selection_id", "student_id", name="uq_selection_student"),
        sa.CheckConstraint(
            "recommendation IS NULL OR recommendation IN ('include', 'exclude', 'needs_evidence')",
            name="ck_selection_recommendation",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('include', 'exclude')",
            name="ck_selection_decision",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    selection_id: Mapped[int] = mapped_column(
        sa.ForeignKey("opportunity_selection.id", ondelete="RESTRICT"), index=True
    )
    student_id: Mapped[int] = mapped_column(
        sa.ForeignKey("user.id", ondelete="RESTRICT"), index=True
    )
    recommendation: Mapped[str | None] = mapped_column(sa.String(24))
    recommendation_reason: Mapped[str | None] = mapped_column(sa.Text)
    recommended_by: Mapped[int | None] = mapped_column(
        sa.ForeignKey("user.id", ondelete="RESTRICT")
    )
    recommended_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    decision: Mapped[str | None] = mapped_column(sa.String(16))
    decision_reason: Mapped[str | None] = mapped_column(sa.Text)
    student_feedback: Mapped[str | None] = mapped_column(sa.Text)
    next_step: Mapped[str | None] = mapped_column(sa.Text)
    decided_by: Mapped[int | None] = mapped_column(
        sa.ForeignKey("user.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[datetime.datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    evidence_snapshot: Mapped[dict | None] = mapped_column(JSONB)
