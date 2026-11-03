"""Approved assessment conditions (architecture pack F13).

A student privately asks for an adjustment. A person holding the restricted accommodations
permission approves (with a fixed set of conditions) or declines, with a reason the student
sees. The student confirms approved conditions. Anyone else who needs to know (an interviewer, an
assessment reviewer) sees only the confirmed condition labels, never the request text."""

import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.repository.table import Base

# Fixed catalogue: a code is what the server enforces or shows; the label is plain language.
CONDITIONS = {
    "extra_time_25": "25% extra time",
    "extra_time_50": "50% extra time",
    "extra_time_100": "Double time",
    "rest_breaks": "Short rest breaks",
    "read_aloud_instructions": "Instructions read aloud",
    "flexible_rescheduling": "Flexible rescheduling",
}


class AccommodationRequest(Base):
    __tablename__ = "accommodation_request"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('requested', 'approved', 'declined', 'withdrawn')",
            name="ck_accommodation_status",
        ),
        sa.UniqueConstraint("student_id", "request_key", name="uq_accommodation_request"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="CASCADE"), index=True)
    request_key: Mapped[str] = mapped_column(sa.String(36))
    # Private: only the accommodations owners and the student can read it.
    description: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(16), server_default="requested")
    version: Mapped[int] = mapped_column(server_default="1")
    # Codes from CONDITIONS, set on approval.
    conditions: Mapped[list] = mapped_column(JSONB, server_default="[]")
    # Shown to the student with the decision.
    decision_reason: Mapped[str | None] = mapped_column(sa.Text)
    decided_by: Mapped[int | None] = mapped_column(sa.Integer)
    decided_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # Conditions apply only once the student has confirmed them.
    confirmed_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
