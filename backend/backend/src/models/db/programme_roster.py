import datetime
import sqlalchemy
from sqlalchemy.orm import Mapped as SQLAlchemyMapped, mapped_column as sqlalchemy_mapped_column
from sqlalchemy.sql import functions as sqlalchemy_functions

from src.repository.table import Base


class ProgrammeRosterMember(Base):  # type: ignore
    """A programme-scoped learner roster member independent of course enrolment.

    See ADR-2026-11-03: Programme-scoped learner roster independent of courses.
    Allows placement teams to import CSV/XLSX learner lists, track invite status,
    and schedule interviews prior to or alongside course enrolment.
    """

    __tablename__ = "programme_roster_member"
    __table_args__ = (
        sqlalchemy.UniqueConstraint(
            "programme_scope",
            "email",
            name="uq_programme_roster_member_scope_email",
        ),
    )

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True, autoincrement=True)
    programme_scope: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(128),
        nullable=False,
        server_default="default",
        index=True,
    )
    email: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(254),
        nullable=False,
        index=True,
    )
    name: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(160),
        nullable=False,
    )
    external_learner_id: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(128),
        nullable=True,
    )
    import_batch_id: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(64),
        nullable=True,
        index=True,
    )
    # One of: "invited", "invitation_resent", "already_ready", "claimed", "manual_review"
    invite_status: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(32),
        nullable=False,
        server_default="invited",
    )
    claimed_user_id: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    invited_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        server_default=sqlalchemy_functions.now(),
        nullable=False,
    )
    claimed_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=True,
    )
    created_by: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        server_default=sqlalchemy_functions.now(),
        nullable=False,
    )
    updated_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        server_default=sqlalchemy_functions.now(),
        onupdate=sqlalchemy_functions.now(),
        nullable=False,
    )

