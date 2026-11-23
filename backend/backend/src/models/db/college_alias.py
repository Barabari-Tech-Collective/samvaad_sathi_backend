import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from src.repository.table import Base


class CollegeAlias(Base):
    """One spelling students typed, pointing at the college name staff chose for it."""

    __tablename__ = "college_alias"

    spelling_key: Mapped[str] = mapped_column(sa.String(256), primary_key=True)
    canonical_key: Mapped[str] = mapped_column(sa.String(256), index=True)
    canonical_label: Mapped[str] = mapped_column(sa.String(256))
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


class LearnerImportCollege(Base):
    """The college given in an import sheet, used until the learner's account has its own."""

    __tablename__ = "learner_import_college"

    email: Mapped[str] = mapped_column(sa.String(320), primary_key=True)
    college: Mapped[str] = mapped_column(sa.String(256))
    added_by: Mapped[int | None] = mapped_column(sa.ForeignKey("user.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )


class LearnerGroupEntry(Base):
    """A college, course or batch that staff added by hand, so it is listed before it has students."""

    __tablename__ = "learner_group_entry"
    __table_args__ = (sa.UniqueConstraint("kind", "group_key", "course_key", name="uq_learner_group_entry"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(sa.String(16))  # college | course | batch
    group_key: Mapped[str] = mapped_column(sa.String(256))
    label: Mapped[str] = mapped_column(sa.String(256))
    # A batch belongs to a course; the other kinds leave this empty (stored as '' so the unique key works).
    course_key: Mapped[str] = mapped_column(sa.String(128), default="", server_default="")
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
