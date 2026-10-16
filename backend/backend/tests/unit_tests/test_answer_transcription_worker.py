"""Background speech processing never changes or loses a newer answer."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from arq.worker import Retry

from src.worker.tasks import process_answer_transcription_task


@pytest.fixture
def worker_dependencies(monkeypatch):
    checksum = "a" * 64
    attempt = SimpleNamespace(
        id=42,
        answer_checksum=checksum,
        answer_kind="audio",
        audio_url="answers/42.webm",
        transcription=None,
        transcription_status="pending",
    )
    repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=attempt),
        mark_transcription_processing=AsyncMock(return_value=attempt),
        mark_transcription_pending=AsyncMock(return_value=attempt),
        mark_transcription_failed=AsyncMock(return_value=attempt),
        update_audio_transcription=AsyncMock(return_value=attempt),
        mark_transcription_complete=AsyncMock(return_value=attempt),
    )
    sessions = []

    def get_session():
        session = SimpleNamespace(close=AsyncMock())
        sessions.append(session)
        return session

    import src.repository.crud.question as question_module
    import src.services.follow_up as follow_up_module
    import src.services.s3_service as s3_module
    import src.services.whisper as whisper_module
    import src.worker.database as worker_database

    monkeypatch.setattr(worker_database, "get_session", get_session)
    monkeypatch.setattr(
        question_module,
        "QuestionAttemptCRUDRepository",
        MagicMock(return_value=repo),
    )
    monkeypatch.setattr(
        s3_module,
        "fetch_answer_audio_from_s3",
        AsyncMock(return_value=b"saved audio"),
    )
    transcribe = AsyncMock(
        return_value=(
            {"text": "A durable answer", "language": "en"},
            None,
            25,
            "whisper",
        )
    )
    monkeypatch.setattr(whisper_module, "transcribe_audio_with_whisper", transcribe)
    follow_up = AsyncMock(return_value={"follow_up_question_id": 70})
    monkeypatch.setattr(
        follow_up_module,
        "FollowUpService",
        MagicMock(return_value=SimpleNamespace(handle_transcription_saved=follow_up)),
    )
    return SimpleNamespace(
        checksum=checksum,
        attempt=attempt,
        repo=repo,
        sessions=sessions,
        transcribe=transcribe,
        follow_up=follow_up,
    )


@pytest.mark.asyncio
async def test_worker_transcribes_saved_audio_and_publishes_after_follow_up(
    worker_dependencies,
):
    deps = worker_dependencies
    result = await process_answer_transcription_task(
        {"job_try": 1},
        question_attempt_id=42,
        checksum=deps.checksum,
        language="en",
        filename="answer.webm",
    )

    assert result == {
        "status": "transcribed",
        "question_attempt_id": 42,
        "follow_up_generated": True,
    }
    deps.repo.mark_transcription_processing.assert_awaited_once()
    deps.repo.update_audio_transcription.assert_awaited_once_with(
        question_attempt_id=42,
        audio_url="answers/42.webm",
        transcription={"text": "A durable answer", "language": "en"},
        checksum=deps.checksum,
        finalize=False,
    )
    deps.follow_up.assert_awaited_once_with(42)
    deps.repo.mark_transcription_complete.assert_awaited_once()
    assert all(session.close.await_count == 1 for session in deps.sessions)


@pytest.mark.asyncio
async def test_worker_retries_provider_failure_without_marking_answer_lost(
    worker_dependencies,
):
    deps = worker_dependencies
    deps.transcribe.return_value = (None, "provider unavailable", 25, "whisper")

    with pytest.raises(Retry):
        await process_answer_transcription_task(
            {"job_try": 1},
            question_attempt_id=42,
            checksum=deps.checksum,
            language="en",
            filename="answer.webm",
        )

    deps.repo.mark_transcription_pending.assert_awaited_once()
    deps.repo.mark_transcription_failed.assert_not_called()
    deps.repo.mark_transcription_complete.assert_not_called()


@pytest.mark.asyncio
async def test_worker_stops_after_three_failures_with_student_safe_error(
    worker_dependencies,
):
    deps = worker_dependencies
    deps.transcribe.return_value = (None, "secret provider error", 25, "whisper")

    result = await process_answer_transcription_task(
        {"job_try": 3},
        question_attempt_id=42,
        checksum=deps.checksum,
        language="en",
        filename="answer.webm",
    )

    assert result["status"] == "failed"
    error = deps.repo.mark_transcription_failed.await_args.kwargs["error"]
    assert "secret provider error" not in error
    assert "saved" in error.lower()


@pytest.mark.asyncio
async def test_worker_drops_a_job_for_a_superseded_recording(worker_dependencies):
    deps = worker_dependencies
    deps.attempt.answer_checksum = "b" * 64

    result = await process_answer_transcription_task(
        {"job_try": 1},
        question_attempt_id=42,
        checksum=deps.checksum,
        language="en",
        filename="answer.webm",
    )

    assert result["status"] == "superseded"
    deps.transcribe.assert_not_called()
    deps.repo.mark_transcription_processing.assert_not_called()
