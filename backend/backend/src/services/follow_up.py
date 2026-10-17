from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession as SQLAlchemyAsyncSession

from src.models.db.interview import Interview
from src.models.db.question_attempt import QuestionAttempt
from src.repository.crud.interview import InterviewCRUDRepository
from src.repository.crud.interview_question import InterviewQuestionCRUDRepository
from src.repository.crud.question import QuestionAttemptCRUDRepository
from src.services.llm import _is_near_empty_answer, generate_follow_up_question

logger = logging.getLogger(__name__)


class FollowUpService:
    """Generates follow-up questions for adaptive interview flows."""

    def __init__(self, async_session: SQLAlchemyAsyncSession):
        self._async_session = async_session
        self._question_attempt_repo = QuestionAttemptCRUDRepository(
            async_session=async_session
        )
        self._interview_question_repo = InterviewQuestionCRUDRepository(
            async_session=async_session
        )
        self._interview_repo = InterviewCRUDRepository(async_session=async_session)

    async def _resolve_follow_up(self, *, attempt, disposition: str) -> None:
        """Persist Gate 2 state only for a real stored attempt."""
        attempt_id = getattr(attempt, "id", None)
        resolver = getattr(self._question_attempt_repo, "resolve_follow_up", None)
        if attempt_id is None or not callable(resolver):
            return
        await resolver(
            question_attempt_id=attempt_id,
            disposition=disposition,
        )

    async def handle_transcription_saved(
        self, question_attempt_id: int
    ) -> dict[str, Any] | None:
        start_time = time.perf_counter()

        logger.info(
            "Follow-up workflow started | QuestionAttempt=%s",
            question_attempt_id,
        )
        """Trigger follow-up generation after a transcription is persisted."""
        attempt = await self._question_attempt_repo.get_by_id(
            question_attempt_id=question_attempt_id
        )
        logger.info(
            "Question attempt loaded | QuestionAttempt=%s | Found=%s | Interview=%s | Question=%s",
            question_attempt_id,
            attempt is not None,
            attempt.interview_id if attempt else None,
            attempt.question_id if attempt else None,
        )
        if not attempt or not attempt.question_id:
            return None
        logger.info(
            "Proceeding to follow-up generation | QuestionAttempt=%s",
            question_attempt_id,
        )
        return await self._maybe_generate_follow_up(attempt=attempt)

    async def _maybe_generate_follow_up(
        self, attempt: QuestionAttempt
    ) -> dict[str, Any] | None:
        generation_start = time.perf_counter()

        logger.info(
            "Evaluating follow-up generation | Interview=%s | Question=%s",
            attempt.interview_id,
            attempt.question_id,
        )
        if not attempt.question_id:
            return None
        if getattr(attempt, "follow_up_disposition", None) == "skipped":
            logger.info(
                "Follow-up skipped by student | QuestionAttempt=%s",
                attempt.id,
            )
            return None

        question = await self._interview_question_repo.get_by_id(question_id=attempt.question_id)  # type: ignore[arg-type]
        logger.info(
            "Interview question loaded | Question=%s | IsFollowUp=%s | Strategy=%s",
            question.id if question else None,
            question.is_follow_up if question else None,
            question.follow_up_strategy if question else None,
        )
        if not question or question.is_follow_up or not question.follow_up_strategy:
            return None

        existing = await self._interview_question_repo.get_follow_up_for_parent(
            parent_question_id=question.id
        )
        logger.info(
            "Existing follow-up check | ParentQuestion=%s | Exists=%s",
            question.id,
            existing is not None,
        )
        if existing:
            await self._resolve_follow_up(
                attempt=attempt,
                disposition="generated",
            )
            return None

        answer_chunk = self._extract_answer_chunk(transcription=attempt.transcription)
        logger.info(
            "Answer chunk extracted | Question=%s | Characters=%d",
            question.id,
            len(answer_chunk),
        )
        if not answer_chunk:
            logger.debug(
                "Skipping follow-up generation due to empty transcription chunk for question %s",
                question.id,
            )
            await self._resolve_follow_up(
                attempt=attempt,
                disposition="not_generated",
            )
            return None

        # Same threshold analyze_domain_with_llm/analyze_communication_with_llm
        # already use to short-circuit scoring on a near-empty answer (see
        # _is_near_empty_answer in llm.py). Without this, "I don't know" or a
        # one-word reply still had enough characters to pass the check above,
        # so the LLM was asked to write a follow-up clarifying an answer that
        # never said anything to clarify.
        if _is_near_empty_answer(answer_chunk):
            logger.debug(
                "Skipping follow-up generation for near-empty answer (question %s)",
                question.id,
            )
            await self._resolve_follow_up(
                attempt=attempt,
                disposition="not_generated",
            )
            return None

        interview = await self._interview_repo.get_by_id(
            interview_id=attempt.interview_id
        )

        track = interview.track if interview else "general"
        difficulty = interview.difficulty if interview else "medium"

        logger.info(
            "Interview loaded | Interview=%s | Track=%s | Difficulty=%s",
            attempt.interview_id,
            track,
            difficulty,
        )
        # Enforce strict question count for Full Stack Developer mode
        if track == "Full Stack Developer":
            unasked_qs = (
                await self._interview_question_repo.get_questions_without_attempts(
                    interview_id=attempt.interview_id
                )
            )
            unasked_base_qs = [
                q for q in unasked_qs if not getattr(q, "is_follow_up", False)
            ]
            if not unasked_base_qs:
                logger.debug(
                    "Skipping follow-up generation for Full Stack Developer because no unasked base questions remain to splice."
                )
                await self._resolve_follow_up(
                    attempt=attempt,
                    disposition="not_generated",
                )
                return None

        llm_start = time.perf_counter()

        logger.info(
            "Follow-up LLM request started | Interview=%s | Question=%s",
            attempt.interview_id,
            question.id,
        )
        follow_up_text, llm_error, latency_ms, llm_model = (
            await generate_follow_up_question(
                track=track,
                difficulty=difficulty,
                base_question=question.text,
                answer_excerpt=answer_chunk,
                topic=question.topic,
            )
        )
        logger.info(
            "Follow-up LLM completed | Question=%s | Duration=%.2fs | Model=%s | Error=%s | Generated=%s",
            question.id,
            time.perf_counter() - llm_start,
            llm_model,
            llm_error,
            bool(follow_up_text),
        )
        if not follow_up_text:
            logger.debug(
                "Follow-up generation returned empty text (error=%s) for question %s",
                llm_error,
                question.id,
            )
            await self._resolve_follow_up(
                attempt=attempt,
                disposition="not_generated",
            )
            return None
        # The model call happens outside the student's request. They may choose
        # "continue without follow-up" while it runs, so check the locked row
        # again immediately before publishing anything.
        attempt_id = getattr(attempt, "id", None)
        latest_attempt = (
            await self._question_attempt_repo.lock_by_id(question_attempt_id=attempt_id)
            if attempt_id is not None
            else attempt
        )
        if latest_attempt is None:
            await self._async_session.rollback()
            return None
        if getattr(latest_attempt, "follow_up_disposition", None) == "skipped":
            await self._async_session.rollback()
            logger.info(
                "Late follow-up discarded after student continued | QuestionAttempt=%s",
                attempt.id,
            )
            return None
        existing = await self._interview_question_repo.get_follow_up_for_parent(
            parent_question_id=question.id
        )
        if existing is not None:
            # Another at-least-once worker published while this worker was in
            # the model call. Reuse the persisted child; never append a second
            # prompt for the same answer.
            await self._question_attempt_repo.create_or_get_attempt(
                interview_id=attempt.interview_id,
                question_id=existing.id,
                question_text=existing.text,
                client_attempt_id=f"follow-up:{existing.id}",
            )
            await self._resolve_follow_up(
                attempt=attempt,
                disposition="generated",
            )
            return None
        db_start = time.perf_counter()
        follow_up_question = (
            await self._interview_question_repo.create_follow_up_question(
                interview_id=attempt.interview_id,
                parent_question_id=question.id,
                text=follow_up_text,
                topic=question.topic,
                category=question.category,
                strategy=question.follow_up_strategy,
            )
        )
        logger.info(
            "Follow-up question saved | Question=%s | NewQuestion=%s | Time=%.2fs",
            question.id,
            follow_up_question.id,
            time.perf_counter() - db_start,
        )
        if attempt_id is None:
            follow_up_attempt = await self._question_attempt_repo.create_attempt(
                interview_id=attempt.interview_id,
                question_id=follow_up_question.id,
                question_text=follow_up_question.text,
            )
        else:
            follow_up_attempt, _created = (
                await self._question_attempt_repo.create_or_get_attempt(
                    interview_id=attempt.interview_id,
                    question_id=follow_up_question.id,
                    question_text=follow_up_question.text,
                    client_attempt_id=f"follow-up:{follow_up_question.id}",
                )
            )
        logger.info(
            "Follow-up attempt created | Attempt=%s",
            follow_up_attempt.id,
        )

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
        analysis_start = time.perf_counter()
        await self._question_attempt_repo.update_analysis_json(
            question_attempt_id=follow_up_attempt.id,
            analysis_json={"follow_up": metadata},
        )
        await self._resolve_follow_up(
            attempt=attempt,
            disposition="generated",
        )
        logger.info(
            "Follow-up metadata persisted | Attempt=%s | Time=%.2fs",
            follow_up_attempt.id,
            time.perf_counter() - analysis_start,
        )
        logger.info(
            "Follow-up workflow completed | Interview=%s | ParentQuestion=%s | FollowUpQuestion=%s | TotalTime=%.2fs",
            attempt.interview_id,
            question.id,
            follow_up_question.id,
            time.perf_counter() - generation_start,
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
