from __future__ import annotations

import datetime
import sqlalchemy
import sqlalchemy.orm
from sqlalchemy.orm import (
    Mapped as SQLAlchemyMapped,
    mapped_column as sqlalchemy_mapped_column,
)
from sqlalchemy.sql import functions as sqlalchemy_functions
from sqlalchemy.dialects.postgresql import JSONB
from enum import Enum as _Enum

from src.repository.table import Base


class BulkATSBatchStatus(str, _Enum):
    DRAFT = "draft"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class BulkATSCandidateStatus(str, _Enum):
    QUEUED = "queued"
    PARSING = "parsing"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class BulkATSExperienceMode(str, _Enum):
    FRESHER = "fresher"
    EXPERIENCED = "experienced"


class BulkATSBatch(Base):  # type: ignore
    """One recruiter-initiated bulk screening run against a role profile.

    Reuses ``job_profile`` for the role/skills rubric rather than duplicating
    that data - a batch is always screening candidates against some role, and
    JobProfile already models name/description/skills/experience_level for
    that. ``role_name``/``experience_mode`` are denormalized onto the batch so
    a batch keeps its own label and fresher/experienced weighting even if the
    linked job profile is later edited or deleted.
    """

    __tablename__ = "bulk_ats_batch"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        primary_key=True, autoincrement="auto"
    )
    name: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=160), nullable=False
    )

    job_profile_id: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("job_profile.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    role_name: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=160), nullable=False
    )
    role_description: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.Text, nullable=True
    )
    evaluation_intent: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.Text, nullable=True
    )
    required_skills: SQLAlchemyMapped[list | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )
    preferred_skills: SQLAlchemyMapped[list | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )
    experience_mode: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=16),
        nullable=False,
        server_default=BulkATSExperienceMode.EXPERIENCED.value,
    )
    # Per-batch override of the category weight table (skills/education/experience/
    # project/links -> 0-100). Falls back to the experience-mode default table
    # in scoring_weights.py when null, so most batches never need to set this.
    weight_overrides: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )
    jd_analysis: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )
    rubric_version: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, nullable=False, server_default="1"
    )
    rubric_snapshot: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )
    retention_until: SQLAlchemyMapped[datetime.datetime | None] = (
        sqlalchemy_mapped_column(
            sqlalchemy.DateTime(timezone=True), nullable=True, index=True
        )
    )

    status: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=16),
        nullable=False,
        server_default=BulkATSBatchStatus.DRAFT.value,
        index=True,
    )

    created_by: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
        index=True,
    )
    updated_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
        onupdate=sqlalchemy_functions.now(),
    )

    __table_args__ = (
        sqlalchemy.CheckConstraint(
            "experience_mode IN ('fresher', 'experienced')",
            name="chk_bulk_ats_batch_experience_mode",
        ),
        sqlalchemy.CheckConstraint(
            "status IN ('draft', 'processing', 'completed', 'failed')",
            name="chk_bulk_ats_batch_status",
        ),
    )
    __mapper_args__ = {"eager_defaults": True}

    candidates = sqlalchemy.orm.relationship(
        "BulkATSCandidate",
        back_populates="batch",
        cascade="all, delete",
        passive_deletes=True,
    )
    creator = sqlalchemy.orm.relationship(
        "User", foreign_keys=[created_by], lazy="joined", viewonly=True
    )


class BulkATSCandidate(Base):  # type: ignore
    """One resume inside a batch and its screening lifecycle.

    Duplicate detection is per-batch (not global) via the unique constraint on
    (batch_id, file_sha256): the same person's resume may legitimately appear
    in two different hiring rounds, but re-uploading the identical file into
    the same batch (a retry, or two overlapping Excel rows) should not create
    two rows. ``candidate_key`` backs the same per-batch uniqueness for the
    email/candidate-id or resume_url dedup rule when the file hash alone is
    not available (e.g. a link that hasn't been fetched yet).
    """

    __tablename__ = "bulk_ats_candidate"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        primary_key=True, autoincrement="auto"
    )
    batch_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("bulk_ats_batch.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    candidate_name: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=256), nullable=True
    )
    email_or_candidate_id: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=256), nullable=True, index=True
    )
    candidate_key: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=320), nullable=True
    )
    source: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=True
    )

    resume_url: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=2048), nullable=True
    )
    s3_key: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=512), nullable=True
    )
    original_filename: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=256), nullable=True
    )
    file_sha256: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=True, index=True
    )

    status: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=16),
        nullable=False,
        server_default=BulkATSCandidateStatus.QUEUED.value,
        index=True,
    )
    error_message: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.Text, nullable=True
    )

    extracted_resume_text: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.Text, nullable=True
    )
    parsed_resume_json: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )
    deterministic_report: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )
    jev_report: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )
    link_report: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )

    final_score: SQLAlchemyMapped[float | None] = sqlalchemy_mapped_column(
        sqlalchemy.Float, nullable=True
    )
    score_band: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=16), nullable=True
    )
    needs_review: SQLAlchemyMapped[bool] = sqlalchemy_mapped_column(
        sqlalchemy.Boolean,
        nullable=False,
        server_default=sqlalchemy.sql.expression.false(),
    )
    warnings: SQLAlchemyMapped[list | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )
    confidence_score: SQLAlchemyMapped[float | None] = sqlalchemy_mapped_column(
        sqlalchemy.Float, nullable=True
    )
    score_lower_bound: SQLAlchemyMapped[float | None] = sqlalchemy_mapped_column(
        sqlalchemy.Float, nullable=True
    )
    score_upper_bound: SQLAlchemyMapped[float | None] = sqlalchemy_mapped_column(
        sqlalchemy.Float, nullable=True
    )

    reviewer_notes: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.Text, nullable=True
    )
    reviewer_decision: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=24), nullable=True
    )
    decision_reason: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.Text, nullable=True
    )
    interview_outcome: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=24), nullable=True
    )
    outcome_recorded_at: SQLAlchemyMapped[datetime.datetime | None] = (
        sqlalchemy_mapped_column(sqlalchemy.DateTime(timezone=True), nullable=True)
    )
    reviewed_by: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    assigned_reviewer_id: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    merged_into_id: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("bulk_ats_candidate.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
        index=True,
    )
    updated_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
        onupdate=sqlalchemy_functions.now(),
    )

    batch = sqlalchemy.orm.relationship("BulkATSBatch", back_populates="candidates")

    __table_args__ = (
        sqlalchemy.UniqueConstraint(
            "batch_id", "file_sha256", name="uq_bulk_ats_candidate_batch_sha256"
        ),
        sqlalchemy.UniqueConstraint(
            "batch_id", "candidate_key", name="uq_bulk_ats_candidate_batch_key"
        ),
        sqlalchemy.CheckConstraint(
            "status IN ('queued', 'parsing', 'evaluating', 'completed', 'needs_review', 'failed')",
            name="chk_bulk_ats_candidate_status",
        ),
        sqlalchemy.Index("ix_bulk_ats_candidate_batch_status", "batch_id", "status"),
        sqlalchemy.Index(
            "ix_bulk_ats_candidate_batch_score", "batch_id", "final_score"
        ),
        sqlalchemy.CheckConstraint(
            "reviewer_decision IS NULL OR reviewer_decision IN ('shortlist', 'hold', 'not_shortlisted')",
            name="chk_bulk_ats_candidate_reviewer_decision",
        ),
        sqlalchemy.CheckConstraint(
            "interview_outcome IS NULL OR interview_outcome IN "
            "('not_interviewed', 'interviewed', 'advanced', 'rejected', 'hired', 'withdrew')",
            name="chk_bulk_ats_candidate_interview_outcome",
        ),
    )
    __mapper_args__ = {"eager_defaults": True}


class BulkATSAuditEvent(Base):  # type: ignore
    """Append-only security and recruiter-action audit trail."""

    __tablename__ = "bulk_ats_audit_event"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        primary_key=True, autoincrement="auto"
    )
    batch_id: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("bulk_ats_batch.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    candidate_id: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("bulk_ats_candidate.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_id: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=False, index=True
    )
    event_metadata: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(
        "metadata", JSONB, nullable=True
    )
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
        index=True,
    )

    __mapper_args__ = {"eager_defaults": True}


class BulkATSDriveCredential(Base):  # type: ignore
    """One encrypted Google Drive refresh credential per recruiter.

    Access tokens are intentionally never stored. The worker exchanges this
    refresh token for a short-lived access token only when it must fetch a
    private Drive resume.
    """

    __tablename__ = "bulk_ats_drive_credential"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        primary_key=True, autoincrement="auto"
    )
    user_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    encrypted_refresh_token: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.Text, nullable=False
    )
    google_email: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=254), nullable=True
    )
    scopes: SQLAlchemyMapped[list | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )
    key_id: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=False
    )
    last_used_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=True
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

    __mapper_args__ = {"eager_defaults": True}
