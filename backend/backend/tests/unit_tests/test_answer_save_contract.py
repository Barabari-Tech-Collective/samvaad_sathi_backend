"""An acknowledgement describes durable answer storage, independently of AI."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import fastapi
import pytest

from src.api.routes import audio


@pytest.fixture
def dependencies(monkeypatch):
    attempt = SimpleNamespace(
        id=42, interview_id=99, audio_url=None, answer_checksum=None, answer_kind=None,
        answer_version=0, transcription_status="pending", transcription=None,
    )
    repo = SimpleNamespace(
        get_by_id_and_user=AsyncMock(return_value=attempt),
        save_audio_asset=AsyncMock(), update_audio_transcription=AsyncMock(),
        mark_transcription_failed=AsyncMock(),
        async_session=SimpleNamespace(rollback=AsyncMock()),
    )

    async def save_asset(**kwargs):
        attempt.audio_url = kwargs["audio_url"]
        attempt.answer_checksum = kwargs["checksum"]
        attempt.answer_kind = "audio"
        attempt.answer_version = 1
        return attempt

    async def save_transcription(**kwargs):
        attempt.transcription = kwargs["transcription"]
        attempt.transcription_status = "transcribed"
        return attempt

    async def mark_failed(**_kwargs):
        attempt.transcription_status = "failed"
        return attempt

    repo.save_audio_asset.side_effect = save_asset
    repo.update_audio_transcription.side_effect = save_transcription
    repo.mark_transcription_failed.side_effect = mark_failed
    monkeypatch.setattr(audio, "validate_audio_file", AsyncMock(return_value=(b"audio", {
        "size": 5, "content_type": "audio/wav", "filename": "recording.wav",
    })))
    monkeypatch.setattr(audio, "get_audio_duration_estimate", lambda *args: 10.0)
    monkeypatch.setattr(audio, "validate_transcription_language", lambda value: value)
    monkeypatch.setattr(audio, "transcribe_audio_with_whisper", AsyncMock(return_value=(
        {"text": "This is my answer", "words": []}, None, 10, "test-model",
    )))
    monkeypatch.setattr(audio, "upload_answer_audio_to_s3", AsyncMock(return_value="private/answer.wav"))
    followup = AsyncMock(return_value=None)
    monkeypatch.setattr(
        audio, "FollowUpService",
        MagicMock(return_value=SimpleNamespace(handle_transcription_saved=followup)),
    )
    return repo, attempt, followup


async def upload(repo):
    interview_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=SimpleNamespace(status="active"))
    )
    return await audio.transcribe_audio_answer(
        question_attempt_id="42", language="en",
        file=SimpleNamespace(filename="recording.wav"),
        current_user=SimpleNamespace(id=7), question_repo=repo,
        interview_repo=interview_repo,
    )


@pytest.mark.asyncio
async def test_committed_answer_has_positive_acknowledgement(dependencies):
    repo, _attempt, followup = dependencies
    result = await upload(repo)
    assert result.saved is True
    assert result.transcription_status == "transcribed"
    assert result.model_dump(by_alias=True)["questionAttemptId"] == 42
    repo.save_audio_asset.assert_awaited_once()
    repo.update_audio_transcription.assert_awaited_once()
    followup.assert_awaited_once()


@pytest.mark.asyncio
async def test_transcription_failure_keeps_the_durable_answer(dependencies, monkeypatch):
    repo, _attempt, followup = dependencies
    monkeypatch.setattr(
        audio, "transcribe_audio_with_whisper",
        AsyncMock(return_value=(None, "private provider error", 10, "test-model")),
    )
    result = await upload(repo)
    assert result.saved is True
    assert result.transcription_status == "failed"
    assert "private provider error" not in result.message
    repo.mark_transcription_failed.assert_awaited_once()
    followup.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["storage", "commit", "missing-row"])
async def test_non_durable_answers_never_return_success(dependencies, monkeypatch, failure):
    repo, _attempt, followup = dependencies
    if failure == "storage":
        monkeypatch.setattr(audio, "upload_answer_audio_to_s3", AsyncMock(return_value=None))
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
    followup.assert_not_called()


@pytest.mark.asyncio
async def test_analysis_commit_failure_does_not_discard_saved_audio(dependencies):
    repo, attempt, followup = dependencies
    repo.update_audio_transcription.side_effect = RuntimeError("database unavailable")
    result = await upload(repo)
    assert result.saved is True
    assert attempt.audio_url == "private/answer.wav"
    assert result.transcription_status == "failed"
    followup.assert_not_called()


@pytest.mark.asyncio
async def test_followup_failure_does_not_discard_saved_answer(dependencies):
    repo, _attempt, followup = dependencies
    followup.side_effect = RuntimeError("provider unavailable")
    assert (await upload(repo)).saved is True


@pytest.mark.asyncio
async def test_foreign_attempt_does_not_reach_storage_or_transcription(dependencies):
    repo, _attempt, followup = dependencies
    repo.get_by_id_and_user.return_value = None
    with pytest.raises(fastapi.HTTPException) as exc:
        await upload(repo)
    assert exc.value.status_code == 404
    audio.upload_answer_audio_to_s3.assert_not_called()
    audio.transcribe_audio_with_whisper.assert_not_called()
    followup.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["terminated", "completed"])
async def test_an_ended_interviews_attempt_cannot_be_transcribed(
    dependencies, status
):
    """A late answer must not be accepted after its interview has ended."""
    repo, _attempt, followup = dependencies
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
    audio.transcribe_audio_with_whisper.assert_not_called()
    followup.assert_not_called()
