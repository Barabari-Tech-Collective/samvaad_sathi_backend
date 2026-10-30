"""An opening that candidates are considered for (architecture pack F25).

One opportunity owns its requirements and evidence rules, its candidates and its round plan.
Shortlists, human interviews and resume-screening batches point back to it. It always lives
inside one course, so the existing course and placement access rules apply unchanged.
"""

import datetime

import sqlalchemy
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import functions as sqlalchemy_functions

from src.repository.table import Base


class Opportunity(Base):
    __tablename__ = "opportunity"
    __table_args__ = (
        sqlalchemy.UniqueConstraint(
            "created_by", "request_key", name="uq_opportunity_request"
        ),
        sqlalchemy.CheckConstraint(
            "status IN ('open', 'closed', 'archived')", name="ck_opportunity_status"
        ),
        sqlalchemy.CheckConstraint(
            "max_evidence_age_days BETWEEN 1 AND 365", name="ck_opportunity_evidence_age"
        ),
        sqlalchemy.CheckConstraint(
            "min_assessments BETWEEN 0 AND 20", name="ck_opportunity_min_assessments"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[str] = mapped_column(sqlalchemy.String(128), index=True)
    title: Mapped[str] = mapped_column(sqlalchemy.String(160))
    organisation: Mapped[str] = mapped_column(sqlalchemy.String(160))
    # The job role (an assessment template) candidates are measured against, if any.
    job_profile_id: Mapped[int | None] = mapped_column(
        sqlalchemy.ForeignKey("job_profile.id", ondelete="SET NULL"), index=True
    )
    requirements: Mapped[str | None] = mapped_column(sqlalchemy.Text)
    max_evidence_age_days: Mapped[int] = mapped_column(server_default="90")
    min_assessments: Mapped[int] = mapped_column(server_default="1")
    # Bumped whenever the evidence rules change, so a decision can name the rules it used.
    eligibility_version: Mapped[int] = mapped_column(server_default="1")
    status: Mapped[str] = mapped_column(
        sqlalchemy.String(16), server_default="open", index=True
    )
    created_by: Mapped[int] = mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="RESTRICT"), index=True
    )
    request_key: Mapped[str] = mapped_column(sqlalchemy.String(36))
    request_hash: Mapped[str] = mapped_column(sqlalchemy.String(64))
    created_at: Mapped[datetime.datetime] = mapped_column(
        sqlalchemy.DateTime(timezone=True), server_default=sqlalchemy_functions.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        sqlalchemy.DateTime(timezone=True),
        server_default=sqlalchemy_functions.now(),
        onupdate=sqlalchemy_functions.now(),
    )


class OpportunityCandidate(Base):
    __tablename__ = "opportunity_candidate"
    __table_args__ = (
        sqlalchemy.UniqueConstraint(
            "opportunity_id", "student_id", name="uq_opportunity_candidate"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        sqlalchemy.ForeignKey("opportunity.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int] = mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    added_by: Mapped[int] = mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="RESTRICT")
    )
    added_at: Mapped[datetime.datetime] = mapped_column(
        sqlalchemy.DateTime(timezone=True), server_default=sqlalchemy_functions.now()
    )


class OpportunityRound(Base):
    """One planned step: an AI interview (an assessment template) or a human interview."""

    __tablename__ = "opportunity_round"
    __table_args__ = (
        sqlalchemy.CheckConstraint(
            "kind IN ('ai', 'human')", name="ck_opportunity_round_kind"
        ),
        sqlalchemy.CheckConstraint(
            "kind = 'human' OR job_profile_id IS NOT NULL",
            name="ck_opportunity_round_ai_template",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        sqlalchemy.ForeignKey("opportunity.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column()
    kind: Mapped[str] = mapped_column(sqlalchemy.String(8))
    name: Mapped[str] = mapped_column(sqlalchemy.String(160))
    job_profile_id: Mapped[int | None] = mapped_column(
        sqlalchemy.ForeignKey("job_profile.id", ondelete="SET NULL")
    )
    created_by: Mapped[int] = mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        sqlalchemy.DateTime(timezone=True), server_default=sqlalchemy_functions.now()
    )
