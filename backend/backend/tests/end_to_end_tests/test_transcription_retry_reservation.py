"""Retry state transitions are compare-and-set, not read-then-reset."""

import asyncio
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fastapi
import pytest
import sqlalchemy as sa
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401
from src.api.routes import audio
from src.models.db.interview import Interview
from src.models.db.question_attempt import QuestionAttempt
from src.repository.crud.question import QuestionAttemptCRUDRepository

UTC = datetime.timezone.utc
CHECKSUM = "a" * 64


async def seed(factory, actor_id, status="failed", age=20, version=1):
    async with factory() as session:
        parent = Interview(
            user_id=actor_id,
            track="Communication",
            status="completed",
            interview_mode="assessment",
        )
        session.add(parent)
        await session.flush()
        row = QuestionAttempt(
            interview_id=parent.id,
            question_text="private prompt",
            answer_kind="audio",
            audio_url="private/recording.wav",
            answer_checksum=CHECKSUM,
            answer_version=version,
            transcription_status=status,
            transcription_updated_at=datetime.datetime.now(UTC)
            - datetime.timedelta(minutes=age),
        )
        session.add(row)
        await session.commit()
        return row.id


@pytest.mark.asyncio
async def test_simultaneous_retries_queue_one_job_for_a_failed_saved_answer(
    db, monkeypatch
):
    _, factory, user_id = db
    attempt_id = await seed(factory, user_id)
    queue = AsyncMock(return_value="queued")
    monkeypatch.setattr(audio, "enqueue_job", queue)

    async def retry():
        async with factory() as session:
            return await audio.retry_transcription(
                attempt_id,
                SimpleNamespace(id=user_id),
                QuestionAttemptCRUDRepository(async_session=session),
            )

    results = await asyncio.gather(*(retry() for _ in range(8)))
    assert all(
        row.saved and row.status == "pending" and row.answer_version == 1
        for row in results
    )
    assert queue.await_count == 1
    async with factory() as session:
        row = await session.get(QuestionAttempt, attempt_id)
        assert (
            row.audio_url == "private/recording.wav" and row.answer_checksum == CHECKSUM
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,age,expected",
    [
        ("pending", 0, False),
        ("processing", 0, False),
        ("pending", 20, True),
        ("processing", 20, True),
        ("transcribed", 20, False),
    ],
)
async def test_only_failed_or_stalled_observed_state_is_reserved(
    db, status, age, expected
):
    _, factory, user_id = db
    attempt_id = await seed(factory, user_id, status, age)
    async with factory() as session:
        repo = QuestionAttemptCRUDRepository(async_session=session)
        row = await repo.get_by_id(question_attempt_id=attempt_id)
        result = await repo.reserve_transcription_retry(
            question_attempt_id=attempt_id,
            user_id=user_id,
            checksum=CHECKSUM,
            answer_version=1,
            expected_status=row.transcription_status,
            expected_updated_at=row.transcription_updated_at,
        )
        assert (result is not None) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["worker", "new_audio", "version", "owner"])
async def test_stale_retry_snapshot_cannot_reset_new_work(db, change):
    _, factory, user_id = db
    attempt_id = await seed(factory, user_id)
    async with factory() as session:
        repo = QuestionAttemptCRUDRepository(async_session=session)
        row = await repo.get_by_id(question_attempt_id=attempt_id)
        timestamp = row.transcription_updated_at
        async with factory() as other:
            values = (
                {
                    "transcription_status": "processing",
                    "transcription_updated_at": datetime.datetime.now(UTC),
                }
                if change == "worker"
                else (
                    {"answer_checksum": "b" * 64}
                    if change == "new_audio"
                    else {"answer_version": 2} if change == "version" else {}
                )
            )
            if values:
                await other.execute(
                    sa.update(QuestionAttempt)
                    .where(QuestionAttempt.id == attempt_id)
                    .values(**values)
                )
            await other.commit()
        result = await repo.reserve_transcription_retry(
            question_attempt_id=attempt_id,
            user_id=user_id + 1 if change == "owner" else user_id,
            checksum=CHECKSUM,
            answer_version=1,
            expected_status="failed",
            expected_updated_at=timestamp,
        )
        assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_state", ["processing", "transcribed"])
async def test_enqueue_failure_cannot_overwrite_worker_progress(
    db, monkeypatch, worker_state
):
    _, factory, user_id = db
    attempt_id = await seed(factory, user_id)

    async def queue(*args, **kwargs):
        async with factory() as session:
            repo = QuestionAttemptCRUDRepository(async_session=session)
            assert await repo.mark_transcription_processing(
                question_attempt_id=attempt_id, checksum=CHECKSUM
            )
            if worker_state == "transcribed":
                await session.execute(
                    sa.update(QuestionAttempt)
                    .where(QuestionAttempt.id == attempt_id)
                    .values(
                        transcription_status="transcribed",
                        transcription={"text": "A complete answer"},
                    )
                )
                await session.commit()
        return None

    monkeypatch.setattr(audio, "enqueue_job", queue)
    async with factory() as session:
        result = await audio.retry_transcription(
            attempt_id,
            SimpleNamespace(id=user_id),
            QuestionAttemptCRUDRepository(async_session=session),
        )
        assert result.status == worker_state and result.saved


@pytest.mark.asyncio
async def test_queue_outage_keeps_audio_and_allows_a_later_retry(db, monkeypatch):
    _, factory, user_id = db
    attempt_id = await seed(factory, user_id)
    monkeypatch.setattr(audio, "enqueue_job", AsyncMock(return_value=None))
    async with factory() as session:
        repo = QuestionAttemptCRUDRepository(async_session=session)
        with pytest.raises(fastapi.HTTPException) as error:
            await audio.retry_transcription(
                attempt_id, SimpleNamespace(id=user_id), repo
            )
        assert (
            error.value.status_code == 503 and error.value.headers["Retry-After"] == "3"
        )
        row = await repo.get_by_id(question_attempt_id=attempt_id)
        assert (
            row.transcription_status == "failed"
            and row.audio_url == "private/recording.wav"
        )
        monkeypatch.setattr(audio, "enqueue_job", AsyncMock(return_value="retry"))
        result = await audio.retry_transcription(
            attempt_id, SimpleNamespace(id=user_id), repo
        )
        assert result.saved and result.status == "pending"


@pytest.mark.asyncio
async def test_sweeper_and_browser_retry_share_one_atomic_reservation(db, monkeypatch):
    from src.worker import tasks, queue as queue_module
    from src.repository.database import async_db

    _, factory, user_id = db
    attempt_id = await seed(factory, user_id, "pending", 20)
    queued = AsyncMock(return_value="queued")
    monkeypatch.setattr(audio, "enqueue_job", queued)
    monkeypatch.setattr(queue_module, "enqueue_job", queued)
    monkeypatch.setattr(async_db, "get_session", factory)

    async def retry():
        async with factory() as session:
            return await audio.retry_transcription(
                attempt_id,
                SimpleNamespace(id=user_id),
                QuestionAttemptCRUDRepository(async_session=session),
            )

    await asyncio.gather(tasks.sweep_stalled_answer_transcriptions_task({}), retry())
    assert queued.await_count == 1


@pytest.mark.asyncio
async def test_retry_is_owner_scoped_and_handles_legacy_malformed_metadata(
    db, monkeypatch
):
    _, factory, user_id = db
    attempt_id = await seed(factory, user_id)
    queued = AsyncMock(return_value="queued")
    monkeypatch.setattr(audio, "enqueue_job", queued)
    async with factory() as session:
        repo = QuestionAttemptCRUDRepository(async_session=session)
        with pytest.raises(fastapi.HTTPException) as error:
            await audio.retry_transcription(
                attempt_id, SimpleNamespace(id=user_id + 1), repo
            )
        assert error.value.status_code == 404
        queued.assert_not_awaited()
        row = await session.get(QuestionAttempt, attempt_id)
        row.transcription = ["legacy malformed metadata"]
        await session.commit()
        result = await audio.retry_transcription(
            attempt_id, SimpleNamespace(id=user_id), repo
        )
        assert (
            result.saved and result.status == "pending" and result.transcription is None
        )
        assert queued.await_args.kwargs["language"] == "en"
