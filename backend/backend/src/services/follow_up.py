from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession

from src.models.db.interview import Interview
from src.models.db.question_attempt import QuestionAttempt
from src.repository.crud.interview import InterviewCRUDRepository
from src.repository.crud.interview_question import InterviewQuestionCRUDRepository
from src.repository.crud.question import QuestionAttemptCRUDRepository
from src.services.llm import generate_follow_up_question

logger = logging.getLogger(__name__)


class FollowUpService:
    """Generates follow-up questions for adaptive interview flows."""

    def __init__(self, async_session: SQLAlchemyAsyncSession):
        self._async_session = async_session
        self._question_attempt_repo = QuestionAttemptCRUDRepository(async_session=async_session)
        self._interview_question_repo = InterviewQuestionCRUDRepository(async_session=async_session)
        self._interview_repo = InterviewCRUDRepository(async_session=async_session)

    async def handle_transcription_saved(self, question_attempt_id: int) -> dict[str, Any] | None:
        """Trigger follow-up generation after a transcription is persisted."""
        attempt = await self._question_attempt_repo.get_by_id(question_attempt_id=question_attempt_id)
        if not attempt or not attempt.question_id:
            return None
        return await self._maybe_generate_follow_up(attempt=attempt)

    async def _maybe_generate_follow_up(self, attempt: QuestionAttempt) -> dict[str, Any] | None:
        if not attempt.question_id:
            return None

        question = await self._interview_question_repo.get_by_id(question_id=attempt.question_id)  # type: ignore[arg-type]
        if not question or question.is_follow_up or not question.follow_up_strategy:
            return None

        existing = await self._interview_question_repo.get_follow_up_for_parent(parent_question_id=question.id)
        if existing:
            return None

        answer_chunk = self._extract_answer_chunk(transcription=attempt.transcription)
        if not answer_chunk:
            logger.debug("Skipping follow-up generation due to empty transcription chunk for question %s", question.id)
            return None

        interview = await self._interview_repo.get_by_id(interview_id=attempt.interview_id)

        track = interview.track if interview else "general"
        difficulty = interview.difficulty if interview else "medium"

        # Enforce strict question count for Full Stack Developer mode
        if track == "Full Stack Developer":
            unasked_qs = await self._interview_question_repo.get_questions_without_attempts(interview_id=attempt.interview_id)
            unasked_base_qs = [q for q in unasked_qs if not getattr(q, "is_follow_up", False)]
            if not unasked_base_qs:
                logger.debug("Skipping follow-up generation for Full Stack Developer because no unasked base questions remain to splice.")
                return None

        follow_up_text, llm_error, latency_ms, llm_model = await generate_follow_up_question(
            track=track,
            difficulty=difficulty,
            base_question=question.text,
            answer_excerpt=answer_chunk,
            topic=question.topic,
        )
        if not follow_up_text:
            logger.debug(
                "Follow-up generation returned empty text (error=%s) for question %s", llm_error, question.id
            )
            return None

        follow_up_question = await self._interview_question_repo.create_follow_up_question(
            interview_id=attempt.interview_id,
            parent_question_id=question.id,
            text=follow_up_text,
            topic=question.topic,
            category=question.category,
            strategy=question.follow_up_strategy,
        )
        follow_up_attempt = await self._question_attempt_repo.create_attempt(
            interview_id=attempt.interview_id,
            question_id=follow_up_question.id,
            question_text=follow_up_question.text,
        )

        if track == "Full Stack Developer":
            unasked_qs = await self._interview_question_repo.get_questions_without_attempts(interview_id=attempt.interview_id)
            unasked_base_qs = [q for q in unasked_qs if not getattr(q, "is_follow_up", False) and q.id != follow_up_question.id]
            if unasked_base_qs:
                last_q = unasked_base_qs[-1]
                logger.info("Splicing queue for strict count: deleting unasked base question %s", last_q.id)
                await self._async_session.delete(last_q)
                await self._async_session.commit()

        metadata = {
            "parent_question_id": question.id,
            "follow_up_question_id": follow_up_question.id,
            "follow_up_question_attempt_id": follow_up_attempt.id,
            "follow_up_question_text": follow_up_question.text,
            "llm_model": llm_model,
            "llm_latency_ms": latency_ms,
            "llm_error": llm_error,
            "strategy": question.follow_up_strategy,
        }
        await self._question_attempt_repo.update_analysis_json(
            question_attempt_id=follow_up_attempt.id,
            analysis_json={"follow_up": metadata},
        )
        logger.info(
            "Generated follow-up question %s for parent %s (strategy=%s)",
            follow_up_question.id,
            question.id,
            question.follow_up_strategy,
        )
        return metadata

    def _extract_answer_chunk(
        self,
        transcription: dict | None,
        *,
        max_seconds: float = 90.0,
        max_chars: int = 4000,
    ) -> str:
        """Use the trailing portion of the transcription for LLM prompting."""
        if not transcription:
            return ""

        words = transcription.get("words")
        if isinstance(words, list) and words:
            chunk_words: list[str] = []
            last_end = float(words[-1].get("end") or 0.0)
            min_start = last_end - max_seconds
            for word in reversed(words):
                token = str(word.get("word") or "").strip()
                if not token:
                    continue
                start_time = float(word.get("start") or 0.0)
                if start_time < min_start and chunk_words:
                    break
                chunk_words.append(token)
            chunk = " ".join(reversed(chunk_words))
        else:
            chunk = str(transcription.get("text") or "")

        chunk = chunk.strip()
        if not chunk:
            return ""
        if len(chunk) > max_chars:
            return chunk[-max_chars:]
        return chunk


__all__ = ["FollowUpService"]
