"""Database model for speech pacing practice sessions."""

import datetime
import sqlalchemy
from sqlalchemy.orm import Mapped as SQLAlchemyMapped, mapped_column as sqlalchemy_mapped_column
from sqlalchemy.sql import functions as sqlalchemy_functions
from sqlalchemy.dialects.postgresql import JSONB

from src.repository.table import Base


class PacingPracticeSession(Base):  # type: ignore
    """Model for speech pacing practice sessions."""
    __tablename__ = "pacing_practice_session"

    __table_args__ = (sqlalchemy.UniqueConstraint("user_id", "client_request_id", name="uq_pacing_user_request"),)
    client_request_id: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(36), nullable=True)

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True, autoincrement="auto")
    user_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    level: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, nullable=False, index=True
    )
    prompt_text: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.Text, nullable=False
    )
    prompt_index: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, nullable=False
    )
    status: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=32), nullable=False, index=True, default="pending"
    )
    # Transcription output
    transcript: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.Text, nullable=True
    )
    words_data: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )
    # Computed metrics
    score: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, nullable=True
    )
    wpm: SQLAlchemyMapped[float | None] = sqlalchemy_mapped_column(
        sqlalchemy.Float, nullable=True
    )
    pause_words_interval: SQLAlchemyMapped[float | None] = sqlalchemy_mapped_column(
        sqlalchemy.Float, nullable=True
    )
    analysis_result: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )

    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=False, server_default=sqlalchemy_functions.now()
    )
    updated_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=False,
        server_default=sqlalchemy_functions.now(), onupdate=sqlalchemy_functions.now()
    )

    __mapper_args__ = {"eager_defaults": True}
