import datetime
import logging
import time
from typing import Any

import sqlalchemy
from sqlalchemy.exc import IntegrityError

from src.models.db.interview_question import InterviewQuestion
from src.models.db.question_attempt import QuestionAttempt
from src.repository.crud.base import BaseCRUDRepository

logger = logging.getLogger(__name__)


class AttemptIdempotencyConflict(ValueError):
    """A client key was reused for a different question in one interview."""


class QuestionAttemptCRUDRepository(BaseCRUDRepository):
    async def create_attempt(
        self,
        *,
        interview_id: int,
        question_id: int,
        question_text: str,
        client_attempt_id: str | None = None,
    ) -> QuestionAttempt:
        attempt, _ = await self.create_or_get_attempt(
            interview_id=interview_id,
            question_id=question_id,
            question_text=question_text,
            client_attempt_id=client_attempt_id,
        )
        return attempt

    async def create_or_get_attempt(
        self,
        *,
        interview_id: int,
        question_id: int,
        question_text: str,
        client_attempt_id: str | None = None,
    ) -> tuple[QuestionAttempt, bool]:
        db_start = time.perf_counter()

        if client_attempt_id:
            existing = await self.get_by_client_attempt_id(
                interview_id=interview_id, client_attempt_id=client_attempt_id
            )
            if existing is not None:
                if existing.question_id != question_id:
                    raise AttemptIdempotencyConflict(
                        "The attempt key is already assigned to another question."
                    )
                return existing, False

        logger.info(
            "Creating question attempt | Interview=%s | Question=%s",
            interview_id,
            question_id,
        )
        """Create a new question attempt linked to a specific question"""
        attempt = QuestionAttempt(
            interview_id=interview_id,
            question_id=question_id,
            question_text=question_text,
            client_attempt_id=client_attempt_id,
        )
        self.async_session.add(attempt)
        try:
            await self.async_session.commit()
        except IntegrityError:
            await self.async_session.rollback()
            if not client_attempt_id:
                raise
            existing = await self.get_by_client_attempt_id(
                interview_id=interview_id, client_attempt_id=client_attempt_id
            )
            if existing is None:
                raise
            if existing.question_id != question_id:
                raise AttemptIdempotencyConflict(
                    "The attempt key is already assigned to another question."
                )
            return existing, False
        await self.async_session.refresh(attempt)
        logger.info(
            "Question attempt created | Attempt=%s | Interview=%s | Question=%s | DBTime=%.2fs",
            attempt.id,
            interview_id,
            question_id,
            time.perf_counter() - db_start,
        )
        return attempt, True

    async def get_by_client_attempt_id(
        self, *, interview_id: int, client_attempt_id: str
    ) -> QuestionAttempt | None:
        stmt = sqlalchemy.select(QuestionAttempt).where(
            QuestionAttempt.interview_id == interview_id,
            QuestionAttempt.client_attempt_id == client_attempt_id,
        )
        query = await self.async_session.execute(stmt)
        return query.scalar_one_or_none()

    async def save_audio_asset(
        self,
        *,
        question_attempt_id: int,
        audio_url: str,
        checksum: str,
        immutable: bool = False,
        follow_up_expected: bool = True,
    ) -> QuestionAttempt | None:
        if immutable:
            result = await self.async_session.execute(
                sqlalchemy.select(QuestionAttempt).where(QuestionAttempt.id == question_attempt_id)
                .with_for_update().execution_options(populate_existing=True)
            )
            attempt = result.scalar_one_or_none()
        else:
            attempt = await self.get_by_id(question_attempt_id=question_attempt_id)
        if attempt is None:
            return None
        if immutable and attempt.answer_saved_at is not None:
            if attempt.answer_checksum != checksum:
                raise ValueError("ASSESSMENT_ANSWER_COMMITTED")
            return attempt
        if attempt.answer_checksum != checksum:
            attempt.answer_version = (attempt.answer_version or 0) + 1
        attempt.audio_url = audio_url
        attempt.answer_checksum = checksum
        attempt.answer_kind = "audio"
        attempt.answer_saved_at = datetime.datetime.now(datetime.timezone.utc)
        attempt.attempt_state = "answer_saved"
        attempt.skipped_at = None
        attempt.follow_up_disposition = "pending" if follow_up_expected else "not_applicable"
        attempt.follow_up_resolved_at = None
        attempt.transcription = None
        # The HTTP request owns durable storage only.  Transcription is queued
        # after this commit, so the row must stay pending until a worker
        # actually claims it.  Calling it processing here made a Redis outage
        # indistinguishable from an active Whisper job and left the UI polling
        # forever.
        attempt.transcription_status = "pending"
        attempt.transcription_error = None
        attempt.transcription_updated_at = datetime.datetime.now(datetime.timezone.utc)
        attempt.analysis_json = None
        attempt.feedback = None
        await self.async_session.commit()
        await self.async_session.refresh(attempt)
        return attempt

    async def mark_transcription_pending(
        self, *, question_attempt_id: int, checksum: str
    ) -> QuestionAttempt | None:
        """Reset one still-current audio answer so it can be queued again."""
        stmt = (
            sqlalchemy.update(QuestionAttempt)
            .where(
                QuestionAttempt.id == question_attempt_id,
                QuestionAttempt.answer_checksum == checksum,
                QuestionAttempt.answer_kind == "audio",
                sqlalchemy.or_(
                    QuestionAttempt.transcription_status.is_(None),
                    QuestionAttempt.transcription_status.in_(
                        ("pending", "processing", "failed")
                    ),
                ),
            )
            .values(
                transcription_status="pending",
                transcription_error=None,
                transcription_updated_at=datetime.datetime.now(datetime.timezone.utc),
            )
            .returning(QuestionAttempt)
        )
        attempt = (await self.async_session.execute(stmt)).scalar_one_or_none()
        await self.async_session.commit()
        if attempt is not None:
            await self.async_session.refresh(attempt)
        return attempt

    async def mark_transcription_processing(
        self, *, question_attempt_id: int, checksum: str
    ) -> QuestionAttempt | None:
        """Mark the exact answer version a worker is about to process."""
        stmt = (
            sqlalchemy.update(QuestionAttempt)
            .where(
                QuestionAttempt.id == question_attempt_id,
                QuestionAttempt.answer_checksum == checksum,
                QuestionAttempt.answer_kind == "audio",
                sqlalchemy.or_(
                    QuestionAttempt.transcription_status.is_(None),
                    QuestionAttempt.transcription_status == "pending",
                ),
            )
            .values(
                transcription_status="processing",
                transcription_error=None,
                transcription_updated_at=datetime.datetime.now(datetime.timezone.utc),
            )
            .returning(QuestionAttempt)
        )
        attempt = (await self.async_session.execute(stmt)).scalar_one_or_none()
        await self.async_session.commit()
        if attempt is not None:
            await self.async_session.refresh(attempt)
        return attempt

    async def mark_transcription_complete(
        self, *, question_attempt_id: int, checksum: str
    ) -> QuestionAttempt | None:
        """Publish a transcript only after follow-up processing has finished."""
        stmt = (
            sqlalchemy.update(QuestionAttempt)
            .where(
                QuestionAttempt.id == question_attempt_id,
                QuestionAttempt.answer_checksum == checksum,
                QuestionAttempt.transcription.is_not(None),
            )
            .values(
                transcription_status="transcribed",
                transcription_error=None,
                transcription_updated_at=datetime.datetime.now(datetime.timezone.utc),
            )
            .returning(QuestionAttempt)
        )
        attempt = (await self.async_session.execute(stmt)).scalar_one_or_none()
        await self.async_session.commit()
        if attempt is not None:
            await self.async_session.refresh(attempt)
        return attempt

    async def mark_transcription_failed(
        self, *, question_attempt_id: int, checksum: str, error: str
    ) -> QuestionAttempt | None:
        stmt = (
            sqlalchemy.update(QuestionAttempt)
            .where(
                QuestionAttempt.id == question_attempt_id,
                QuestionAttempt.answer_checksum == checksum,
            )
            .values(
                transcription_status="failed",
                transcription_error=error[:1000],
                transcription_updated_at=datetime.datetime.now(datetime.timezone.utc),
            )
            .returning(QuestionAttempt)
        )
        attempt = (await self.async_session.execute(stmt)).scalar_one_or_none()
        await self.async_session.commit()
        if attempt is not None:
            await self.async_session.refresh(attempt)
        return attempt

    async def lock_by_id(self, *, question_attempt_id: int) -> QuestionAttempt | None:
        stmt = (
            sqlalchemy.select(QuestionAttempt)
            .where(QuestionAttempt.id == question_attempt_id)
            .with_for_update()
        )
        return (await self.async_session.execute(stmt)).scalar_one_or_none()

    async def list_by_interview_for_update(
        self, *, interview_id: int
    ) -> list[QuestionAttempt]:
        """Lock attempts in a stable order while navigation is committed."""
        stmt = (
            sqlalchemy.select(QuestionAttempt)
            .where(QuestionAttempt.interview_id == interview_id)
            .order_by(QuestionAttempt.id.asc())
            .with_for_update()
        )
        return list((await self.async_session.execute(stmt)).scalars().all())

    async def resolve_follow_up(
        self,
        *,
        question_attempt_id: int,
        disposition: str,
    ) -> QuestionAttempt | None:
        stmt = (
            sqlalchemy.update(QuestionAttempt)
            .where(
                QuestionAttempt.id == question_attempt_id,
                sqlalchemy.or_(
                    QuestionAttempt.follow_up_disposition.is_(None),
                    QuestionAttempt.follow_up_disposition != "skipped",
                ),
            )
            .values(
                follow_up_disposition=disposition,
                follow_up_resolved_at=datetime.datetime.now(datetime.timezone.utc),
            )
            .returning(QuestionAttempt)
        )
        attempt = (await self.async_session.execute(stmt)).scalar_one_or_none()
        await self.async_session.commit()
        if attempt is not None:
            await self.async_session.refresh(attempt)
        return attempt

    async def create_batch(
        self,
        *,
        interview_id: int,
        questions: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> list[QuestionAttempt]:
        created: list[QuestionAttempt] = []
        for q in questions:
            qa = QuestionAttempt(interview_id=interview_id, question_text=str(q))
            if metadata is not None:
                qa.analysis_json = {"generation": metadata}
            self.async_session.add(qa)
            created.append(qa)
        await self.async_session.commit()
        for qa in created:
            await self.async_session.refresh(qa)
        return created

    async def list_by_interview(self, *, interview_id: int) -> list[QuestionAttempt]:
        """Get the latest attempt for each question in an interview, ordered by question order.

        When a question has multiple attempts (reattempts), only the latest attempt
        (highest attempt id) is returned. This ensures:
        1. Questions appear in their original order (by InterviewQuestion.order)
        2. Only one attempt per question is returned (the most recent one)
        """
        # Subquery to get the max attempt id for each question
        latest_attempt_subq = (
            sqlalchemy.select(
                QuestionAttempt.question_id,
                sqlalchemy.func.max(QuestionAttempt.id).label("max_id"),
            )
            .where(QuestionAttempt.interview_id == interview_id)
            .group_by(QuestionAttempt.question_id)
            .subquery()
        )

        # Main query: join with subquery to get only the latest attempt per question
        stmt = (
            sqlalchemy.select(QuestionAttempt)
            .join(
                latest_attempt_subq, QuestionAttempt.id == latest_attempt_subq.c.max_id
            )
            .join(
                InterviewQuestion, QuestionAttempt.question_id == InterviewQuestion.id
            )
            .where(QuestionAttempt.interview_id == interview_id)
            .order_by(InterviewQuestion.order.asc())
        )
        query = await self.async_session.execute(statement=stmt)
        rows = query.scalars().all()
        logger.debug(
            "Loaded interview attempts | Interview=%s | Count=%d",
            interview_id,
            len(rows),
        )
        return list(rows)

    async def list_by_interview_cursor(
        self, *, interview_id: int, limit: int, cursor_id: int | None
    ) -> tuple[list[QuestionAttempt], int | None]:
        """Get the latest attempt for each question with cursor pagination, ordered by question order.

        When a question has multiple attempts (reattempts), only the latest attempt
        (highest attempt id) is returned. Cursor is based on InterviewQuestion.order for stable pagination.
        """
        # Subquery to get the max attempt id for each question
        latest_attempt_subq = (
            sqlalchemy.select(
                QuestionAttempt.question_id,
                sqlalchemy.func.max(QuestionAttempt.id).label("max_id"),
            )
            .where(QuestionAttempt.interview_id == interview_id)
            .group_by(QuestionAttempt.question_id)
            .subquery()
        )

        # Main query: join with subquery to get only the latest attempt per question
        stmt = (
            sqlalchemy.select(QuestionAttempt)
            .join(
                latest_attempt_subq, QuestionAttempt.id == latest_attempt_subq.c.max_id
            )
            .join(
                InterviewQuestion, QuestionAttempt.question_id == InterviewQuestion.id
            )
            .where(QuestionAttempt.interview_id == interview_id)
        )
        if cursor_id is not None:
            # Use question order for cursor, not attempt id (more stable for pagination)
            cursor_order_subq = (
                sqlalchemy.select(InterviewQuestion.order)
                .join(
                    QuestionAttempt, QuestionAttempt.question_id == InterviewQuestion.id
                )
                .where(QuestionAttempt.id == cursor_id)
                .scalar_subquery()
            )
            stmt = stmt.where(InterviewQuestion.order > cursor_order_subq)
        stmt = stmt.order_by(InterviewQuestion.order.asc()).limit(limit + 1)
        query = await self.async_session.execute(statement=stmt)
        rows = list(query.scalars().all())
        next_cursor: int | None = None
        if len(rows) > limit:
            next_cursor = rows[-1].id
            rows = rows[:limit]
        return rows, next_cursor

    async def get_by_id(self, *, question_attempt_id: int) -> QuestionAttempt | None:
        db_start = time.perf_counter()
        """Get a question attempt by ID"""
        stmt = sqlalchemy.select(QuestionAttempt).where(
            QuestionAttempt.id == question_attempt_id
        )
        query = await self.async_session.execute(statement=stmt)
        result = query.scalar_one_or_none()
        logger.info(
            "Fetched question attempt | Attempt=%s | Found=%s | DBTime=%.2fs",
            question_attempt_id,
            result is not None,
            time.perf_counter() - db_start,
        )

        return result

    async def get_by_id_and_user(
        self, *, question_attempt_id: int, user_id: int
    ) -> QuestionAttempt | None:
        db_start = time.perf_counter()
        """Get a question attempt by ID, ensuring it belongs to the specified user"""
        stmt = (
            sqlalchemy.select(QuestionAttempt)
            .join(QuestionAttempt.interview)
            .where(
                QuestionAttempt.id == question_attempt_id,
                QuestionAttempt.interview.has(user_id=user_id),
            )
        )
        query = await self.async_session.execute(statement=stmt)
        result = query.scalar_one_or_none()

        logger.info(
            "Verified question attempt ownership | Attempt=%s | User=%s | Found=%s | DBTime=%.2fs",
            question_attempt_id,
            user_id,
            result is not None,
            time.perf_counter() - db_start,
        )

        return result

    async def update_audio_transcription(
        self,
        *,
        question_attempt_id: int,
        audio_url: str,
        transcription: dict,
        checksum: str | None = None,
        finalize: bool = True,
    ) -> QuestionAttempt | None:
        db_start = time.perf_counter()

        logger.info(
            "Updating transcription | Attempt=%s",
            question_attempt_id,
        )
        """Update a question attempt with audio URL and transcription data"""
        conditions = [QuestionAttempt.id == question_attempt_id]
        if checksum is not None:
            conditions.append(QuestionAttempt.answer_checksum == checksum)
        stmt = (
            sqlalchemy.update(QuestionAttempt)
            .where(*conditions)
            .values(
                audio_url=audio_url,
                transcription=transcription,
                transcription_status="transcribed" if finalize else "processing",
                transcription_error=None,
                transcription_updated_at=datetime.datetime.now(datetime.timezone.utc),
            )
            .returning(QuestionAttempt)
        )
        question_attempt = (await self.async_session.execute(stmt)).scalar_one_or_none()
        await self.async_session.commit()

        if question_attempt:
            await self.async_session.refresh(question_attempt)
            logger.info(
                "Transcription saved | Attempt=%s | Audio=%s | TranscriptChars=%d | DBTime=%.2fs",
                question_attempt_id,
                bool(audio_url),
                len(transcription.get("text", "")),
                time.perf_counter() - db_start,
            )
        else:
            logger.warning(
                "Question attempt missing or superseded while saving transcription | Attempt=%s",
                question_attempt_id,
            )

        return question_attempt

    async def get_follow_up_payload_for_attempt(
        self, *, question_attempt_id: int
    ) -> dict[str, Any] | None:
        """Return the generated child prompt for a completed parent answer.

        Follow-up creation is idempotent in ``FollowUpService``.  Looking it up
        from authoritative rows lets a browser reconnect to background work
        without keeping transient worker metadata in Redis or in process.
        """
        parent = await self.get_by_id(question_attempt_id=question_attempt_id)
        if parent is None or parent.question_id is None:
            return None
        question_stmt = (
            sqlalchemy.select(InterviewQuestion)
            .where(InterviewQuestion.parent_question_id == parent.question_id)
            .order_by(InterviewQuestion.id.asc())
            .limit(1)
        )
        question = (
            await self.async_session.execute(question_stmt)
        ).scalar_one_or_none()
        if question is None:
            return None
        attempt_stmt = (
            sqlalchemy.select(QuestionAttempt)
            .where(QuestionAttempt.question_id == question.id)
            .order_by(QuestionAttempt.id.asc())
            .limit(1)
        )
        follow_up_attempt = (
            await self.async_session.execute(attempt_stmt)
        ).scalar_one_or_none()
        if follow_up_attempt is None:
            return None
        return {
            "interview_question_id": question.id,
            "question_attempt_id": follow_up_attempt.id,
            "parent_question_id": parent.question_id,
            "text": question.text,
            "strategy": question.follow_up_strategy,
        }

    async def update_analysis_json(
        self, *, question_attempt_id: int, analysis_json: dict
    ) -> QuestionAttempt | None:
        db_start = time.perf_counter()
        logger.info(
            "Saving analysis JSON | Attempt=%s",
            question_attempt_id,
        )
        """Update a question attempt with analysis results"""
        stmt = sqlalchemy.select(QuestionAttempt).where(
            QuestionAttempt.id == question_attempt_id
        )
        query = await self.async_session.execute(statement=stmt)
        question_attempt = query.scalar_one_or_none()

        if question_attempt:
            question_attempt.analysis_json = analysis_json
            await self.async_session.commit()
            await self.async_session.refresh(question_attempt)
            logger.info(
                "Analysis JSON saved | Attempt=%s | DBTime=%.2fs",
                question_attempt_id,
                time.perf_counter() - db_start,
            )
        else:
            logger.warning(
                "Question attempt not found while saving analysis JSON | Attempt=%s",
                question_attempt_id,
            )
        return question_attempt

    async def get_first_by_question_id(
        self, *, question_id: int
    ) -> QuestionAttempt | None:
        """Fetch the earliest attempt for a given question (useful for metadata lookups)."""
        stmt = (
            sqlalchemy.select(QuestionAttempt)
            .where(QuestionAttempt.question_id == question_id)
            .order_by(QuestionAttempt.id.asc())
            .limit(1)
        )
        query = await self.async_session.execute(statement=stmt)
        return query.scalar_one_or_none()
