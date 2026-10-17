import datetime

import sqlalchemy
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped as SQLAlchemyMapped
from sqlalchemy.orm import mapped_column as sqlalchemy_mapped_column
from sqlalchemy.orm import (
    relationship,
)
from sqlalchemy.sql import functions as sqlalchemy_functions

from src.repository.table import Base


class Interview(Base):  # type: ignore
    __tablename__ = "interview"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        primary_key=True, autoincrement="auto"
    )
    user_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    track: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=False, index=True
    )
    difficulty: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=16), nullable=False, index=True, default="medium"
    )
    status: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=32), nullable=False, index=True
    )
    # Optimistic concurrency token for the student-facing question cursor.  It
    # changes only when the server accepts a navigation transition, so two tabs
    # can never silently advance the same interview in different directions.
    state_version: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, nullable=False, default=0, server_default="0"
    )
    current_question_id: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey(
            "interview_question.id",
            name="fk_interview_current_question_id_interview_question",
            ondelete="SET NULL",
            # interview_question already points back to interview. Emitting
            # this named constraint with ALTER lets SQLAlchemy create and drop
            # disposable schemas deterministically despite that valid cycle.
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    question_set_version: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, nullable=False, default=0, server_default="0"
    )
    question_set_hash: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=True
    )
    question_set_frozen_at: SQLAlchemyMapped[datetime.datetime | None] = (
        sqlalchemy_mapped_column(sqlalchemy.DateTime(timezone=True), nullable=True)
    )
    # The most recent transition is retained for immediate request replay.  A
    # duplicated Next tap therefore returns the accepted checkpoint rather
    # than becoming an error or skipping a second question.
    last_transition_id: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=True
    )
    last_transition_response: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(
        JSONB, nullable=True
    )
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=False,
        server_default=sqlalchemy_functions.now(),
    )
    completed_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=True, index=True
    )
    paused_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=True, index=True
    )
    expires_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=True, index=True
    )
    session_lease_hash: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=True
    )
    session_lease_required: SQLAlchemyMapped[bool] = sqlalchemy_mapped_column(
        sqlalchemy.Boolean, nullable=False, server_default=sqlalchemy.false()
    )
    session_lease_acquired_at: SQLAlchemyMapped[datetime.datetime | None] = (
        sqlalchemy_mapped_column(sqlalchemy.DateTime(timezone=True), nullable=True)
    )
    session_lease_expires_at: SQLAlchemyMapped[datetime.datetime | None] = (
        sqlalchemy_mapped_column(
            sqlalchemy.DateTime(timezone=True), nullable=True, index=True
        )
    )
    duration_seconds: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.Integer, nullable=True
    )
    job_profile_id: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("job_profile.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # A snapshot of JobProfile.interview_mode at the moment this interview was
    # created, not a live read of it. job_profile_id goes NULL on SET NULL if
    # the profile is later deleted, and a facilitator can edit a published
    # profile's mode at any time - without this column, either event would
    # retroactively reclassify every interview already sat against that role,
    # in history and in analytics, after the fact. "practice" for an interview
    # with no job profile at all: free generation is practice by definition.
    interview_mode: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=16),
        nullable=False,
        server_default="practice",
        index=True,
    )
    # Set only by the proctoring-violation endpoint, and only for an
    # interview_mode="assessment" interview - a student leaving the tab or
    # exiting fullscreen during a formal sitting ends it immediately. True
    # here is itself the facilitator-visible remark; the reason names which
    # violation did it. completed_at doubles as "when the violation fired";
    # no separate timestamp column for a fact the status change already dates.
    proctoring_terminated: SQLAlchemyMapped[bool] = sqlalchemy_mapped_column(
        sqlalchemy.Boolean,
        nullable=False,
        server_default=sqlalchemy.false(),
        index=True,
    )
    proctoring_violation_reason: SQLAlchemyMapped[str | None] = (
        sqlalchemy_mapped_column(sqlalchemy.String(length=32), nullable=True)
    )

    user = relationship("User", back_populates="interviews")
    job_profile = relationship("JobProfile")
    questions = relationship(
        "InterviewQuestion",
        back_populates="interview",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="InterviewQuestion.interview_id",
    )
    question_attempts = relationship(
        "QuestionAttempt",
        back_populates="interview",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __mapper_args__ = {"eager_defaults": True}
