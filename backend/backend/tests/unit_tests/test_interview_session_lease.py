import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fastapi
import pytest

from src.api.routes import interviews
from src.models.schemas.interview import (
    ClaimSessionLeaseRequest,
    CompleteInterviewRequest,
    CreateAttemptRequest,
    HeartbeatSessionLeaseRequest,
    PauseInterviewRequest,
)
from src.repository.crud.interview import InterviewCRUDRepository
from src.services.interview_session_lease import (
    SESSION_LEASE_TTL,
    session_lease_digest,
    session_lease_is_active,
    session_lease_matches,
)

UTC = datetime.timezone.utc
TOKEN_A = "a" * 43
TOKEN_B = "b" * 43


def practice_interview(**overrides):
    values = {
        "id": 7,
        "user_id": 42,
        "status": "active",
        "interview_mode": "practice",
        "expires_at": datetime.datetime.now(UTC) + datetime.timedelta(days=1),
        "session_lease_hash": None,
        "session_lease_required": False,
        "session_lease_acquired_at": None,
        "session_lease_expires_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def repository_for(interview):
    session = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
    repo = InterviewCRUDRepository(async_session=session)
    repo.lock_by_id_and_user = AsyncMock(return_value=interview)
    return repo, session


def test_tokens_are_compared_by_digest_and_expiry_is_timezone_safe():
    leased = practice_interview(
        session_lease_hash=session_lease_digest(TOKEN_A),
        session_lease_expires_at=datetime.datetime.now(UTC)
        + datetime.timedelta(seconds=30),
    )

    assert session_lease_matches(leased, TOKEN_A)
    assert not session_lease_matches(leased, TOKEN_B)
    assert session_lease_is_active(leased)

    leased.session_lease_expires_at = (
        datetime.datetime.now(UTC) - datetime.timedelta(seconds=1)
    ).replace(tzinfo=None)
    assert not session_lease_is_active(leased)


@pytest.mark.asyncio
async def test_first_claim_stores_only_a_hash_and_enables_enforcement():
    interview = practice_interview()
    repo, session = repository_for(interview)

    returned, state, token = await repo.claim_session_lease(
        interview_id=7, user_id=42, presented_token=None, force=False
    )

    assert returned is interview
    assert state == "acquired"
    assert token is not None
    assert interview.session_lease_hash == session_lease_digest(token)
    assert interview.session_lease_hash != token
    assert interview.session_lease_required is True
    assert interview.session_lease_expires_at > datetime.datetime.now(UTC)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(interview)


@pytest.mark.asyncio
async def test_matching_token_renews_without_rotating():
    original_expiry = datetime.datetime.now(UTC) + datetime.timedelta(seconds=10)
    interview = practice_interview(
        session_lease_hash=session_lease_digest(TOKEN_A),
        session_lease_required=True,
        session_lease_expires_at=original_expiry,
    )
    repo, _session = repository_for(interview)

    _returned, state, token = await repo.claim_session_lease(
        interview_id=7, user_id=42, presented_token=TOKEN_A, force=False
    )

    assert state == "renewed"
    assert token == TOKEN_A
    assert interview.session_lease_hash == session_lease_digest(TOKEN_A)
    assert interview.session_lease_expires_at > original_expiry


@pytest.mark.asyncio
async def test_active_lease_rejects_another_device_without_mutation():
    expiry = datetime.datetime.now(UTC) + datetime.timedelta(seconds=60)
    interview = practice_interview(
        session_lease_hash=session_lease_digest(TOKEN_A),
        session_lease_required=True,
        session_lease_expires_at=expiry,
    )
    repo, session = repository_for(interview)

    returned, state, token = await repo.claim_session_lease(
        interview_id=7, user_id=42, presented_token=TOKEN_B, force=False
    )

    assert returned is interview
    assert state == "conflict"
    assert token is None
    assert interview.session_lease_hash == session_lease_digest(TOKEN_A)
    assert interview.session_lease_expires_at == expiry
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_takeover_rotates_the_token_and_invalidates_old_device():
    interview = practice_interview(
        session_lease_hash=session_lease_digest(TOKEN_A),
        session_lease_required=True,
        session_lease_expires_at=datetime.datetime.now(UTC)
        + datetime.timedelta(seconds=60),
    )
    repo, _session = repository_for(interview)

    _returned, state, token = await repo.claim_session_lease(
        # A duplicated tab can inherit sessionStorage and therefore present
        # the same token. Force must still rotate it or the old tab survives.
        interview_id=7,
        user_id=42,
        presented_token=TOKEN_A,
        force=True,
    )

    assert state == "taken_over"
    assert token not in (None, TOKEN_A, TOKEN_B)
    assert session_lease_matches(interview, token)
    assert not session_lease_matches(interview, TOKEN_A)


@pytest.mark.asyncio
async def test_heartbeat_allows_same_device_to_recover_an_expired_lease():
    interview = practice_interview(
        session_lease_hash=session_lease_digest(TOKEN_A),
        session_lease_required=True,
        session_lease_expires_at=datetime.datetime.now(UTC)
        - datetime.timedelta(seconds=1),
    )
    repo, session = repository_for(interview)

    returned = await repo.heartbeat_session_lease(
        interview_id=7, user_id=42, lease_token=TOKEN_A
    )

    assert returned is interview
    assert interview.session_lease_expires_at > datetime.datetime.now(UTC)
    assert interview.session_lease_expires_at <= (
        datetime.datetime.now(UTC) + SESSION_LEASE_TTL
    )
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_endpoint_reports_conflict_without_leaking_a_token():
    repo = SimpleNamespace(
        claim_session_lease=AsyncMock(
            return_value=(practice_interview(), "conflict", None)
        )
    )

    with pytest.raises(fastapi.HTTPException) as exc:
        await interviews.claim_session_lease(
            ClaimSessionLeaseRequest(interviewId=7),
            current_user=SimpleNamespace(id=42),
            interview_repo=repo,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "SESSION_ACTIVE_ELSEWHERE"
    assert "token" not in exc.value.detail


@pytest.mark.asyncio
async def test_heartbeat_endpoint_rejects_a_replaced_token():
    repo = SimpleNamespace(heartbeat_session_lease=AsyncMock(return_value=None))

    with pytest.raises(fastapi.HTTPException) as exc:
        await interviews.heartbeat_session_lease(
            HeartbeatSessionLeaseRequest(interviewId=7, leaseToken=TOKEN_A),
            current_user=SimpleNamespace(id=42),
            interview_repo=repo,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "SESSION_LEASE_REPLACED"


def test_write_guard_is_progressive_and_does_not_change_assessments():
    # Existing Practice interviews created before the frontend rollout remain
    # writable until the new client claims them for the first time.
    interviews._require_session_lease(practice_interview(), None)
    interviews._require_session_lease(
        practice_interview(interview_mode="assessment", session_lease_required=True),
        None,
    )


@pytest.mark.parametrize(
    ("token", "expiry_delta", "expected_code"),
    [
        (TOKEN_B, 30, "SESSION_LEASE_REPLACED"),
        (TOKEN_A, -1, "SESSION_LEASE_EXPIRED"),
    ],
)
def test_write_guard_rejects_stale_or_expired_practice_sessions(
    token, expiry_delta, expected_code
):
    interview = practice_interview(
        session_lease_hash=session_lease_digest(TOKEN_A),
        session_lease_required=True,
        session_lease_expires_at=datetime.datetime.now(UTC)
        + datetime.timedelta(seconds=expiry_delta),
    )

    with pytest.raises(fastapi.HTTPException) as exc:
        interviews._require_session_lease(interview, token)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == expected_code


@pytest.mark.asyncio
async def test_completed_retry_is_idempotent_after_the_lease_was_cleared():
    completed = practice_interview(
        status="completed",
        session_lease_required=True,
        session_lease_hash=None,
        session_lease_expires_at=None,
    )
    repo = SimpleNamespace(lock_by_id_and_user=AsyncMock(return_value=completed))

    result = await interviews.complete_interview(
        CompleteInterviewRequest(interviewId=7, sessionLeaseToken=TOKEN_A),
        current_user=SimpleNamespace(id=42),
        interview_repo=repo,
    )

    assert result["status"] == "completed"
    assert result["message"] == "Interview already completed"


@pytest.mark.asyncio
async def test_pause_retry_is_idempotent_after_the_lease_was_cleared():
    paused_at = datetime.datetime.now(UTC)
    paused = practice_interview(
        status="paused",
        paused_at=paused_at,
        session_lease_required=True,
        session_lease_hash=None,
        session_lease_expires_at=None,
    )
    repo = SimpleNamespace(
        lock_by_id_and_user=AsyncMock(return_value=paused),
        mark_expired=AsyncMock(),
    )

    result = await interviews.pause_interview(
        PauseInterviewRequest(interviewId=7, sessionLeaseToken=TOKEN_A),
        current_user=SimpleNamespace(id=42),
        interview_repo=repo,
    )

    assert result.status == "paused"
    assert result.paused_at == paused_at


@pytest.mark.asyncio
async def test_attempt_rechecks_status_after_waiting_for_the_row_lock():
    initial = practice_interview()
    paused = practice_interview(status="paused", paused_at=datetime.datetime.now(UTC))
    repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=initial),
        lock_by_id_and_user=AsyncMock(return_value=paused),
        mark_expired=AsyncMock(),
    )
    question_repo = SimpleNamespace(
        get_by_id=AsyncMock(
            return_value=SimpleNamespace(id=11, interview_id=7, text="Question")
        ),
        update_status=AsyncMock(),
    )
    attempt_repo = SimpleNamespace(create_or_get_attempt=AsyncMock())

    with pytest.raises(fastapi.HTTPException) as exc:
        await interviews.create_question_attempt(
            CreateAttemptRequest(interviewId=7, questionId=11),
            current_user=SimpleNamespace(id=42),
            interview_repo=repo,
            question_repo=question_repo,
            attempt_repo=attempt_repo,
        )

    assert exc.value.status_code == 409
    attempt_repo.create_or_get_attempt.assert_not_awaited()
