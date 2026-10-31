"""A learner accepted the privacy notice once, at a given version."""

import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.repository.table import Base


class PrivacyNoticeAcceptance(Base):
    __tablename__ = "privacy_notice_acceptance"
    __table_args__ = (sa.UniqueConstraint("user_id", "version", name="uq_privacy_notice_user_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(sa.String(32))
    accepted_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
