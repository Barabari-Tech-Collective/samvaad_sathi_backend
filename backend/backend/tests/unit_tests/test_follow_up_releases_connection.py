"""The follow-up model call must not hold a database connection.

The worker has 5 connections for 10 concurrent jobs. A follow-up call takes
5-10 seconds; holding the connection through it left other jobs waiting for
the pool when a whole class answered at once. The transaction is now ended
before the call, and publishing re-reads and locks what it writes.
"""

from unittest.mock import AsyncMock

import pytest

from src.services.follow_up import FollowUpService


class _Question:
    id = 1
    is_follow_up = False
    follow_up_strategy = "llm_transcription_based"
    text = "Tell me about a project you led."
    topic = "Leadership"
    category = "behavioural"


class _Attempt:
    id = None
    question_id = 1
    interview_id = 1
    follow_up_disposition = None
    transcription = {"text": "I led a team of four to rebuild our billing system last year"}


class _Interview:
    track = "general"
    difficulty = "medium"


class _Session:
    def __init__(self, events: list[str]):
        self.events = events

    def in_transaction(self) -> bool:
        return True

    async def commit(self) -> None:
        self.events.append("commit")


@pytest.mark.asyncio
async def test_transaction_ends_before_the_model_call(monkeypatch):
    events: list[str] = []

    async def model(**_kwargs):
        events.append("model")
        return "", "stop here", 1, "test-model"

    monkeypatch.setattr("src.services.follow_up.generate_follow_up_question", model)
    monkeypatch.setattr(
        "src.services.interview_question_plan.planned_count", lambda _i: None
    )
    service = FollowUpService(async_session=_Session(events))  # type: ignore[arg-type]
    service._interview_question_repo.get_by_id = AsyncMock(return_value=_Question())  # type: ignore[method-assign]
    service._interview_question_repo.get_follow_up_for_parent = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service._interview_repo.get_by_id = AsyncMock(return_value=_Interview())  # type: ignore[method-assign]
    service._resolve_follow_up = AsyncMock()  # type: ignore[method-assign]

    await service._maybe_generate_follow_up(attempt=_Attempt())

    assert events[:2] == ["commit", "model"]
