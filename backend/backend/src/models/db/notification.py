"""Durable in-app notifications and email delivery records (architecture pack F30).

The business record (an appointment, a result) is saved first. A notification is the in-app
message for one person; a delivery is one attempt-tracked email about it. A failed email never
changes the appointment, and a reminder for an old time is superseded, never sent."""

import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.repository.table import Base


class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient_id: Mapped[int] = mapped_column(
        sa.ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(48))
    title: Mapped[str] = mapped_column(sa.String(160))
    body: Mapped[str | None] = mapped_column(sa.Text)
    # An in-app path. Opening it checks current authorization; no private detail is embedded.
    href: Mapped[str | None] = mapped_column(sa.String(300))
    related_type: Mapped[str | None] = mapped_column(sa.String(32))
    related_id: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), index=True
    )
    read_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))


class NotificationDelivery(Base):
    __tablename__ = "notification_delivery"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('queued', 'sent', 'failed', 'skipped', 'superseded')",
            name="ck_notification_delivery_status",
        ),
        sa.Index("ix_notification_delivery_due", "status", "scheduled_for"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(sa.String(16), server_default="email")
    # scheduled | rescheduled | cancelled | result | reminder
    event: Mapped[str] = mapped_column(sa.String(32))
    related_type: Mapped[str] = mapped_column(sa.String(32), server_default="human_interview")
    related_id: Mapped[int] = mapped_column(sa.Integer, index=True)
    # A reminder is valid only for the start time it was made for.
    expected_starts_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    status: Mapped[str] = mapped_column(sa.String(16), server_default="queued")
    attempts: Mapped[int] = mapped_column(server_default="0")
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    scheduled_for: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    sent_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
