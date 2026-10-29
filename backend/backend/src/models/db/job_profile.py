from __future__ import annotations
import datetime
import sqlalchemy
import sqlalchemy.orm
from sqlalchemy.orm import Mapped as SQLAlchemyMapped, mapped_column as sqlalchemy_mapped_column
from sqlalchemy.sql import functions as sqlalchemy_functions
from sqlalchemy.dialects.postgresql import JSONB
from enum import Enum as _Enum
from src.repository.table import Base

class JobProfileStatus(str, _Enum):
    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    PUBLISHED = "published"
    CHANGES_REQUESTED = "changes_requested"


class JobProfileInterviewMode(str, _Enum):
    """Who the result is for, chosen by the facilitator at creation.

    A role's questions are the facilitator's either way - this never relaxes
    question integrity (see interview_eligibility.py, which does not branch on
    this field at all). It only decides which entry point on the student side
    surfaces the role, and whether a completed interview is something a
    reviewer evaluates for shortlisting. PRACTICE is never scored or shared;
    ASSESSMENT is.
    """

    PRACTICE = "practice"
    ASSESSMENT = "assessment"


class JobProfile(Base):
    __tablename__ = "job_profile"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True, autoincrement="auto")
    job_name: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(length=160), nullable=False, index=True)
    job_description: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.Text, nullable=False)
    experience_level: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(length=64), nullable=True)
    company_name: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(length=256), nullable=True)
    skills: SQLAlchemyMapped[list[str] | None] = sqlalchemy_mapped_column(JSONB, nullable=True)
    additional_context: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.Text, nullable=True)
    category: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(length=160), nullable=True)
    employment_type: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(length=64), nullable=True)
    status: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(length=32), nullable=False, server_default="draft")
    # Existing rows default to "assessment": every job profile created before
    # this field existed was, in effect, an assessment - nothing else was ever
    # offered - so this is the only backfill that does not change behaviour.
    interview_mode: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=16), nullable=False, server_default="assessment", index=True
    )
    # Null identifies a legacy template. New authoring explicitly sets a count.
    questions_per_interview: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, nullable=True
    )
    authoring_mode: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(16), nullable=True)
    content_revision: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(sqlalchemy.Integer, nullable=False, default=0, server_default="0")
    published_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(sqlalchemy.DateTime(timezone=True), nullable=True)
    publication_snapshot: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(JSONB, nullable=True)
    parent_profile_id: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(sqlalchemy.ForeignKey("job_profile.id"), nullable=True)
    generation_token: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(64), nullable=True)
    generation_result_count: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(sqlalchemy.Integer, nullable=True)
    generation_state: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(32), nullable=True)
    admin_comment: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.Text, nullable=True)
    submitted_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(sqlalchemy.DateTime(timezone=True), nullable=True)
    
    created_by: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=False, server_default=sqlalchemy_functions.now(), index=True
    )
    updated_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
        onupdate=sqlalchemy_functions.now(),
    )

    __mapper_args__ = {"eager_defaults": True}

    questions = sqlalchemy.orm.relationship(
        "JobProfileQuestion",
        back_populates="job_profile",
        primaryjoin="JobProfile.id == foreign(JobProfileQuestion.job_profile_id)",
        lazy="selectin",
        cascade="all, delete",
        passive_deletes=True
    )

    @property
    def easy_questions(self) -> int:
        return sum(1 for q in self.questions if getattr(q, 'level', 0) == 1)

    @property
    def medium_questions(self) -> int:
        return sum(1 for q in self.questions if getattr(q, 'level', 0) == 2)

    @property
    def hard_questions(self) -> int:
        return sum(1 for q in self.questions if getattr(q, 'level', 0) == 3)

    @property
    def expert_questions(self) -> int:
        return sum(1 for q in self.questions if getattr(q, 'level', 0) == 4)

    @property
    def total_questions(self) -> int:
        return len(self.questions)

    @property
    def title(self) -> str:
        return self.job_name
    
    @title.setter
    def title(self, value: str):
        self.job_name = value

    @property
    def description(self) -> str | None:
        return self.job_description
    
    @description.setter
    def description(self, value: str | None):
        self.job_description = value or ""
