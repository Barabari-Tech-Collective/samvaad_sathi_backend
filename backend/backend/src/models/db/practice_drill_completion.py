"""Durable, idempotent completion evidence for Practice skill builders."""

import datetime

import sqlalchemy
from sqlalchemy.orm import Mapped as SQLAlchemyMapped
from sqlalchemy.orm import mapped_column as sqlalchemy_mapped_column
from sqlalchemy.sql import functions as sqlalchemy_functions

from src.repository.table import Base


class PracticeDrillCompletion(Base):  # type: ignore
    """One completed Practice drill attempt reported by an authenticated student."""

    __tablename__ = "practice_drill_completion"
    __table_args__ = (
        sqlalchemy.UniqueConstraint(
            "user_id",
            "client_completion_id",
            name="uq_practice_drill_completion_user_client_id",
        ),
        sqlalchemy.Index(
            "ix_practice_drill_completion_user_completed_at",
            "user_id",
            "completed_at",
        ),
    )

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        primary_key=True, autoincrement="auto"
    )
    user_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    module: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=32), nullable=False
    )
    level: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=24), nullable=False
    )
    client_completion_id: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=False
    )
    evidence_kind: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=24), nullable=False
    )
    evidence_units: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, nullable=False
    )
    completed_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
    )

    __mapper_args__ = {"eager_defaults": True}
