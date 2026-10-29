import datetime
import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column
from src.repository.table import Base


class ResumeStorageJob(Base):
    __tablename__ = "resume_storage_job"
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    file_sha256: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    payload: Mapped[bytes | None] = mapped_column(sa.LargeBinary, nullable=True)
    filename: Mapped[str] = mapped_column(sa.String(256))
    content_type: Mapped[str] = mapped_column(sa.String(128))
    resume_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    state: Mapped[str] = mapped_column(sa.String(16), server_default="pending")
    attempts: Mapped[int] = mapped_column(sa.Integer, server_default="0")
    expires_at: Mapped[datetime.datetime] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = (sa.CheckConstraint("state IN ('pending','stored','failed','expired')",name="ck_resume_storage_job_state"),sa.CheckConstraint("attempts >= 0 AND attempts <= 3",name="ck_resume_storage_job_attempts"))
