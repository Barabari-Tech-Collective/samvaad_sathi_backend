from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.routes import audio, interviews
from src.models.schemas.audio import TextAnswerRequest
from src.models.schemas.interview import CreateAttemptRequest
from src.repository.crud.question import AttemptIdempotencyConflict


USER = SimpleNamespace(id=17)


@pytest.mark.asyncio
async def test_start_attempt_returns_existing_idempotent_attempt():
    interview = SimpleNamespace(id=9, user_id=USER.id, status="active")
    question = SimpleNamespace(id=4, interview_id=9, text="Tell me about yourself")

    class InterviewRepo:
        async def get_by_id(self, *, interview_id):
            return interview

    class QuestionRepo:
        async def get_by_id(self, *, question_id):
            return question

        async def update_status(self, **_kwargs):
            return None

    class AttemptRepo:
        async def create_or_get_attempt(self, **kwargs):
            assert kwargs["client_attempt_id"] == "practice-9-question-4"
            return SimpleNamespace(id=81), False

    result = await interviews.create_question_attempt(
        CreateAttemptRequest(
            interviewId=9,
            questionId=4,
            clientAttemptId="practice-9-question-4",
        ),
        current_user=USER,
        interview_repo=InterviewRepo(),
        question_repo=QuestionRepo(),
        attempt_repo=AttemptRepo(),
    )

    assert result.question_attempt_id == 81
    assert result.created is False


@pytest.mark.asyncio
async def test_start_attempt_rejects_client_key_reused_for_another_question():
    interview = SimpleNamespace(id=9, user_id=USER.id, status="active")
    question = SimpleNamespace(id=5, interview_id=9, text="A different question")

    class InterviewRepo:
        async def get_by_id(self, **_kwargs):
            return interview

    class QuestionRepo:
        async def get_by_id(self, **_kwargs):
            return question

        async def update_status(self, **_kwargs):
            return None

    class AttemptRepo:
        async def create_or_get_attempt(self, **_kwargs):
            raise AttemptIdempotencyConflict("Attempt key belongs to another question.")

    with pytest.raises(Exception) as exc:
        await interviews.create_question_attempt(
            CreateAttemptRequest(
                interviewId=9,
                questionId=5,
                clientAttemptId="practice-9-question-4",
            ),
            current_user=USER,
            interview_repo=InterviewRepo(),
            question_repo=QuestionRepo(),
            attempt_repo=AttemptRepo(),
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_audio_is_acknowledged_when_transcription_fails(monkeypatch):
    attempt = SimpleNamespace(
        id=42,
        interview_id=9,
        audio_url=None,
        answer_checksum=None,
        answer_kind=None,
        answer_version=0,
        transcription_status="pending",
        transcription=None,
    )

    class Repo:
        async_session = SimpleNamespace()

        async def get_by_id_and_user(self, **_kwargs):
            return attempt

        async def save_audio_asset(self, **kwargs):
            attempt.audio_url = kwargs["audio_url"]
            attempt.answer_checksum = kwargs["checksum"]
            attempt.answer_kind = "audio"
            attempt.answer_version = 1
            return attempt

        async def mark_transcription_failed(self, **_kwargs):
            attempt.transcription_status = "failed"
            return attempt

    async def validate(_file):
        return b"valid wav bytes", {
            "filename": "answer.wav",
            "content_type": "audio/wav",
            "size": 15,
        }

    async def store(**_kwargs):
        return "private/answer.wav"

    async def transcribe(**_kwargs):
        return None, "provider unavailable", 25, "whisper"

    monkeypatch.setattr(audio, "validate_audio_file", validate)
    monkeypatch.setattr(audio, "upload_answer_audio_to_s3", store)
    monkeypatch.setattr(audio, "transcribe_audio_with_whisper", transcribe)
    monkeypatch.setattr(audio, "get_audio_duration_estimate", lambda *_args: 1.2)

    result = await audio.transcribe_audio_answer(
        question_attempt_id="42",
        language="en",
        file=SimpleNamespace(filename="answer.wav"),
        current_user=USER,
        question_repo=Repo(),
        interview_repo=SimpleNamespace(
            get_by_id=AsyncMock(
                return_value=SimpleNamespace(status="active")
            )
        ),
    )

    assert result.saved is True
    assert result.transcription_status == "failed"
    assert result.answer_version == 1
    assert "Answer saved" in result.message


@pytest.mark.asyncio
async def test_typed_answer_is_saved_as_transcribed_evidence():
    attempt = SimpleNamespace(id=42)
    saved = SimpleNamespace(id=42, answer_version=3)

    class Repo:
        async def get_by_id_and_user(self, **_kwargs):
            return attempt

        async def save_text_answer(self, **kwargs):
            assert kwargs["text"] == "I solved the issue"
            assert kwargs["language"] == "en"
            return saved

    result = await audio.save_text_answer(
        TextAnswerRequest(
            questionAttemptId=42,
            text="  I solved   the issue  ",
            language="en",
        ),
        current_user=USER,
        question_repo=Repo(),
    )

    assert result.saved is True
    assert result.answer_version == 3
    assert result.transcription_status == "transcribed"
