"""A student may only ever sit a facilitator's published questions.

This is the behavioural proof behind the rule, driven through the real app over
ASGI against a real database. The structural guard in
tests/unit_tests/test_interview_eligibility.py catches a generation site written
without any notion of curated interviews, but it cannot prove an existing guard
still works - removing the branch while leaving other `job_profile_id` mentions
in the function passes it. These tests cannot be fooled that way: the generator
is a stub that records every call, so "the LLM was never asked" is asserted
directly rather than inferred from the shape of the code.

The scenario each test is built from: a facilitator publishes questions for one
role at **easy only**. Before this work a student could pick medium - the picker
offered all four levels and defaulted to medium - creation accepted it because
it only checked the profile existed, and the generator then quietly fell through
to the LLM. The student was assessed against that facilitator's role on
questions the facilitator had never seen, and nothing in the response or the
report said so.

Set SCHEMA_TEST_DATABASE_URL to a throwaway database to enable.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import src.models.db  # noqa: F401  # registers every model on Base.metadata
from src.api.dependencies.auth import get_current_user
from src.models.db.interview import Interview
from src.models.db.job_profile import JobProfile, JobProfileStatus
from src.models.db.job_profile_question import JobProfileQuestion
from src.models.db.user import User
from src.repository.table import Base
from src.services.interview_eligibility import LEVEL_TO_DIFFICULTY, MINIMUM_QUESTIONS_PER_LEVEL

_RAW_URL = os.environ.get("SCHEMA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="Set SCHEMA_TEST_DATABASE_URL to a throwaway PostgreSQL database to run",
)

CREATE_PATH = "/api/v2/interviews/create"
V1_GENERATE_PATH = "/api/interviews/generate-questions"
V2_GENERATE_PATH = "/api/v2/interviews/generate-questions"
TRACK = "Backend Developer"


def _async_url(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.fixture
async def database():
    from src.repository.database import async_db

    engine = create_async_engine(_async_url(_RAW_URL))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    original_factory = async_db.async_session_factory
    async_db.async_session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield engine
    finally:
        async_db.async_session_factory = original_factory
        await engine.dispose()


@pytest.fixture
async def seed(database):
    """Builds a facilitator profile with questions at chosen levels only."""

    async def _seed(*, status=JobProfileStatus.PUBLISHED, questions_per_level=None):
        questions_per_level = questions_per_level or {1: MINIMUM_QUESTIONS_PER_LEVEL}
        sessionmaker = async_sessionmaker(bind=database, expire_on_commit=False)
        async with sessionmaker() as session:
            user = User(
                email="curated@example.test",
                password_hash="not-a-real-hash",
                name="Curated Probe",
            )
            session.add(user)

            profile = JobProfile(
                job_name=TRACK,
                job_description="A role a facilitator curated.",
                status=status,
            )
            session.add(profile)
            await session.flush()

            for level, count in questions_per_level.items():
                for index in range(count):
                    session.add(
                        JobProfileQuestion(
                            job_profile_id=profile.id,
                            level=level,
                            # Both columns describe the same fact; the writers
                            # derive one from the other, so seed them in step.
                            difficulty=LEVEL_TO_DIFFICULTY[level],
                            question_text=f"Facilitator level-{level} question {index}",
                        )
                    )
            await session.commit()
            return user.id, profile.id

    return _seed


@pytest.fixture
def app(database, monkeypatch):
    """The real app, with every generator replaced by a recording stub."""
    from src.api.routes import interviews, interviews_v2
    from src.main import initialize_backend_application

    calls: list[str] = []

    async def fake_generate(*_args, **_kwargs):
        calls.append("generated")
        items = [
            {"text": f"AI invented question {i}", "topic": "ai", "category": "tech"}
            for i in range(1, 6)
        ]
        return [item["text"] for item in items], None, 10, "stub-llm", items

    monkeypatch.setattr(interviews, "generate_interview_questions_with_llm", fake_generate)
    monkeypatch.setattr(interviews_v2, "generate_full_stack_questions_with_llm", fake_generate)
    monkeypatch.setattr(interviews_v2, "_get_supplement_map", lambda **_kw: _empty_map())
    monkeypatch.setattr(interviews_v2, "_prepare_audio_for_questions", lambda _q: [])

    application = initialize_backend_application()
    application.state.generator_calls = calls
    application.state.principal_id = 1

    class _Principal:
        id = 1
        resume_text = None
        years_experience = 2.0
        skills = {"items": ["python"]}
        target_position = TRACK

    application.dependency_overrides[get_current_user] = lambda: _Principal()
    return application


async def _empty_map():
    return {}


async def _post(app, path: str, payload: dict):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://probe"
    ) as client:
        return await client.post(path, json=payload)


async def _interview_at(database, *, profile_id: int, difficulty: str) -> int:
    """Insert an interview directly, bypassing creation's refusal.

    Used to test the generator's own guard: creation already refuses these, so
    the only way to reach the generator in this state is a profile that changed
    under an in-flight interview, or a client calling the generator directly.
    """
    sessionmaker = async_sessionmaker(bind=database, expire_on_commit=False)
    async with sessionmaker() as session:
        interview = Interview(
            user_id=1,
            track=TRACK,
            difficulty=difficulty,
            status="active",
            job_profile_id=profile_id,
        )
        session.add(interview)
        await session.commit()
        return interview.id


# --------------------------------------------------------------------------
# Creation refuses a level the facilitator did not publish
# --------------------------------------------------------------------------


async def test_cannot_start_a_level_the_facilitator_did_not_publish(app, seed):
    """The reported scenario: easy published, student asks for medium."""
    _, profile_id = await seed()

    response = await _post(
        app, CREATE_PATH, {"track": TRACK, "difficulty": "medium", "jobProfileId": profile_id}
    )

    assert response.status_code == 409, response.text
    assert "medium" in response.json()["detail"]
    # And it tells them what they can actually sit.
    assert "easy" in response.json()["detail"]


async def test_can_start_the_level_the_facilitator_did_publish(app, seed):
    _, profile_id = await seed()

    response = await _post(
        app, CREATE_PATH, {"track": TRACK, "difficulty": "easy", "jobProfileId": profile_id}
    )

    assert response.status_code in (200, 201), response.text
    assert response.json()["difficulty"] == "easy"


async def test_cannot_start_a_level_with_too_few_questions(app, seed):
    """Below the minimum the level is refused, not served short."""
    _, profile_id = await seed(
        questions_per_level={1: MINIMUM_QUESTIONS_PER_LEVEL, 2: MINIMUM_QUESTIONS_PER_LEVEL - 1}
    )

    response = await _post(
        app, CREATE_PATH, {"track": TRACK, "difficulty": "medium", "jobProfileId": profile_id}
    )

    assert response.status_code == 409, response.text


@pytest.mark.parametrize(
    "status",
    [
        JobProfileStatus.DRAFT,
        JobProfileStatus.UNDER_REVIEW,
        JobProfileStatus.CHANGES_REQUESTED,
    ],
)
async def test_cannot_start_an_unpublished_profile(app, seed, status):
    """Even with plenty of questions, unpublished content is not sittable."""
    _, profile_id = await seed(
        status=status, questions_per_level={1: 20, 2: 20, 3: 20, 4: 20}
    )

    response = await _post(
        app, CREATE_PATH, {"track": TRACK, "difficulty": "easy", "jobProfileId": profile_id}
    )

    assert response.status_code == 409, response.text


async def test_unknown_difficulty_is_rejected_rather_than_coerced(app, seed):
    """It used to silently become medium."""
    _, profile_id = await seed()

    response = await _post(
        app,
        CREATE_PATH,
        {"track": TRACK, "difficulty": "intermediate", "jobProfileId": profile_id},
    )

    assert response.status_code == 400, response.text


# --------------------------------------------------------------------------
# Generation serves the facilitator, or refuses - never the LLM
# --------------------------------------------------------------------------


async def test_curated_interview_serves_the_facilitators_questions(app, seed, database):
    _, profile_id = await seed()
    created = await _post(
        app, CREATE_PATH, {"track": TRACK, "difficulty": "easy", "jobProfileId": profile_id}
    )
    interview_id = created.json()["interviewId"]

    response = await _post(
        app, V2_GENERATE_PATH, {"interviewId": interview_id, "useResume": False}
    )

    assert response.status_code == 201, response.text
    texts = [item["text"] for item in response.json()["items"]]
    assert all(text.startswith("Facilitator level-1") for text in texts), texts
    assert app.state.generator_calls == [], "The LLM was asked despite a curated bank existing"


async def test_generator_refuses_rather_than_inventing_questions(app, seed, database):
    """The core defect, asserted directly: no fallback to the LLM.

    The interview is inserted at a level with no published questions, which is
    the state a profile edited mid-interview would leave behind.
    """
    _, profile_id = await seed()
    interview_id = await _interview_at(database, profile_id=profile_id, difficulty="medium")

    response = await _post(
        app, V2_GENERATE_PATH, {"interviewId": interview_id, "useResume": False}
    )

    assert response.status_code == 409, (
        f"Expected a refusal, got {response.status_code}: {response.text}"
    )
    assert app.state.generator_calls == [], (
        "The LLM invented questions for a facilitator's role. A student would have been "
        "assessed on questions the facilitator never wrote."
    )


async def test_nothing_is_persisted_when_generation_is_refused(app, seed, database):
    """A refusal must not leave a half-built interview behind.

    Question sets are immutable once persisted, so a set written during a
    refused request would become permanent for that student.
    """
    _, profile_id = await seed()
    interview_id = await _interview_at(database, profile_id=profile_id, difficulty="hard")

    await _post(app, V2_GENERATE_PATH, {"interviewId": interview_id, "useResume": False})

    sessionmaker = async_sessionmaker(bind=database, expire_on_commit=False)
    async with sessionmaker() as session:
        interview = await session.get(Interview, interview_id, options=[])
        await session.refresh(interview, ["questions"])
        assert interview.questions == [], (
            f"A refused generation still persisted {len(interview.questions)} questions"
        )


async def test_v1_generator_refuses_curated_interviews(app, seed, database):
    """v1 has no job-profile awareness, so it must not serve an assessment.

    It would otherwise generate from the syllabus and the LLM, and because
    question sets are immutable once written, whichever generator ran first
    would decide the student's questions permanently.
    """
    _, profile_id = await seed()
    interview_id = await _interview_at(database, profile_id=profile_id, difficulty="easy")

    response = await _post(
        app, V1_GENERATE_PATH, {"interviewId": interview_id, "useResume": False}
    )

    assert response.status_code == 409, response.text
    assert app.state.generator_calls == []


# --------------------------------------------------------------------------
# Resuming
# --------------------------------------------------------------------------


async def test_starting_a_different_level_does_not_resume_the_old_one(app, seed):
    """Difficulty is part of an interview's identity.

    A student holding an active easy interview who started a medium one used to
    be silently resumed into the easy one, and told the difficulty was easy.
    """
    _, profile_id = await seed(
        questions_per_level={1: MINIMUM_QUESTIONS_PER_LEVEL, 2: MINIMUM_QUESTIONS_PER_LEVEL}
    )

    first = await _post(
        app, CREATE_PATH, {"track": TRACK, "difficulty": "easy", "jobProfileId": profile_id}
    )
    second = await _post(
        app, CREATE_PATH, {"track": TRACK, "difficulty": "medium", "jobProfileId": profile_id}
    )

    assert first.json()["difficulty"] == "easy"
    assert second.json()["difficulty"] == "medium", (
        "Asked for medium and got "
        f"{second.json()['difficulty']} - resumed into the wrong interview"
    )
    assert second.json()["interviewId"] != first.json()["interviewId"]


async def test_restarting_the_same_level_resumes(app, seed):
    """Resuming still works when the level does match."""
    _, profile_id = await seed()

    first = await _post(
        app, CREATE_PATH, {"track": TRACK, "difficulty": "easy", "jobProfileId": profile_id}
    )
    second = await _post(
        app, CREATE_PATH, {"track": TRACK, "difficulty": "easy", "jobProfileId": profile_id}
    )

    assert second.json()["interviewId"] == first.json()["interviewId"]
    assert second.json()["resumed"] is True


# --------------------------------------------------------------------------
# What the client is told it may offer
# --------------------------------------------------------------------------


async def test_available_difficulties_drive_the_picker(app, seed, database):
    """The threshold must stay server-owned, so the API reports availability.

    The picker was a hardcoded list of all four levels; this is the field that
    replaces it, so that the picker and interview creation cannot disagree.
    """
    from src.models.schemas.job_profile import JobProfileResponse

    _, profile_id = await seed(
        questions_per_level={1: MINIMUM_QUESTIONS_PER_LEVEL, 3: MINIMUM_QUESTIONS_PER_LEVEL - 1}
    )

    sessionmaker = async_sessionmaker(bind=database, expire_on_commit=False)
    async with sessionmaker() as session:
        profile = await session.get(JobProfile, profile_id)
        await session.refresh(profile, ["questions"])
        payload = JobProfileResponse.model_validate(profile).model_dump(by_alias=True)

    assert payload["availableDifficulties"] == ["easy"], payload["availableDifficulties"]
