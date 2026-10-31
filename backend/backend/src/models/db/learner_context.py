"""Project and experience context a student has written and confirmed (architecture pack F10).

Only confirmed items may shape generated practice questions. Editing a confirmed item returns it
to draft, so what the questions use is always what the student last confirmed."""

import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.repository.table import Base


class LearnerContext(Base):
    __tablename__ = "learner_context"
    __table_args__ = (
        sa.CheckConstraint("kind IN ('project', 'experience')", name="ck_learner_context_kind"),
        sa.CheckConstraint("status IN ('draft', 'confirmed')", name="ck_learner_context_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(sa.String(16))
    title: Mapped[str] = mapped_column(sa.String(120))
    summary: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(16), server_default="draft")
    version: Mapped[int] = mapped_column(server_default="1")
    confirmed_at: Mapped[datetime.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )
