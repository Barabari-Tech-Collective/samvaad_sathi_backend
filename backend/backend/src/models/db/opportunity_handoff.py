"""The final list handed to the hiring company, and what the company did with each person."""

import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.repository.table import Base

COMPANY_STATUSES = (
    "not_sent", "sent", "interviewing", "offered", "joined", "declined", "not_selected",
)


class OpportunityHandoff(Base):
    __tablename__ = "opportunity_handoff"
    __table_args__ = (
        sa.UniqueConstraint("selection_id", name="uq_handoff_selection"),
        sa.CheckConstraint("status IN ('prepared', 'shared')", name="ck_handoff_status"),
        sa.CheckConstraint(
            "channel IS NULL OR channel IN ('email', 'portal', 'meeting', 'other')",
            name="ck_handoff_channel",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        sa.ForeignKey("opportunity.id", ondelete="RESTRICT"), index=True
    )
    selection_id: Mapped[int] = mapped_column(
        sa.ForeignKey("opportunity_selection.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(sa.String(16), server_default="prepared")
    version: Mapped[int] = mapped_column(server_default="1")
    contact_name: Mapped[str] = mapped_column(sa.String(160))
    contact_email: Mapped[str | None] = mapped_column(sa.String(255))
    channel: Mapped[str | None] = mapped_column(sa.String(16))
    note: Mapped[str | None] = mapped_column(sa.Text)
    created_by: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    shared_by: Mapped[int | None] = mapped_column(sa.Integer)
    shared_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))


class OpportunityHandoffCandidate(Base):
    __tablename__ = "opportunity_handoff_candidate"
    __table_args__ = (
        sa.UniqueConstraint("handoff_id", "student_id", name="uq_handoff_candidate"),
        sa.CheckConstraint(
            "company_status IN ('not_sent', 'sent', 'interviewing', 'offered', 'joined', "
            "'declined', 'not_selected')",
            name="ck_handoff_company_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    handoff_id: Mapped[int] = mapped_column(
        sa.ForeignKey("opportunity_handoff.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="RESTRICT"))
    company_status: Mapped[str] = mapped_column(sa.String(16), server_default="not_sent")
    status_note: Mapped[str | None] = mapped_column(sa.Text)
    updated_by: Mapped[int | None] = mapped_column(sa.Integer)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
