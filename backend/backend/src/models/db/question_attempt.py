import datetime
import enum

import sqlalchemy
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped as SQLAlchemyMapped, mapped_column as sqlalchemy_mapped_column, relationship
from sqlalchemy.sql import functions as sqlalchemy_functions

from src.repository.table import Base


class TranscriptionStatus(str, enum.Enum):
    """Lifecycle of one answer's transcription.

    Transcription used to run inside the upload request, which pinned a database
    connection for the whole of a Whisper call. With roughly fifteen pooled
    connections available that capped the platform at about fifteen students
    answering at the same time. Moving the work to the arq worker lets the
    request record intent and return, so the client needs somewhere to read
    progress from - this column is it.
    """

    PENDING = "pending"  # accepted and queued, not yet picked up
    PROCESSING = "processing"  # a worker is running it
    TRANSCRIBED = "transcribed"  # finished; `transcription` is populated
    FAILED = "failed"  # terminal; `transcription_error` explains why


#: Statuses the client can stop polling on.
TERMINAL_TRANSCRIPTION_STATUSES = frozenset(
    {TranscriptionStatus.TRANSCRIBED.value, TranscriptionStatus.FAILED.value}
)


class QuestionAttempt(Base):  # type: ignore
    __tablename__ = "question_attempt"

    id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(primary_key=True, autoincrement="auto")
    interview_id: SQLAlchemyMapped[int] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("interview.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: SQLAlchemyMapped[int | None] = sqlalchemy_mapped_column(
        sqlalchemy.ForeignKey("interview_question.id", ondelete="CASCADE"), nullable=True, index=True
    )
    question_text: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(sqlalchemy.Text, nullable=False)
    audio_url: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.String(length=512), nullable=True)
    transcription: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(JSONB, nullable=True)
    analysis_json: SQLAlchemyMapped[dict | None] = sqlalchemy_mapped_column(JSONB, nullable=True)
    feedback: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(sqlalchemy.Text, nullable=True)
    created_at: SQLAlchemyMapped[datetime.datetime] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True), nullable=False, server_default=sqlalchemy_functions.now()
    )

    transcription_status: SQLAlchemyMapped[str] = sqlalchemy_mapped_column(
        sqlalchemy.String(length=16),
        nullable=False,
        index=True,
        default=TranscriptionStatus.PENDING.value,
        server_default=TranscriptionStatus.PENDING.value,
        doc="See TranscriptionStatus. Indexed because the worker sweeps for stalled rows.",
    )
    transcription_error: SQLAlchemyMapped[str | None] = sqlalchemy_mapped_column(
        sqlalchemy.Text,
        nullable=True,
        doc="Why transcription failed, in language that can be shown to a student.",
    )
    transcription_updated_at: SQLAlchemyMapped[datetime.datetime | None] = sqlalchemy_mapped_column(
        sqlalchemy.DateTime(timezone=True),
        nullable=True,
        doc=(
            "When the status last changed. A worker killed mid-job would otherwise "
            "leave a row in `processing` forever, with the student polling an answer "
            "that will never arrive; this makes stalled work detectable and requeueable."
        ),
    )

    interview = relationship("Interview", back_populates="question_attempts")
    question = relationship("InterviewQuestion", back_populates="question_attempts")

    __mapper_args__ = {"eager_defaults": True}


