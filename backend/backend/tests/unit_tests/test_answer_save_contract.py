"""An acknowledgement describes durable answer storage, independently of AI."""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fastapi
import pytest

from src.api.routes import audio


@pytest.fixture
def dependencies(monkeypatch):
    attempt = SimpleNamespace(
        id=42,
        interview_id=99,
        audio_url=None,
        answer_checksum=None,
        answer_kind=None,
        answer_version=0,
        transcription_status="pending",
        transcription=None,
        transcription_error=None,
        transcription_updated_at=None,
    )
    repo = SimpleNamespace(
        get_by_id_and_user=AsyncMock(return_value=attempt),
        save_audio_asset=AsyncMock(),
        mark_transcription_pending=AsyncMock(),
        mark_transcription_failed=AsyncMock(),
        get_follow_up_payload_for_attempt=AsyncMock(return_value=None),
        async_session=SimpleNamespace(rollback=AsyncMock()),
    )

    async def save_asset(**kwargs):
        attempt.audio_url = kwargs["audio_url"]
        attempt.answer_checksum = kwargs["checksum"]
        attempt.answer_kind = "audio"
        attempt.answer_version = 1
        return attempt

    async def mark_pending(**_kwargs):
        attempt.transcription_status = "pending"
        return attempt

    async def mark_failed(**_kwargs):
        attempt.transcription_status = "failed"
        return attempt

    repo.save_audio_asset.side_effect = save_asset
    repo.mark_transcription_pending.side_effect = mark_pending
    repo.mark_transcription_failed.side_effect = mark_failed
    monkeypatch.setattr(
        audio,
        "validate_audio_file",
        AsyncMock(
            return_value=(
                b"audio",
                {
                    "size": 5,
                    "content_type": "audio/wav",
                    "filename": "recording.wav",
                },
            )
        ),
    )
    monkeypatch.setattr(audio, "get_audio_duration_estimate", lambda *args: 10.0)
    monkeypatch.setattr(audio, "validate_transcription_language", lambda value: value)
    monkeypatch.setattr(
        audio, "upload_answer_audio_to_s3", AsyncMock(return_value="private/answer.wav")
    )
    queued = AsyncMock(return_value="job-42")
    monkeypatch.setattr(audio, "enqueue_job", queued)
    return repo, attempt, queued


async def upload(repo):
    interview_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=SimpleNamespace(status="active"))
    )
    return await audio.transcribe_audio_answer(
        question_attempt_id="42",
        language="en",
        file=SimpleNamespace(filename="recording.wav"),
        current_user=SimpleNamespace(id=7),
        question_repo=repo,
        interview_repo=interview_repo,
    )


@pytest.mark.asyncio
async def test_committed_answer_has_positive_acknowledgement(dependencies):
    repo, _attempt, queued = dependencies
    result = await upload(repo)
    assert result.saved is True
    assert result.transcription_status == "pending"
    assert result.model_dump(by_alias=True)["questionAttemptId"] == 42
    repo.save_audio_asset.assert_awaited_once()
    queued.assert_awaited_once_with(
        "process_answer_transcription_task",
        question_attempt_id=42,
        checksum=result.audio_url and _attempt.answer_checksum,
        language="en",
        filename="recording.wav",
    )


@pytest.mark.asyncio
async def test_queue_failure_keeps_the_durable_answer(dependencies):
    repo, _attempt, queued = dependencies
    queued.return_value = None
    result = await upload(repo)
    assert result.saved is True
    assert result.transcription_status == "failed"
    assert "saved" in result.message.lower()
    repo.mark_transcription_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_idempotent_retry_recovers_an_already_generated_follow_up(dependencies):
    repo, attempt, queued = dependencies
    attempt.audio_url = "private/answer.wav"
    attempt.answer_checksum = hashlib.sha256(b"audio").hexdigest()
    attempt.answer_kind = "audio"
    attempt.answer_version = 1
    attempt.transcription_status = "transcribed"
    attempt.transcription = {"text": "I resolved the incident"}
    repo.get_follow_up_payload_for_attempt.return_value = {
        "interview_question_id": 70,
        "question_attempt_id": 71,
        "parent_question_id": 10,
        "text": "How did you communicate during the incident?",
        "strategy": "llm_transcription_based",
    }

    result = await upload(repo)

    assert result.saved is True
    assert result.transcription_status == "transcribed"
    assert result.follow_up_generated is True
    assert result.follow_up_question.question_attempt_id == 71
    audio.upload_answer_audio_to_s3.assert_not_called()
    queued.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["storage", "commit", "missing-row"])
async def test_non_durable_answers_never_return_success(
    dependencies, monkeypatch, failure
):
    repo, _attempt, queued = dependencies
    if failure == "storage":
        monkeypatch.setattr(
            audio, "upload_answer_audio_to_s3", AsyncMock(return_value=None)
        )
    elif failure == "commit":
        repo.save_audio_asset.side_effect = RuntimeError("private database error")
    else:
        repo.save_audio_asset.side_effect = None
        repo.save_audio_asset.return_value = None
    with pytest.raises(fastapi.HTTPException) as exc:
        await upload(repo)
    assert exc.value.status_code == 503
    assert "private" not in exc.value.detail
    assert exc.value.headers["Retry-After"] == "3"
    queued.assert_not_called()


@pytest.mark.asyncio
async def test_foreign_attempt_does_not_reach_storage_or_transcription(dependencies):
    repo, _attempt, queued = dependencies
    repo.get_by_id_and_user.return_value = None
    with pytest.raises(fastapi.HTTPException) as exc:
        await upload(repo)
    assert exc.value.status_code == 404
    audio.upload_answer_audio_to_s3.assert_not_called()
    queued.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["terminated", "completed"])
async def test_an_ended_interviews_attempt_cannot_be_transcribed(dependencies, status):
    """A late answer must not be accepted after its interview has ended."""
    repo, _attempt, queued = dependencies
    interview_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=SimpleNamespace(status=status))
    )

    with pytest.raises(fastapi.HTTPException) as exc:
        await audio.transcribe_audio_answer(
            question_attempt_id="42",
            language="en",
            file=SimpleNamespace(filename="recording.wav"),
            current_user=SimpleNamespace(id=7),
            question_repo=repo,
            interview_repo=interview_repo,
        )

    assert exc.value.status_code == 409
    audio.upload_answer_audio_to_s3.assert_not_called()
    queued.assert_not_called()
