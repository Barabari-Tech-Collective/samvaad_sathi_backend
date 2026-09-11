"""Regression guard: follow-up generation must not run the LLM on a
near-empty answer.

Before this fix, _maybe_generate_follow_up only skipped a fully empty
transcription (`if not answer_chunk`). A one-word reply like "sorting" or a
non-answer like "um" still produced a non-empty chunk, so the follow-up LLM
call went ahead trying to "clarify" an answer that said nothing worth
clarifying - QA's second report. Scoring already solved this exact problem
with _is_near_empty_answer (min 3 words, see test_non_answer_detection.py);
this reuses that same threshold rather than inventing a second one.
"""

from unittest.mock import AsyncMock

import pytest

from src.services.follow_up import FollowUpService


class _FakeQuestion:
    def __init__(self, id: int = 1, is_follow_up: bool = False, follow_up_strategy: str | None = "llm_transcription_based"):
        self.id = id
        self.is_follow_up = is_follow_up
        self.follow_up_strategy = follow_up_strategy
        self.text = "What is a closure?"
        self.topic = "JavaScript"
        self.category = "tech"


class _FakeAttempt:
    def __init__(self, transcription_text: str, question_id: int = 1, interview_id: int = 1):
        self.question_id = question_id
        self.interview_id = interview_id
        self.transcription = {"text": transcription_text}


def _make_service_with_question(question: _FakeQuestion) -> FollowUpService:
    """Wires just enough of the repo surface to reach the near-empty check,
    with the LLM call (generate_follow_up_question) left unmockedimportable
    but never expected to run."""
    service = FollowUpService(async_session=object())  # type: ignore[arg-type]
    service._interview_question_repo.get_by_id = AsyncMock(return_value=question)  # type: ignore[method-assign]
    service._interview_question_repo.get_follow_up_for_parent = AsyncMock(return_value=None)  # type: ignore[method-assign]
    return service


@pytest.mark.parametrize(
    "transcription_text",
    ["", "   ", "um", "sorting", "I don't"],
    ids=["empty", "whitespace-only", "filler-word", "one-word-answer", "two-words-below-floor"],
)
@pytest.mark.asyncio
async def test_near_empty_answers_skip_follow_up_generation_without_calling_the_llm(monkeypatch, transcription_text):
    llm_call = AsyncMock(side_effect=AssertionError("LLM must not be called for a near-empty answer"))
    monkeypatch.setattr("src.services.follow_up.generate_follow_up_question", llm_call)

    service = _make_service_with_question(_FakeQuestion())
    result = await service._maybe_generate_follow_up(attempt=_FakeAttempt(transcription_text))

    assert result is None
    llm_call.assert_not_called()


class _FakeFollowUpQuestion:
    id = 99
    text = "Can you walk through why?"


class _FakeFollowUpAttempt:
    id = 999


@pytest.mark.asyncio
async def test_a_three_word_refusal_still_reaches_the_llm(monkeypatch):
    """"I don't know" is 3 words - the same boundary case
    test_non_answer_detection.py already pins for scoring (deliberately left
    to the LLM, not deterministically zeroed). Follow-up generation should
    treat it the same way: substantial enough to proceed past this guard."""
    llm_call = AsyncMock(return_value=("Can you walk through why?", None, 120, "test-model"))
    monkeypatch.setattr("src.services.follow_up.generate_follow_up_question", llm_call)

    service = _make_service_with_question(_FakeQuestion())
    service._interview_repo.get_by_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service._interview_question_repo.create_follow_up_question = AsyncMock(  # type: ignore[method-assign]
        return_value=_FakeFollowUpQuestion()
    )
    service._question_attempt_repo.create_attempt = AsyncMock(return_value=_FakeFollowUpAttempt())  # type: ignore[method-assign]
    service._question_attempt_repo.update_analysis_json = AsyncMock(return_value=None)  # type: ignore[method-assign]

    result = await service._maybe_generate_follow_up(attempt=_FakeAttempt("I don't know"))

    llm_call.assert_called_once()
    assert result is not None
    assert result["follow_up_question_id"] == 99
