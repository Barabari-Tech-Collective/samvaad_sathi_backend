import datetime

import sqlalchemy
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped as SQLAlchemyMapped
from sqlalchemy.orm import mapped_column as sqlalchemy_mapped_column
from sqlalchemy.sql import functions as sqlalchemy_functions

from src.repository.table import Base


class AIJob(Base):  # type: ignore
    """Privacy-minimised audit record for one versioned AI operation.

    Prompts, resumes, transcripts and API keys are intentionally not stored.
    ``input_hash`` proves which immutable input was used, while the small result
    payload allows a safe idempotent replay for generated practice questions.
    """

    __tablename__ = "ai_job"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        primary_key=True, autoincrement=True
    )
    interview_id: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("interview.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    job_type: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=False, index=True
    )
    idempotency_key: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=160), nullable=False, unique=True
    )
    input_hash: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=False
    )
    status: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=24), nullable=False, index=True
    )
    provider: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=32), nullable=False
    )
    model: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=128), nullable=False
    )
    prompt_version: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=False
    )
    schema_version: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=False
    )
    rubric_version: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=True
    )
    parameters: SQLAlchemyMapped[dict] = sqlalchemy_mapped_column(
        JSONB, nullable=False, default=dict
    )
    result_payload: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )
    result_hash: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=True
    )
    attempt_count: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, nullable=False, default=0, server_default="0"
    )
    latency_ms: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, nullable=True
    )
    error_code: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=48), nullable=True
    )
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
    )
    updated_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
        onupdate=sqlalchemy_functions.now(),
    )
    completed_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=True
    )

    __mapper_args__ = {"eager_defaults": True}
