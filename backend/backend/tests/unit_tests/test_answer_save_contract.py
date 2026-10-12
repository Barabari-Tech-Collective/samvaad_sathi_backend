"""An upload acknowledgement must describe an actually committed answer."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import fastapi
import pytest

from src.api.routes import audio


@pytest.fixture
def dependencies(monkeypatch):
    repo = SimpleNamespace(
        get_by_id_and_user=AsyncMock(return_value=SimpleNamespace(id=42)),
        update_audio_transcription=AsyncMock(return_value=SimpleNamespace(id=42)),
        async_session=SimpleNamespace(rollback=AsyncMock()),
    )
    monkeypatch.setattr(audio, "validate_audio_file", AsyncMock(return_value=(b"audio", {
        "size": 5, "content_type": "audio/wav", "filename": "recording.wav",
    })))
    monkeypatch.setattr(audio, "get_audio_duration_estimate", lambda *args: 10.0)
    monkeypatch.setattr(audio, "validate_transcription_language", lambda value: value)
    monkeypatch.setattr(audio, "transcribe_audio_with_whisper", AsyncMock(return_value=(
        {"text": "This is my answer", "words": []}, None, 10, "test-model",
    )))
    monkeypatch.setattr(audio, "save_audio_file", AsyncMock(return_value=("/tmp/test-only.wav", "answer-reference")))
    monkeypatch.setattr(audio, "cleanup_temp_audio_file", AsyncMock())
    followup = AsyncMock(return_value=None)
    monkeypatch.setattr(audio, "FollowUpService", MagicMock(return_value=SimpleNamespace(handle_transcription_saved=followup)))
    return repo, followup


async def upload(repo):
    return await audio.transcribe_audio_answer(
        question_attempt_id="42", language="en",
        file=SimpleNamespace(filename="recording.wav"),
        current_user=SimpleNamespace(id=7), question_repo=repo,
    )


@pytest.mark.asyncio
async def test_committed_answer_has_positive_acknowledgement(dependencies):
    repo, followup = dependencies
    result = await upload(repo)
    assert result.saved is True
    assert result.model_dump(by_alias=True)["questionAttemptId"] == 42
    repo.update_audio_transcription.assert_awaited_once()
    followup.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transcription", "file", "database", "missing-row"])
async def test_unsaved_answers_never_return_success(dependencies, monkeypatch, failure):
    repo, followup = dependencies
    if failure == "transcription":
        monkeypatch.setattr(audio, "transcribe_audio_with_whisper", AsyncMock(return_value=(None, "private provider error", 10, "test-model")))
    elif failure == "file":
        monkeypatch.setattr(audio, "save_audio_file", AsyncMock(side_effect=RuntimeError("private path")))
    elif failure == "database":
        repo.update_audio_transcription.side_effect = RuntimeError("private database error")
    else:
        repo.update_audio_transcription.return_value = None
    with pytest.raises(fastapi.HTTPException) as exc:
        await upload(repo)
    assert exc.value.status_code == 503
    assert "private" not in exc.value.detail
    assert exc.value.headers["Retry-After"] == "3"
    repo.async_session.rollback.assert_awaited_once()
    followup.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_error_cannot_hide_an_already_committed_answer(dependencies, monkeypatch):
    repo, _ = dependencies
    monkeypatch.setattr(audio, "cleanup_temp_audio_file", AsyncMock(side_effect=OSError("cleanup")))
    assert (await upload(repo)).saved is True


@pytest.mark.asyncio
async def test_followup_failure_does_not_discard_saved_answer(dependencies):
    repo, followup = dependencies
    followup.side_effect = RuntimeError("provider unavailable")
    assert (await upload(repo)).saved is True


@pytest.mark.asyncio
async def test_foreign_attempt_does_not_reach_transcription(dependencies):
    repo, followup = dependencies
    repo.get_by_id_and_user.return_value = None
    with pytest.raises(fastapi.HTTPException) as exc:
        await upload(repo)
    assert exc.value.status_code == 404
    audio.transcribe_audio_with_whisper.assert_not_called()
    followup.assert_not_called()
