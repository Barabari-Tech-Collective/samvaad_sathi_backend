"""Student requests to export or delete their data (architecture pack F32).

No cosmetic toggle: a request is verified by a person holding the restricted data-requests
permission, processed for real, and closed with what was deleted, what was retained and why."""

import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.repository.table import Base


class DataRequest(Base):
    __tablename__ = "data_request"
    __table_args__ = (
        sa.CheckConstraint("kind IN ('export', 'deletion')", name="ck_data_request_kind"),
        sa.CheckConstraint(
            "status IN ('submitted', 'verified', 'completed', 'rejected', 'withdrawn')",
            name="ck_data_request_status",
        ),
        sa.UniqueConstraint("student_id", "request_key", name="uq_data_request_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(sa.String(16))
    status: Mapped[str] = mapped_column(sa.String(16), server_default="submitted")
    version: Mapped[int] = mapped_column(server_default="1")
    request_key: Mapped[str] = mapped_column(sa.String(36))
    note: Mapped[str | None] = mapped_column(sa.Text)
    verification_note: Mapped[str | None] = mapped_column(sa.Text)
    handled_by: Mapped[int | None] = mapped_column(sa.Integer)
    # {"deleted": [{"category", "count"}], "retained": [{"category", "reason"}], "pending": [...]}
    outcome: Mapped[dict | None] = mapped_column(JSONB)
    closure_note: Mapped[str | None] = mapped_column(sa.Text)
    export_available_until: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
