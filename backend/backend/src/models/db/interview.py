import datetime

import sqlalchemy
from sqlalchemy.orm import Mapped as SQLAlchemyMapped, mapped_column as sqlalchemy_mapped_column, relationship
from sqlalchemy.sql import functions as sqlalchemy_functions

from src.repository.table import Base


class Interview(Base):  # type: ignore
    __tablename__ = "interview"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True, autoincrement="auto")
    user_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    track: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(length=64), nullable=False, index=True)
    difficulty: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=16), nullable=False, index=True, default="medium"
    )
    status: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.String(length=32), nullable=False, index=True)
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=False, server_default=sqlalchemy_functions.now()
    )
    completed_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=True, index=True
    )
    duration_seconds: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(sqlalchemy.Integer, nullable=True)
    job_profile_id: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("job_profile.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # A snapshot of JobProfile.interview_mode at the moment this interview was
    # created, not a live read of it. job_profile_id goes NULL on SET NULL if
    # the profile is later deleted, and a facilitator can edit a published
    # profile's mode at any time - without this column, either event would
    # retroactively reclassify every interview already sat against that role,
    # in history and in analytics, after the fact. "practice" for an interview
    # with no job profile at all: free generation is practice by definition.
    interview_mode: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=16), nullable=False, server_default="practice", index=True
    )
    # Set only by the proctoring-violation endpoint, and only for an
    # interview_mode="assessment" interview - a student leaving the tab or
    # exiting fullscreen during a formal sitting ends it immediately. True
    # here is itself the facilitator-visible remark; the reason names which
    # violation did it. completed_at doubles as "when the violation fired";
    # no separate timestamp column for a fact the status change already dates.
    proctoring_terminated: SQLAlchemyMapped[bool] = sqlalchemy_mapped_column(
        sqlalchemy.Boolean, nullable=False, server_default=sqlalchemy.false(), index=True
    )
    proctoring_violation_reason: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=32), nullable=True
    )

    user = relationship("User", back_populates="interviews")
    job_profile = relationship("JobProfile")
    questions = relationship(
        "InterviewQuestion", back_populates="interview", cascade="all, delete-orphan", passive_deletes=True
    )
    question_attempts = relationship(
        "QuestionAttempt", back_populates="interview", cascade="all, delete-orphan", passive_deletes=True
    )

    __mapper_args__ = {"eager_defaults": True}


