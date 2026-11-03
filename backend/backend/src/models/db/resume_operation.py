import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column
from src.repository.table import Base


class ResumeOperation(Base):
    __tablename__ = "resume_operation"
    user_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    request_key: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    file_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    state: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    receipt: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    __table_args__ = (sa.CheckConstraint("state IN ('started','completed','failed')", name="ck_resume_operation_state"),)
