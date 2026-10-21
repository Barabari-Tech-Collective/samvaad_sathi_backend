"""Real PostgreSQL ownership fencing with simulated storage/model boundaries."""

import asyncio
import datetime
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa

from .test_fixed_question_rounds import db, pytestmark  # noqa: F401
from .test_transcription_retry_reservation import CHECKSUM, UTC, seed
from src.models.db.question_attempt import QuestionAttempt
from src.models.db.interview import Interview
from src.models.db.interview_question import InterviewQuestion
from src.repository.crud.question import QuestionAttemptCRUDRepository
from src.worker.tasks import process_answer_transcription_task


async def claim(factory, attempt_id):
    async with factory() as session:
        row = await QuestionAttemptCRUDRepository(
            async_session=session
        ).mark_transcription_processing(
            question_attempt_id=attempt_id, checksum=CHECKSUM, answer_version=1
        )
        return (
            (CHECKSUM, row.answer_version, row.transcription_updated_at)
            if row
            else None
        )


@pytest.mark.asyncio
async def test_concurrent_claims_have_one_winner_including_same_audio(db):
    _, factory, user_id = db
    attempt_id = await seed(factory, user_id, "pending", 0)
    claims = await asyncio.gather(*(claim(factory, attempt_id) for _ in range(8)))
    assert sum(item is not None for item in claims) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["claim", "version", "checksum", "terminal", "deleted"]
)
@pytest.mark.parametrize(
    "write", ["transcript", "retry", "failure", "complete", "follow_up"]
)
async def test_old_claim_cannot_publish_or_reset_new_state(db, change, write):
    _, factory, user_id = db
    attempt_id = await seed(factory, user_id, "pending", 0)
    guard = await claim(factory, attempt_id)
    async with factory() as other:
        if change == "deleted":
            await other.execute(
                sa.delete(QuestionAttempt).where(QuestionAttempt.id == attempt_id)
            )
        else:
            changes = {
                "claim": {
                    "transcription_updated_at": guard[2] + datetime.timedelta(seconds=1)
                },
                "version": {"answer_version": 2},
                "checksum": {"answer_checksum": "b" * 64},
                "terminal": {"transcription_status": "transcribed"},
            }[change]
            await other.execute(
                sa.update(QuestionAttempt)
                .where(QuestionAttempt.id == attempt_id)
                .values(transcription={"text": "New durable transcript"}, **changes)
            )
        await other.commit()
    async with factory() as session:
        repo = QuestionAttemptCRUDRepository(async_session=session)
        kwargs = dict(question_attempt_id=attempt_id, processing_guard=guard)
        if write == "transcript":
            result = await repo.update_audio_transcription(
                **kwargs,
                checksum=CHECKSUM,
                audio_url="old/key",
                transcription={"text": "Stale result"},
                finalize=False,
            )
        elif write == "follow_up":
            result = await repo.resolve_follow_up(**kwargs, disposition="generated")
        else:
            fn = {
                "retry": repo.mark_transcription_pending,
                "failure": repo.mark_transcription_failed,
                "complete": repo.mark_transcription_complete,
            }[write]
            result = await fn(
                **kwargs,
                checksum=CHECKSUM,
                **({"error": "stale failure"} if write == "failure" else {}),
            )
        assert result is None
        current = await session.get(QuestionAttempt, attempt_id)
        if current:
            assert current.transcription == {"text": "New durable transcript"}
            assert current.audio_url == "private/recording.wav"
            assert current.transcription_error is None


@pytest.mark.asyncio
async def test_transcript_save_keeps_claim_until_completion_and_rejects_duplicate_finish(
    db,
):
    _, factory, user_id = db
    attempt_id = await seed(factory, user_id, "pending", 0)
    guard = await claim(factory, attempt_id)
    async with factory() as session:
        repo = QuestionAttemptCRUDRepository(async_session=session)
        saved = await repo.update_audio_transcription(
            question_attempt_id=attempt_id,
            checksum=CHECKSUM,
            audio_url="private/recording.wav",
            transcription={"text": "Complete durable answer"},
            finalize=False,
            processing_guard=guard,
        )
        assert saved.transcription_updated_at == guard[2]
        assert await repo.mark_transcription_complete(
            question_attempt_id=attempt_id, checksum=CHECKSUM, processing_guard=guard
        )
        assert (
            await repo.mark_transcription_complete(
                question_attempt_id=attempt_id,
                checksum=CHECKSUM,
                processing_guard=guard,
            )
            is None
        )
        assert (
            await repo.mark_transcription_failed(
                question_attempt_id=attempt_id,
                checksum=CHECKSUM,
                error="late",
                processing_guard=guard,
            )
            is None
        )


@pytest.mark.asyncio
async def test_repeated_audio_version_cannot_be_claimed_from_an_old_read(db):
    _, factory, user_id = db
    attempt_id = await seed(factory, user_id, "pending", 0, version=3)
    assert await claim(factory, attempt_id) is None


@pytest.mark.asyncio
async def test_queue_redelivery_never_inherits_an_active_worker_claim(db, monkeypatch):
    from src.worker import database
    from src.services import s3_service, whisper

    _, factory, user_id = db
    attempt_id = await seed(factory, user_id, "pending", 0)
    guard = await claim(factory, attempt_id)
    monkeypatch.setattr(database, "get_session", factory)
    fetch = AsyncMock(return_value=b"saved")
    transcribe = AsyncMock()
    monkeypatch.setattr(s3_service, "fetch_answer_audio_from_s3", fetch)
    monkeypatch.setattr(whisper, "transcribe_audio_with_whisper", transcribe)
    result = await process_answer_transcription_task(
        {"job_try": 2},
        question_attempt_id=attempt_id,
        checksum=CHECKSUM,
        language="en",
        filename="answer.wav",
    )
    assert result["status"] == "superseded"
    fetch.assert_not_awaited()
    transcribe.assert_not_awaited()
    async with factory() as session:
        row = await session.get(QuestionAttempt, attempt_id)
        assert (
            row.transcription_status == "processing"
            and row.transcription_updated_at == guard[2]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "retry", "exhausted"])
async def test_delayed_provider_result_cannot_overwrite_reclaimed_same_audio(
    db, monkeypatch, outcome
):
    from src.worker import database
    from src.services import s3_service, whisper

    _, factory, user_id = db
    attempt_id = await seed(factory, user_id, "pending", 0)
    monkeypatch.setattr(database, "get_session", factory)
    monkeypatch.setattr(
        s3_service, "fetch_answer_audio_from_s3", AsyncMock(return_value=b"saved")
    )
    newest = {}

    async def delayed_provider(**kwargs):
        async with factory() as other:
            await other.execute(
                sa.update(QuestionAttempt)
                .where(QuestionAttempt.id == attempt_id)
                .values(
                    transcription_updated_at=datetime.datetime.now(UTC)
                    - datetime.timedelta(minutes=20)
                )
            )
            await other.commit()
            repo = QuestionAttemptCRUDRepository(async_session=other)
            row = await repo.get_by_id(question_attempt_id=attempt_id)
            assert await repo.reserve_transcription_retry(
                question_attempt_id=attempt_id,
                user_id=user_id,
                checksum=CHECKSUM,
                answer_version=1,
                expected_status="processing",
                expected_updated_at=row.transcription_updated_at,
            )
        newest["guard"] = await claim(factory, attempt_id)
        if outcome == "success":
            return {"text": "A late transcript from the old worker"}, None, 1, "mock"
        return None, "private provider error", 1, "mock"

    monkeypatch.setattr(whisper, "transcribe_audio_with_whisper", delayed_provider)
    result = await process_answer_transcription_task(
        {"job_try": 3 if outcome == "exhausted" else 1},
        question_attempt_id=attempt_id,
        checksum=CHECKSUM,
        language="en",
        filename="answer.wav",
    )
    assert result["status"] == "superseded"
    async with factory() as session:
        row = await session.get(QuestionAttempt, attempt_id)
        assert row.transcription_status == "processing"
        assert row.transcription_updated_at == newest["guard"][2]
        assert row.transcription is None and row.transcription_error is None


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent_upload", [False, True])
async def test_replaced_answer_discards_late_follow_up_even_with_cached_orm_row(
    db, monkeypatch, concurrent_upload
):
    from src.services import follow_up

    _, factory, user_id = db
    attempt_id = await seed(factory, user_id, "pending", 0)
    guard = await claim(factory, attempt_id)
    async with factory() as session:
        row = await session.get(QuestionAttempt, attempt_id)
        question = InterviewQuestion(
            interview_id=row.interview_id,
            order=1,
            text="Tell me about your project",
            follow_up_strategy="llm_transcription_based",
        )
        session.add(question)
        await session.flush()
        row.question_id = question.id
        row.transcription = {
            "text": "I built a payment service with automated tests and metrics to detect failures early and help the team resolve issues."
        }
        row.follow_up_disposition = "pending"
        await session.commit()
        qid = question.id

        replacement_tasks = []
        interview_id = row.interview_id
        parent_locked = asyncio.Event()

        async def replace_answer():
            async with factory() as other:
                if concurrent_upload:
                    await other.get(Interview, interview_id, with_for_update=True)
                    parent_locked.set()
                    await asyncio.sleep(0.05)
                await other.execute(
                    sa.update(QuestionAttempt)
                    .where(QuestionAttempt.id == attempt_id)
                    .values(
                        answer_version=2,
                        answer_checksum="b" * 64,
                        transcription=None,
                        transcription_status="pending",
                        follow_up_disposition="pending",
                        transcription_updated_at=datetime.datetime.now(UTC),
                    )
                )
                await other.commit()

        async def delayed_follow_up(**kwargs):
            if concurrent_upload:
                replacement_tasks.append(asyncio.create_task(replace_answer()))
                await parent_locked.wait()
            else:
                await replace_answer()
            return "A stale follow-up", None, 1, "mock"

        model = AsyncMock(side_effect=delayed_follow_up)
        monkeypatch.setattr(follow_up, "generate_follow_up_question", model)
        result = await follow_up.FollowUpService(
            async_session=session
        ).handle_transcription_saved(attempt_id, processing_guard=guard)
        assert result is None
        model.assert_awaited_once()
        await asyncio.gather(*replacement_tasks)
    async with factory() as session:
        assert (
            not (
                await session.execute(
                    sa.select(InterviewQuestion).where(
                        InterviewQuestion.parent_question_id == qid
                    )
                )
            )
            .scalars()
            .all()
        )
        row = await session.get(QuestionAttempt, attempt_id)
        assert row.answer_version == 2 and row.follow_up_disposition == "pending"


@pytest.mark.asyncio
async def test_handled_provider_failure_retries_then_completes(db, monkeypatch):
    from arq.worker import Retry
    from src.worker import database
    from src.services import s3_service, whisper

    _, factory, user_id = db
    attempt_id = await seed(factory, user_id, "pending", 0)
    monkeypatch.setattr(database, "get_session", factory)
    monkeypatch.setattr(
        s3_service, "fetch_answer_audio_from_s3", AsyncMock(return_value=b"saved")
    )
    provider = AsyncMock(
        side_effect=[
            (None, "temporarily unavailable", 1, "mock"),
            ({"text": "A clear and complete durable answer"}, None, 1, "mock"),
        ]
    )
    monkeypatch.setattr(whisper, "transcribe_audio_with_whisper", provider)
    kwargs = dict(
        question_attempt_id=attempt_id,
        checksum=CHECKSUM,
        language="en",
        filename="answer.wav",
    )
    with pytest.raises(Retry):
        await process_answer_transcription_task({"job_try": 1}, **kwargs)
    async with factory() as session:
        row = await session.get(QuestionAttempt, attempt_id)
        assert (
            row.transcription_status == "pending"
            and row.audio_url == "private/recording.wav"
        )
    result = await process_answer_transcription_task({"job_try": 2}, **kwargs)
    assert result["status"] == "transcribed" and provider.await_count == 2


@pytest.mark.asyncio
async def test_recovered_claim_reuses_durable_transcript_after_worker_crash(
    db, monkeypatch
):
    from src.worker import database
    from src.services import s3_service, whisper

    _, factory, user_id = db
    attempt_id = await seed(factory, user_id, "processing", 20)
    async with factory() as session:
        repo = QuestionAttemptCRUDRepository(async_session=session)
        row = await session.get(QuestionAttempt, attempt_id)
        row.transcription = {
            "text": "Already saved clear speech from the correct recording"
        }
        await session.commit()
        assert await repo.reserve_transcription_retry(
            question_attempt_id=attempt_id,
            user_id=user_id,
            checksum=CHECKSUM,
            answer_version=1,
            expected_status="processing",
            expected_updated_at=row.transcription_updated_at,
        )
    monkeypatch.setattr(database, "get_session", factory)
    fetch = AsyncMock()
    provider = AsyncMock()
    monkeypatch.setattr(s3_service, "fetch_answer_audio_from_s3", fetch)
    monkeypatch.setattr(whisper, "transcribe_audio_with_whisper", provider)
    result = await process_answer_transcription_task(
        {"job_try": 1},
        question_attempt_id=attempt_id,
        checksum=CHECKSUM,
        language="en",
        filename="answer.wav",
    )
    assert result["status"] == "transcribed"
    fetch.assert_not_awaited()
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_replacement_recordings_advance_version_from_locked_row(db):
    _, factory, user_id = db
    attempt_id = await seed(factory, user_id, "pending", 0)

    async def save(checksum):
        async with factory() as session:
            repo = QuestionAttemptCRUDRepository(async_session=session)
            # Deliberately populate both identity maps with the old version.
            await repo.get_by_id(question_attempt_id=attempt_id)
            await asyncio.sleep(0)
            await repo.save_audio_asset(
                question_attempt_id=attempt_id,
                audio_url=f"private/{checksum}.wav",
                checksum=checksum,
            )

    await asyncio.gather(save("b" * 64), save("c" * 64))
    async with factory() as session:
        row = await session.get(QuestionAttempt, attempt_id)
        assert row.answer_version == 3 and row.transcription_status == "pending"
        assert row.answer_checksum in ("b" * 64, "c" * 64)
