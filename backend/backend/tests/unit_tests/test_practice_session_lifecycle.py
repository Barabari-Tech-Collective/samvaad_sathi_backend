import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fastapi
import pytest

from src.api.routes import audio, interviews
from src.models.schemas.interview import (
    CreateAttemptRequest,
    PauseInterviewRequest,
    ResumeInterviewRequest,
)
from src.services.interview_lifecycle import interview_has_expired

UTC = datetime.timezone.utc
NOW = datetime.datetime.now(UTC)


def interview(**overrides):
    values = {
        "id": 7,
        "user_id": 42,
        "track": "general",
        "difficulty": "medium",
        "status": "active",
        "interview_mode": "practice",
        "paused_at": None,
        "expires_at": NOW + datetime.timedelta(days=1),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_practice_interview_can_be_paused_idempotently(monkeypatch):
    paused = interview(
        status="paused",
        paused_at=NOW,
        expires_at=NOW + datetime.timedelta(days=7),
    )
    repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=interview()),
        mark_paused=AsyncMock(return_value=paused),
        mark_expired=AsyncMock(),
        async_session=SimpleNamespace(),
    )
    analytics = AsyncMock()
    monkeypatch.setattr(interviews, "track_analytics_event", analytics)

    result = await interviews.pause_interview(
        PauseInterviewRequest(interviewId=7),
        current_user=SimpleNamespace(id=42),
        interview_repo=repo,
    )

    assert result.status == "paused"
    assert result.expires_at == paused.expires_at
    repo.mark_paused.assert_awaited_once_with(interview_id=7)
    analytics.assert_awaited_once()

    repo.get_by_id.return_value = paused
    second = await interviews.pause_interview(
        PauseInterviewRequest(interviewId=7),
        current_user=SimpleNamespace(id=42),
        interview_repo=repo,
    )
    assert second.status == "paused"
    assert repo.mark_paused.await_count == 1
    assert analytics.await_count == 1


@pytest.mark.asyncio
async def test_assessment_interview_cannot_use_save_and_exit():
    repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=interview(interview_mode="assessment")),
        mark_paused=AsyncMock(),
    )

    with pytest.raises(fastapi.HTTPException) as exc:
        await interviews.pause_interview(
            PauseInterviewRequest(interviewId=7),
            current_user=SimpleNamespace(id=42),
            interview_repo=repo,
        )

    assert exc.value.status_code == 409
    repo.mark_paused.assert_not_called()


@pytest.mark.asyncio
async def test_expired_session_is_closed_instead_of_resumed():
    expired = interview(
        expires_at=datetime.datetime.now(UTC) - datetime.timedelta(seconds=1)
    )
    repo = SimpleNamespace(
        get_by_id_and_user=AsyncMock(return_value=expired),
        mark_expired=AsyncMock(return_value=interview(status="expired")),
    )

    with pytest.raises(fastapi.HTTPException) as exc:
        await interviews.resume_interview(
            ResumeInterviewRequest(interviewId=7),
            current_user=SimpleNamespace(id=42),
            interview_repo=repo,
            question_repo=SimpleNamespace(),
            question_attempt_repo=SimpleNamespace(),
        )

    assert exc.value.status_code == 410
    repo.mark_expired.assert_awaited_once_with(interview_id=7)


@pytest.mark.asyncio
async def test_expired_session_rejects_a_new_question_attempt():
    expired = interview(
        expires_at=datetime.datetime.now(UTC) - datetime.timedelta(seconds=1)
    )
    repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=expired),
        mark_expired=AsyncMock(return_value=interview(status="expired")),
    )

    with pytest.raises(fastapi.HTTPException) as exc:
        await interviews.create_question_attempt(
            CreateAttemptRequest(interviewId=7, questionId=11),
            current_user=SimpleNamespace(id=42),
            interview_repo=repo,
            question_repo=SimpleNamespace(),
            attempt_repo=SimpleNamespace(),
        )

    assert exc.value.status_code == 410
    repo.mark_expired.assert_awaited_once_with(interview_id=7)


@pytest.mark.asyncio
async def test_paused_session_is_reactivated_before_checkpoint_is_returned(monkeypatch):
    paused = interview(status="paused", paused_at=NOW)
    active = interview(expires_at=NOW + datetime.timedelta(days=7))
    repo = SimpleNamespace(
        get_by_id_and_user=AsyncMock(return_value=paused),
        mark_resumed=AsyncMock(return_value=active),
        mark_expired=AsyncMock(),
        async_session=SimpleNamespace(),
    )
    question_repo = SimpleNamespace(
        list_by_interview=AsyncMock(return_value=[]),
        async_session=SimpleNamespace(),
    )
    attempt_repo = SimpleNamespace(list_by_interview=AsyncMock(return_value=[]))
    analytics = AsyncMock()
    monkeypatch.setattr(interviews, "track_analytics_event", analytics)
    monkeypatch.setattr(interviews, "_backfill_follow_up_parents", AsyncMock())
    monkeypatch.setattr(interviews, "_get_supplement_map", AsyncMock(return_value={}))

    result = await interviews.resume_interview(
        ResumeInterviewRequest(interviewId=7),
        current_user=SimpleNamespace(id=42),
        interview_repo=repo,
        question_repo=question_repo,
        question_attempt_repo=attempt_repo,
    )

    assert result.status == "active"
    assert result.paused_at is None
    repo.mark_resumed.assert_awaited_once_with(interview_id=7)
    analytics.assert_awaited_once()


@pytest.mark.asyncio
async def test_paused_assessment_is_not_reactivated_by_practice_resume():
    paused = interview(
        status="paused",
        interview_mode="assessment",
        paused_at=NOW,
        expires_at=None,
    )
    repo = SimpleNamespace(
        get_by_id_and_user=AsyncMock(return_value=paused),
        mark_resumed=AsyncMock(),
        mark_expired=AsyncMock(),
    )

    with pytest.raises(fastapi.HTTPException) as exc:
        await interviews.resume_interview(
            ResumeInterviewRequest(interviewId=7),
            current_user=SimpleNamespace(id=42),
            interview_repo=repo,
            question_repo=SimpleNamespace(),
            question_attempt_repo=SimpleNamespace(),
        )

    assert exc.value.status_code == 409
    repo.mark_resumed.assert_not_called()


@pytest.mark.asyncio
async def test_paused_interview_rejects_late_typed_or_audio_answers():
    attempt = SimpleNamespace(interview_id=7)
    repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=interview(status="paused", paused_at=NOW)),
        mark_expired=AsyncMock(),
    )

    with pytest.raises(fastapi.HTTPException) as exc:
        await audio._require_active_interview(attempt, repo)

    assert exc.value.status_code == 409


def test_expiry_comparison_handles_timezone_aware_and_legacy_naive_values():
    assert interview_has_expired(
        SimpleNamespace(expires_at=NOW - datetime.timedelta(seconds=1)), now=NOW
    )
    assert interview_has_expired(
        SimpleNamespace(
            expires_at=(NOW - datetime.timedelta(seconds=1)).replace(tzinfo=None)
        ),
        now=NOW,
    )
    assert not interview_has_expired(SimpleNamespace(expires_at=None), now=NOW)
