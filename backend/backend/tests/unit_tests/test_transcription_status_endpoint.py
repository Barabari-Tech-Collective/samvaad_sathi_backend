"""The endpoint a client polls while its answer is transcribed in the background.

Transcription is moving out of the upload request, which pinned a pooled
database connection for the whole of a Whisper call and capped the platform at
roughly fifteen students answering at once. Once the response no longer carries
the outcome, this is where progress is read from.
"""

import datetime
import types
from unittest.mock import AsyncMock

import fastapi
import pytest

from src.api.routes import audio
from src.api.routes.audio import get_transcription_status
from src.models.db.question_attempt import TranscriptionStatus


def attempt(**overrides):
    """A question_attempt row with only the fields the endpoint reads."""
    defaults = dict(
        id=42,
        answer_version=3,
        audio_url="s3://bucket/answer.wav",
        answer_kind="audio",
        answer_checksum="a" * 64,
        transcription=None,
        transcription_status=TranscriptionStatus.PENDING.value,
        transcription_error=None,
        transcription_updated_at=None,
    )
    return types.SimpleNamespace(**{**defaults, **overrides})


class Repo:
    """Stands in for the ownership-scoped lookup."""

    def __init__(self, row):
        self.row = row
        self.calls = []

    async def get_by_id_and_user(self, *, question_attempt_id, user_id):
        self.calls.append((question_attempt_id, user_id))
        return self.row

    async def get_follow_up_payload_for_attempt(self, *, question_attempt_id):
        return None


USER = types.SimpleNamespace(id=7)


@pytest.mark.anyio
async def test_pending_answer_is_not_terminal_but_is_already_saved():
    response = await get_transcription_status(
        42, current_user=USER, question_repo=Repo(attempt())
    )
    assert response.status == "pending"
    assert response.is_terminal is False
    # The recording is stored before the job is queued, so a student must never
    # be told their answer was lost merely because it is still being processed.
    assert response.saved is True
    assert response.answer_version == 3
    assert response.transcription is None


@pytest.mark.anyio
async def test_processing_is_not_terminal():
    row = attempt(transcription_status=TranscriptionStatus.PROCESSING.value)
    response = await get_transcription_status(
        42, current_user=USER, question_repo=Repo(row)
    )
    assert response.status == "processing"
    assert response.is_terminal is False


@pytest.mark.anyio
async def test_transcribed_answer_is_terminal_and_carries_the_transcript():
    moment = datetime.datetime(2026, 10, 12, 9, 30, tzinfo=datetime.timezone.utc)
    row = attempt(
        transcription_status=TranscriptionStatus.TRANSCRIBED.value,
        transcription={"text": "I built a payments service", "words": [{"word": "I"}]},
        transcription_updated_at=moment,
    )
    response = await get_transcription_status(
        42, current_user=USER, question_repo=Repo(row)
    )
    assert response.status == "transcribed"
    assert response.is_terminal is True
    assert response.transcription["text"] == "I built a payments service"
    assert response.updated_at == moment.isoformat()


@pytest.mark.anyio
async def test_transcribed_answer_carries_a_reconnect_safe_follow_up():
    repo = Repo(
        attempt(
            transcription_status=TranscriptionStatus.TRANSCRIBED.value,
            transcription={"text": "I resolved a production incident"},
        )
    )

    async def follow_up(*, question_attempt_id):
        assert question_attempt_id == 42
        return {
            "interview_question_id": 70,
            "question_attempt_id": 71,
            "parent_question_id": 10,
            "text": "How did you communicate during the incident?",
            "strategy": "llm_transcription_based",
        }

    repo.get_follow_up_payload_for_attempt = follow_up
    response = await get_transcription_status(42, current_user=USER, question_repo=repo)
    assert response.follow_up_question.question_attempt_id == 71
    assert "communicate" in response.follow_up_question.text


@pytest.mark.anyio
async def test_failed_answer_is_terminal_and_explains_why():
    row = attempt(
        transcription_status=TranscriptionStatus.FAILED.value,
        transcription_error="We could not hear any speech in this recording.",
    )
    response = await get_transcription_status(
        42, current_user=USER, question_repo=Repo(row)
    )
    assert response.status == "failed"
    assert response.is_terminal is True
    # Polling must stop, and the student needs a reason rather than a silent stall.
    assert "could not hear" in response.error


@pytest.mark.anyio
async def test_unusable_speech_requires_a_new_recording_instead_of_provider_retry():
    row = attempt(
        transcription_status=TranscriptionStatus.FAILED.value,
        transcription={
            "text": "um",
            "evidence": {
                "status": "unavailable",
                "reason": "insufficient_speech",
                "word_count": 1,
                "confidence": None,
            },
        },
        transcription_error="We could not hear enough clear speech.",
    )
    repo = Repo(row)
    response = await get_transcription_status(42, current_user=USER, question_repo=repo)

    assert response.evidence_status == "unavailable"
    assert response.evidence_reason == "insufficient_speech"
    assert response.retryable is False

    with pytest.raises(fastapi.HTTPException) as raised:
        await audio.retry_transcription(42, current_user=USER, question_repo=repo)
    assert raised.value.status_code == fastapi.status.HTTP_409_CONFLICT


@pytest.mark.anyio
async def test_an_attempt_without_audio_is_not_reported_as_saved():
    row = attempt(audio_url=None)
    response = await get_transcription_status(
        42, current_user=USER, question_repo=Repo(row)
    )
    assert response.saved is False


@pytest.mark.anyio
async def test_another_students_attempt_is_reported_as_missing():
    """404 rather than 403, so the endpoint cannot be used to enumerate ids."""
    repo = Repo(None)
    with pytest.raises(fastapi.HTTPException) as raised:
        await get_transcription_status(9999, current_user=USER, question_repo=repo)
    assert raised.value.status_code == fastapi.status.HTTP_404_NOT_FOUND
    # The lookup must be scoped by the caller, not by attempt id alone.
    assert repo.calls == [(9999, 7)]


@pytest.mark.anyio
async def test_a_row_predating_the_column_is_treated_as_pending():
    """Backfill covers production, but a NULL must not crash the endpoint."""
    row = attempt(transcription_status=None)
    response = await get_transcription_status(
        42, current_user=USER, question_repo=Repo(row)
    )
    assert response.status == "pending"
    assert response.is_terminal is False


@pytest.mark.anyio
async def test_retry_requeues_a_failed_saved_answer(monkeypatch):
    row = attempt(transcription_status=TranscriptionStatus.FAILED.value)
    repo = Repo(row)
    repo.reserve_transcription_retry = AsyncMock(return_value=row)

    async def reserve(**kwargs):
        row.transcription_status = "pending"
        row.transcription_updated_at = datetime.datetime.now(datetime.timezone.utc)
        return row

    repo.reserve_transcription_retry.side_effect = reserve
    queued = AsyncMock(return_value="job-42")
    monkeypatch.setattr(audio, "enqueue_job", queued)

    response = await audio.retry_transcription(
        42, current_user=USER, question_repo=repo
    )

    assert response.status == "pending"
    assert response.saved is True
    queued.assert_awaited_once_with(
        "process_answer_transcription_task",
        question_attempt_id=42,
        checksum="a" * 64,
        language="en",
        filename="answer-42.wav",
    )


@pytest.mark.anyio
async def test_retry_does_not_duplicate_a_fresh_processing_job(monkeypatch):
    row = attempt(
        transcription_status=TranscriptionStatus.PROCESSING.value,
        transcription_updated_at=datetime.datetime.now(datetime.timezone.utc),
    )
    repo = Repo(row)
    repo.reserve_transcription_retry = AsyncMock(return_value=None)
    queued = AsyncMock(return_value="job-42")
    monkeypatch.setattr(audio, "enqueue_job", queued)

    response = await audio.retry_transcription(
        42, current_user=USER, question_repo=repo
    )

    assert response.status == "processing"
    repo.reserve_transcription_retry.assert_awaited_once()
    queued.assert_not_called()
