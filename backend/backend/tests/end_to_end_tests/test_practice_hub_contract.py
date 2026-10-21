"""Current-question progress and advisory course readiness on PostgreSQL."""

import datetime

import pytest
import sqlalchemy as sa

from .test_fixed_question_rounds import client, db, pytestmark  # noqa: F401
from src.models.db.interview import Interview
from src.models.db.interview_question import InterviewQuestion
from src.models.db.question_attempt import QuestionAttempt
from src.models.db.user import User
from src.services.practice_hub import build_practice_hub
from src.services import sampark_enrollment_sync as sync

UTC = datetime.timezone.utc


async def seed(
    factory,
    user_id,
    count=2,
    plan=None,
    course=None,
    status="paused",
    mode="practice",
    expires=None,
):
    async with factory() as session:
        interview = Interview(
            user_id=user_id,
            track="Communication",
            difficulty="medium",
            status=status,
            interview_mode=mode,
            question_plan=plan,
            course_id=course,
            expires_at=expires,
        )
        session.add(interview)
        await session.flush()
        questions = [
            InterviewQuestion(
                interview_id=interview.id, text=f"Prompt {i}", order=i + 1
            )
            for i in range(count)
        ]
        session.add_all(questions)
        await session.commit()
        return interview.id, [q.id for q in questions]


async def attempt(session, interview_id, question_id, **kwargs):
    row = QuestionAttempt(
        interview_id=interview_id,
        question_id=question_id,
        question_text="Saved prompt",
        **kwargs,
    )
    session.add(row)
    await session.flush()
    return row


async def card(factory, user_id, now=None):
    async with factory() as session:
        return (await build_practice_hub(session, user_id=user_id, now=now)).resume


@pytest.mark.asyncio
async def test_latest_attempt_per_current_question_is_counted_once(db):
    _, factory, user_id = db
    interview_id, qs = await seed(factory, user_id)
    other_id, other_qs = await seed(factory, user_id, mode="assessment")
    async with factory() as session:
        for _ in range(4):
            await attempt(
                session, interview_id, qs[0], answer_saved_at=datetime.datetime.now(UTC)
            )
        # A new unsaved retry supersedes an older saved attempt for q2.
        await attempt(session, interview_id, qs[1], audio_url="private/old.wav")
        await attempt(session, interview_id, qs[1], transcription=None)
        # Neither unbound legacy evidence nor a question in another snapshot counts.
        await attempt(session, interview_id, None, audio_url="private/unbound.wav")
        await attempt(
            session, interview_id, other_qs[0], audio_url="private/foreign-question.wav"
        )
        await attempt(
            session, other_id, qs[1], audio_url="private/foreign-interview.wav"
        )
        await session.commit()
    result = await card(factory, user_id)
    assert result.interview_id == interview_id
    assert result.total_questions == 2 and result.answered_questions == 1
    assert result.readiness == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence,expected",
    [
        ("saved_timestamp", 1),
        ("pending_audio", 1),
        ("failed_audio", 1),
        ("legacy_transcript", 1),
        ("json_null", 0),
        ("sql_null", 0),
        ("empty_audio", 0),
        ("skipped", 0),
        ("skipped_retained_audio", 1),
    ],
)
async def test_durable_answer_semantics_match_retained_navigation_evidence(
    db, evidence, expected
):
    _, factory, user_id = db
    interview_id, qs = await seed(factory, user_id, count=1)
    values = {
        "saved_timestamp": {"answer_saved_at": datetime.datetime.now(UTC)},
        "pending_audio": {
            "audio_url": "private/saved.wav",
            "transcription_status": "pending",
        },
        "failed_audio": {
            "audio_url": "private/saved.wav",
            "transcription_status": "failed",
        },
        "legacy_transcript": {"transcription": {"text": "Legacy saved speech"}},
        "json_null": {"transcription": None},
        "sql_null": {"transcription": sa.null()},
        "empty_audio": {"audio_url": ""},
        "skipped": {"attempt_state": "skipped"},
        "skipped_retained_audio": {
            "attempt_state": "skipped",
            "audio_url": "private/retained.wav",
        },
    }[evidence]
    async with factory() as session:
        await attempt(session, interview_id, qs[0], **values)
        await session.commit()
    result = await card(factory, user_id)
    assert result.answered_questions == expected and result.total_questions == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "count,plan,readiness,total",
    [
        (0, None, "preparing", 0),
        (1, None, "ready", 1),
        (2, {"version": 1, "total": 3, "adaptive_slots": 0}, "preparing", 3),
        (3, {"version": 1, "total": 3, "adaptive_slots": 0}, "ready", 3),
        (4, {"version": 1, "total": 3, "adaptive_slots": 0}, "plan_unavailable", None),
        (2, {"version": 2, "total": 3}, "plan_unavailable", None),
        (2, {"version": 1, "total": 0}, "plan_unavailable", None),
        (2, [], "plan_unavailable", None),
        (2, {}, "plan_unavailable", None),
    ],
)
async def test_fixed_and_legacy_preparation_never_invent_counts(
    db, count, plan, readiness, total
):
    _, factory, user_id = db
    interview_id, qs = await seed(factory, user_id, count=count, plan=plan)
    async with factory() as session:
        for qid in qs:
            await attempt(session, interview_id, qid, audio_url="private/saved.wav")
        await session.commit()
    result = await card(factory, user_id)
    assert result.readiness == readiness and result.total_questions == total
    assert result.answered_questions == (None if total is None else count)
    # Private plan data is never exposed to students.
    assert "questionPlan" not in result.model_dump(by_alias=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,expected",
    [
        ("active", "ready"),
        ("revoked", "enrollment_revoked"),
        ("stale", "enrollment_unverified"),
        ("future", "enrollment_unverified"),
        ("missing_time", "enrollment_unverified"),
        ("wrong_source", "enrollment_unverified"),
    ],
)
async def test_readiness_uses_the_existing_course_participation_policy(
    db, monkeypatch, state, expected
):
    _, factory, user_id = db
    interview_id, qs = await seed(factory, user_id, course="course-a")
    monkeypatch.setattr(sync, "enabled", lambda: True)
    monkeypatch.setattr(sync, "max_age", lambda: 3600)
    async with factory() as session:
        student = await session.get(User, user_id)
        student.enrolled_course_ids = [] if state == "revoked" else ["course-a"]
        student.enrollment_source = "other" if state == "wrong_source" else "sampark"
        student.enrollment_observed_at = (
            None
            if state == "missing_time"
            else datetime.datetime.now(UTC)
            + datetime.timedelta(
                hours=-2 if state == "stale" else 2 if state == "future" else 0
            )
        )
        await attempt(session, interview_id, qs[0], audio_url="private/saved.wav")
        await session.commit()
    result = await card(factory, user_id)
    assert result.readiness == expected
    assert result.answered_questions == 1 and result.interview_id == interview_id
    async with factory() as session:
        # Readiness must not delete answers, terminate or mutate the sitting.
        row = await session.get(Interview, interview_id)
        assert row.status == "paused"
        assert (
            await session.execute(
                sa.select(QuestionAttempt.audio_url).where(
                    QuestionAttempt.interview_id == interview_id
                )
            )
        ).scalar_one() == "private/saved.wav"


@pytest.mark.asyncio
async def test_latest_blocked_sitting_is_not_silently_replaced_and_restoration_is_reflected(
    db,
):
    _, factory, user_id = db
    await seed(factory, user_id)
    latest, _ = await seed(factory, user_id, course="course-a")
    result = await card(factory, user_id)
    assert result.interview_id == latest and result.readiness == "enrollment_revoked"
    async with factory() as session:
        student = await session.get(User, user_id)
        student.enrolled_course_ids = ["course-a"]
        await session.commit()
    result = await card(factory, user_id)
    assert result.interview_id == latest and result.readiness == "ready"


@pytest.mark.asyncio
async def test_owner_mode_status_expiry_and_tied_created_times_are_deterministic(db):
    _, factory, user_id = db
    now = datetime.datetime.now(UTC)
    valid, _ = await seed(factory, user_id)
    newer, _ = await seed(factory, user_id)
    await seed(factory, user_id, mode="assessment")
    await seed(factory, user_id, status="completed")
    await seed(factory, user_id, expires=now)
    async with factory() as session:
        other = User(email="other-hub@example.test", name="Other", password_hash="test")
        session.add(other)
        await session.flush()
        other_id = other.id
        await session.execute(
            sa.update(Interview)
            .where(Interview.id.in_([valid, newer]))
            .values(created_at=now - datetime.timedelta(days=1))
        )
        await session.commit()
    await seed(factory, other_id)
    result = await card(factory, user_id, now)
    assert result.interview_id == newer


@pytest.mark.asyncio
async def test_preparing_status_and_null_enrollment_legacy_compatibility(
    db, monkeypatch
):
    _, factory, user_id = db
    monkeypatch.setattr(sync, "enabled", lambda: True)
    interview_id, _ = await seed(factory, user_id, status="preparing")
    result = await card(factory, user_id)
    assert result.interview_id == interview_id and result.readiness == "preparing"


@pytest.mark.asyncio
async def test_empty_hub_does_not_invent_a_resume_card(db):
    _, factory, user_id = db
    result = await card(factory, user_id)
    assert result is None


@pytest.mark.asyncio
async def test_legacy_saved_evidence_without_snapshot_is_not_claimed_to_be_preparing(
    db,
):
    _, factory, user_id = db
    interview_id, _ = await seed(factory, user_id, count=0)
    async with factory() as session:
        await attempt(session, interview_id, None, audio_url="private/unmapped.wav")
        await session.commit()
    result = await card(factory, user_id)
    assert result.readiness == "plan_unavailable"
    assert result.answered_questions is None and result.total_questions is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition", ["ready", "enrollment_revoked", "plan_unavailable"]
)
async def test_http_hub_contract_serializes_readiness_and_nullable_progress(
    client, db, condition
):
    _, factory, user_id = db
    interview_id, qs = await seed(
        factory,
        user_id,
        course="course-a" if condition == "enrollment_revoked" else None,
        plan={"version": 9} if condition == "plan_unavailable" else None,
    )
    async with factory() as session:
        await attempt(
            session, interview_id, qs[0], audio_url="private/audio-must-not-appear.wav"
        )
        await session.commit()
    response = await client.get("/practice/hub")
    assert response.status_code == 200, response.text
    resume = response.json()["resume"]
    assert resume["interviewId"] == interview_id and resume["readiness"] == condition
    assert resume["answeredQuestions"] == (
        None if condition == "plan_unavailable" else 1
    )
    assert resume["totalQuestions"] == (None if condition == "plan_unavailable" else 2)
    assert "private/audio-must-not-appear.wav" not in response.text
    assert "questionPlan" not in response.text


@pytest.mark.asyncio
async def test_restoring_enrollment_does_not_mask_an_independent_plan_problem(db):
    _, factory, user_id = db
    await seed(factory, user_id, course="course-a", plan={"version": 2, "total": 2})
    result = await card(factory, user_id)
    assert result.readiness == "enrollment_revoked" and result.total_questions is None
    async with factory() as session:
        student = await session.get(User, user_id)
        student.enrolled_course_ids = ["course-a"]
        await session.commit()
    result = await card(factory, user_id)
    assert result.readiness == "plan_unavailable" and result.answered_questions is None
