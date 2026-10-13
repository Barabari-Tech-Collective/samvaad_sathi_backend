"""The endpoint a client polls while its answer is transcribed in the background.

Transcription is moving out of the upload request, which pinned a pooled
database connection for the whole of a Whisper call and capped the platform at
roughly fifteen students answering at once. Once the response no longer carries
the outcome, this is where progress is read from.
"""

import datetime
import types

import fastapi
import pytest

from src.api.routes.audio import get_transcription_status
from src.models.db.question_attempt import TranscriptionStatus


def attempt(**overrides):
    """A question_attempt row with only the fields the endpoint reads."""
    defaults = dict(
        id=42,
        audio_url="s3://bucket/answer.wav",
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


USER = types.SimpleNamespace(id=7)


@pytest.mark.anyio
async def test_pending_answer_is_not_terminal_but_is_already_saved():
    response = await get_transcription_status(42, current_user=USER, question_repo=Repo(attempt()))
    assert response.status == "pending"
    assert response.is_terminal is False
    # The recording is stored before the job is queued, so a student must never
    # be told their answer was lost merely because it is still being processed.
    assert response.saved is True
    assert response.transcription is None


@pytest.mark.anyio
async def test_processing_is_not_terminal():
    row = attempt(transcription_status=TranscriptionStatus.PROCESSING.value)
    response = await get_transcription_status(42, current_user=USER, question_repo=Repo(row))
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
    response = await get_transcription_status(42, current_user=USER, question_repo=Repo(row))
    assert response.status == "transcribed"
    assert response.is_terminal is True
    assert response.transcription["text"] == "I built a payments service"
    assert response.updated_at == moment.isoformat()


@pytest.mark.anyio
async def test_failed_answer_is_terminal_and_explains_why():
    row = attempt(
        transcription_status=TranscriptionStatus.FAILED.value,
        transcription_error="We could not hear any speech in this recording.",
    )
    response = await get_transcription_status(42, current_user=USER, question_repo=Repo(row))
    assert response.status == "failed"
    assert response.is_terminal is True
    # Polling must stop, and the student needs a reason rather than a silent stall.
    assert "could not hear" in response.error


@pytest.mark.anyio
async def test_an_attempt_without_audio_is_not_reported_as_saved():
    row = attempt(audio_url=None)
    response = await get_transcription_status(42, current_user=USER, question_repo=Repo(row))
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
    response = await get_transcription_status(42, current_user=USER, question_repo=Repo(row))
    assert response.status == "pending"
    assert response.is_terminal is False
