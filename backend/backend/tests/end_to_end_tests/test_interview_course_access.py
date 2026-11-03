"""Revoke participation without deleting saved answers, receipts or processing."""

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import fastapi
import sqlalchemy as sa
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401
from src.api.routes import interviews, audio
from src.models.db.interview import Interview
from src.models.db.user import User
from src.models.db.question_attempt import QuestionAttempt
from src.repository.crud.interview import InterviewCRUDRepository
from src.models.schemas.interview import ResumeInterviewRequest
from src.services.interview_course_access import require_interview_course_access
from src.services import sampark_enrollment_sync


async def seed(factory, actor_id, mode="practice"):
    async with factory() as session:
        student = await session.get(User, actor_id)
        student.enrolled_course_ids = ["course-a"]
        student.enrollment_source = "sampark"
        student.enrollment_observed_at = datetime.datetime.now(datetime.timezone.utc)
        row = Interview(
            user_id=actor_id,
            course_id="course-a",
            interview_mode=mode,
            status="active",
            track="Communication",
        )
        session.add(row)
        await session.flush()
        attempt = QuestionAttempt(
            interview_id=row.id,
            question_text="Stored prompt",
            audio_url="private/saved.wav",
            answer_kind="audio",
            answer_checksum="a" * 64,
            transcription_status="pending",
        )
        session.add(attempt)
        await session.commit()
        return row.id, attempt.id


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["practice", "assessment"])
async def test_revoked_enrollment_blocks_resume_begin_and_answer_without_erasing_evidence(
    db, mode
):
    _, factory, actor_id = db
    interview_id, attempt_id = await seed(factory, actor_id, mode)
    async with factory() as session:
        repo = InterviewCRUDRepository(async_session=session)
        # Force an identity-map cache to exist before another transaction revokes enrollment.
        await session.get(User, actor_id)
        async with factory() as other:
            (await other.get(User, actor_id)).enrolled_course_ids = []
            await other.commit()
        current = SimpleNamespace(id=actor_id)
        for operation in [
            lambda: interviews.begin_fixed_interview(
                ResumeInterviewRequest(interview_id=interview_id),
                current,
                repo,
                SimpleNamespace(list_by_interview=AsyncMock()),
            ),
            lambda: interviews.resume_interview(
                ResumeInterviewRequest(interview_id=interview_id),
                current,
                repo,
                SimpleNamespace(),
                SimpleNamespace(),
            ),
            lambda: audio._require_active_interview(
                SimpleNamespace(interview_id=interview_id),
                repo,
                user_id=actor_id,
                lock=True,
            ),
        ]:
            with pytest.raises(fastapi.HTTPException) as error:
                await operation()
            assert (
                error.value.status_code == 403
                and error.value.detail["code"] == "COURSE_ACCESS_REVOKED"
            )
            await session.rollback()
        row = await session.get(Interview, interview_id)
        attempt = await session.get(QuestionAttempt, attempt_id)
        assert (
            row.status == "active"
            and attempt.audio_url == "private/saved.wav"
            and attempt.transcription_status == "pending"
        )
        # Restore access; original attempt can continue subject to its existing deadline/lease rules.
        async with factory() as other:
            (await other.get(User, actor_id)).enrolled_course_ids = ["course-a"]
            await other.commit()
        await require_interview_course_access(session, row)


@pytest.mark.asyncio
async def test_stale_source_blocks_lease_renewal_and_claim_but_keeps_token_and_receipt(
    db, monkeypatch
):
    _, factory, actor_id = db
    monkeypatch.setattr(sampark_enrollment_sync, "enabled", lambda: True)
    # imported fresh resolves enabled in the source module.
    interview_id, _ = await seed(factory, actor_id)
    async with factory() as session:
        repo = InterviewCRUDRepository(async_session=session)
        _, _, token = await repo.claim_session_lease(
            interview_id=interview_id,
            user_id=actor_id,
            presented_token=None,
            force=False,
        )
        original = (await session.get(Interview, interview_id)).session_lease_hash
        async with factory() as other:
            (await other.get(User, actor_id)).enrollment_observed_at = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(days=30)
            )
            await other.commit()
        for operation in [
            lambda: repo.heartbeat_session_lease(
                interview_id=interview_id, user_id=actor_id, lease_token=token
            ),
            lambda: repo.claim_session_lease(
                interview_id=interview_id,
                user_id=actor_id,
                presented_token=token,
                force=True,
            ),
        ]:
            with pytest.raises(fastapi.HTTPException) as error:
                await operation()
            assert error.value.detail["code"] == "COURSE_ACCESS_UNVERIFIED"
            await session.rollback()
        row = await session.get(Interview, interview_id)
        assert row.session_lease_hash == original and row.status == "active"
        row.status = "completed"
        row.submission_receipt = {"planned": 1, "answered": 1}
        await session.commit()
        await require_interview_course_access(session, row)
        receipt = await interviews.get_submission_receipt(
            interview_id, SimpleNamespace(id=actor_id), repo
        )
        assert receipt["receipt"] == {"planned": 1, "answered": 1}
