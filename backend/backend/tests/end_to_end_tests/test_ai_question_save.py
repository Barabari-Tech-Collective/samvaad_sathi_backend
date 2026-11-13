"""Saving AI-written questions keeps the good ones when a few are blank or repeated."""

import fastapi
import pytest

from src.models.db.job_profile import JobProfile
from src.models.db.job_profile_question import JobProfileQuestion
from src.repository.crud.job_profile import JobProfileCRUDRepository

from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


async def _draft(session, existing=()):
    profile = JobProfile(
        job_name="Full Stack Developer",
        job_description="Build and run web applications end to end.",
        status="draft",
        interview_mode="assessment",
        questions_per_interview=5,
        generation_state="in_progress",
        generation_token="tok",
        content_revision=0,
    )
    session.add(profile)
    await session.flush()
    for text in existing:
        session.add(JobProfileQuestion(job_profile_id=profile.id, question_text=text, level=1, difficulty="easy", question_type="tech"))
    await session.commit()
    return profile.id


def _q(pid, text, level=1):
    return {"job_profile_id": pid, "question_text": text, "level": level}


async def test_ai_output_skips_repeats_and_blanks_and_saves_the_rest(db):
    _, factory, _ = db
    async with factory() as session:
        pid = await _draft(session, existing=["What is a REST API?"])
        repo = JobProfileCRUDRepository(async_session=session)
        saved = await repo.create_job_profile_questions(
            [
                _q(pid, "What is a REST API?"),  # already in the draft
                _q(pid, "Explain database indexing."),
                _q(pid, "explain   DATABASE indexing."),  # repeat in the same batch
                _q(pid, "   "),  # blank
                _q(pid, "How would you design a rate limiter?", 2),
            ],
            expected_revision=0,
            generation_token="tok",
        )
        assert sorted(q.question_text for q in saved) == [
            "Explain database indexing.",
            "How would you design a rate limiter?",
        ]
        profile = await session.get(JobProfile, pid)
        assert profile.generation_state == "complete" and profile.generation_result_count == 2


async def test_ai_output_with_nothing_usable_still_fails(db):
    _, factory, _ = db
    async with factory() as session:
        pid = await _draft(session, existing=["What is a REST API?"])
        repo = JobProfileCRUDRepository(async_session=session)
        with pytest.raises(fastapi.HTTPException) as err:
            await repo.create_job_profile_questions(
                [_q(pid, "What is a REST API?"), _q(pid, "")],
                expected_revision=0,
                generation_token="tok",
            )
        assert err.value.status_code == 409


async def test_staff_input_stays_strict(db):
    _, factory, _ = db
    async with factory() as session:
        pid = await _draft(session, existing=["What is a REST API?"])
        repo = JobProfileCRUDRepository(async_session=session)
        with pytest.raises(fastapi.HTTPException):
            await repo.create_job_profile_questions([_q(pid, "What is a REST API?")])
