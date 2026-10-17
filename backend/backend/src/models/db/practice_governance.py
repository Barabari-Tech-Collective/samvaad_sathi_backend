"""Governance records for staff access to Practice interview evidence.

These tables deliberately store decisions and metadata, never recordings,
transcripts, prompts, or generated reports.  The interview tables remain the
source of evidence; this module records why a person may see or act on it.
"""

import datetime

import sqlalchemy
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped as SQLAlchemyMapped
from sqlalchemy.orm import mapped_column as sqlalchemy_mapped_column
from sqlalchemy.sql import functions as sqlalchemy_functions

from src.repository.table import Base


class PracticeConsent(Base):  # type: ignore
    __tablename__ = "practice_consent"
    __table_args__ = (
        sqlalchemy.UniqueConstraint(
            "user_id", "purpose", name="uq_practice_consent_user_purpose"
        ),
    )

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True)
    user_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    purpose: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(32), index=True
    )
    granted: SQLAlchemyMapped[bool] = sqlalchemy_mapped_column(
        sqlalchemy.Boolean, nullable=False, server_default=sqlalchemy.false()
    )
    policy_version: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(32), nullable=False
    )
    granted_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True)
    )
    withdrawn_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True)
    )
    expires_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), index=True
    )
    updated_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
        onupdate=sqlalchemy_functions.now(),
    )


class PracticeAssignment(Base):  # type: ignore
    __tablename__ = "practice_assignment"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True)
    student_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    assigned_by: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="RESTRICT"), index=True
    )
    module: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(32))
    reason: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.Text)
    note: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.Text)
    due_date: SQLAlchemyMapped[datetime.date | None] = sqlalchemy_mapped_column(
        sqlalchemy.Date
    )
    status: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(24), nullable=False, server_default="assigned", index=True
    )
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
    )


class PracticeShortlist(Base):  # type: ignore
    __tablename__ = "practice_shortlist"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True)
    name: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(160))
    opportunity: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(200)
    )
    evidence_rules: SQLAlchemyMapped[dict] = sqlalchemy_mapped_column(
        JSONB, nullable=False
    )
    status: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(24), nullable=False, server_default="draft", index=True
    )
    version: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, nullable=False, server_default="1"
    )
    created_by: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="RESTRICT"), index=True
    )
    locked_by: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="SET NULL")
    )
    locked_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True)
    )
    expires_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), index=True
    )
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
    )


class PracticeShortlistEntry(Base):  # type: ignore
    __tablename__ = "practice_shortlist_entry"
    __table_args__ = (
        sqlalchemy.UniqueConstraint(
            "shortlist_id", "student_id", name="uq_practice_shortlist_student"
        ),
    )

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True)
    shortlist_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("practice_shortlist.id", ondelete="CASCADE"), index=True
    )
    student_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    decision: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(16), nullable=False
    )
    reason: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.Text)
    evidence_snapshot: SQLAlchemyMapped[dict] = sqlalchemy_mapped_column(
        JSONB, nullable=False
    )
    decided_by: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="RESTRICT")
    )
    decided_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
    )


class PracticeDashboardAuditEvent(Base):  # type: ignore
    __tablename__ = "practice_dashboard_audit_event"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True)
    actor_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="RESTRICT"), index=True
    )
    action: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(64), index=True
    )
    purpose: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(32))
    target_type: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(32))
    target_id: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(128))
    reason: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.Text)
    event_metadata: SQLAlchemyMapped[dict] = sqlalchemy_mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
        index=True,
    )


class PracticeFeatureControl(Base):  # type: ignore
    __tablename__ = "practice_feature_control"

    key: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(64), primary_key=True
    )
    enabled: SQLAlchemyMapped[bool] = sqlalchemy_mapped_column(
        sqlalchemy.Boolean, nullable=False, server_default=sqlalchemy.true()
    )
    reason: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.Text)
    updated_by: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="RESTRICT")
    )
    updated_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
        onupdate=sqlalchemy_functions.now(),
    )
