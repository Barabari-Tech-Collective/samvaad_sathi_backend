import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column
from src.repository.table import Base


class Graduation(Base):
    __tablename__ = "graduation"
    student_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id"), primary_key=True)
    course_id: Mapped[str] = mapped_column(sa.String(128), primary_key=True)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    state: Mapped[str] = mapped_column(sa.String(20), default="draft", nullable=False)
    course_evidence: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    placement_evidence: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    approval: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    __table_args__ = (sa.CheckConstraint("state IN ('draft','ready','graduated')", name="ck_graduation_state"),)


class GraduationEvent(Base):
    __tablename__ = "graduation_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id"), nullable=False, index=True)
    course_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    actor_id: Mapped[int] = mapped_column(sa.ForeignKey("user.id"), nullable=False)
    action: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    reason: Mapped[str] = mapped_column(sa.String(1000), nullable=False)
    snapshot: Mapped[dict] = mapped_column(sa.JSON, nullable=False)
    __table_args__ = (sa.UniqueConstraint("student_id", "course_id", "version", name="uq_graduation_event_version"),)
