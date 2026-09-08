import datetime

import sqlalchemy
from sqlalchemy.orm import Mapped as SQLAlchemyMapped, mapped_column as sqlalchemy_mapped_column, relationship
from sqlalchemy.sql import functions as sqlalchemy_functions

from src.repository.table import Base


class InterviewQuestion(Base):  # type: ignore
    __tablename__ = "interview_question"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True, autoincrement="auto")
    text: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.Text, nullable=False)
    topic: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(length=128), nullable=True)
    category: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(length=32), nullable=True)
    status: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=32), nullable=False, default="pending", index=True
    )
    order: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(sqlalchemy.Integer, nullable=False, index=True)
    interview_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("interview.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resume_used: SQLAlchemyMapped[bool] = sqlalchemy_mapped_column(
        sqlalchemy.Boolean, nullable=False, default=False, index=True
    )
    is_follow_up: SQLAlchemyMapped[bool] = sqlalchemy_mapped_column(
        sqlalchemy.Boolean, nullable=False, default=False, index=True
    )
    parent_question_id: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("interview_question.id", ondelete="SET NULL"), nullable=True, index=True
    )
    follow_up_strategy: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=64), nullable=True
    )
    audio_url: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=512), nullable=True
    )
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=False, server_default=sqlalchemy_functions.now()
    )

    interview = relationship("Interview", back_populates="questions")
    parent_question = relationship("InterviewQuestion", remote_side="InterviewQuestion.id", back_populates="follow_up_questions")
    follow_up_questions = relationship(
        "InterviewQuestion",
        back_populates="parent_question",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    question_attempts = relationship(
        "QuestionAttempt", back_populates="question", cascade="all, delete-orphan", passive_deletes=True
    )
    supplement = relationship(
        "QuestionSupplement",
        back_populates="question",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    __mapper_args__ = {"eager_defaults": True}
