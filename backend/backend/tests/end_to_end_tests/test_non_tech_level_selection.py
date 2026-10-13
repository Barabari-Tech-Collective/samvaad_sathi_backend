"""Non-tech interviews must find the facilitator's questions wherever they filed them.

The non-tech flow sets its own difficulty and shows the student no level
picker, so it cannot ask which level they meant. It used to pin that difficulty
to NON_TECH_FIXED_DIFFICULTY ("medium") and hardcode the question lookup to the
matching level. A facilitator who published non-tech questions at easy, hard or
expert therefore had their questions served to nobody - not occasionally, never
- while students silently received the generic blueprint instead. The student's
`difficulty` field was read into a variable used only for an analytics log.

The rule now: serve the facilitator's level when they published enough there,
preferring the configured difficulty, otherwise the easiest level they did
publish. The blueprint stays for roles with no usable curated content, because
there is no curated set to prefer - and the two are never mixed, so a student
is not scored on a blend of approved and generic questions.

The no-regression tests matter as much as the fix: this flow creates the
interview itself, so a refusal would stop a student starting a non-tech
interview at all. Roles that get the blueprint today must keep getting it.

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
from src.services.non_tech_blueprint import NON_TECH_FIXED_DIFFICULTY

_RAW_URL = os.environ.get("SCHEMA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="Set SCHEMA_TEST_DATABASE_URL to a throwaway PostgreSQL database to run",
)

NON_TECH_PATH = "/api/v2/interviews/non-tech/generate-questions"
#: Must contain "hr"/"non-technical"/"communication" for the non-tech routing.
NON_TECH_CATEGORY = "HR & Non-Technical"
JOB_NAME = "Customer Support Associate"


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
    # Some tests seed two roles to compare them, and the student is the same
    # person in both, so the user is inserted once rather than per call.
    created_user = {"done": False}

    async def _seed(questions_per_level=None):
        sessionmaker = async_sessionmaker(bind=database, expire_on_commit=False)
        async with sessionmaker() as session:
            if not created_user["done"]:
                session.add(
                    User(
                        email="nontech@example.test",
                        password_hash="not-a-real-hash",
                        name="Non Tech Probe",
                    )
                )
                created_user["done"] = True
            profile = JobProfile(
                job_name=JOB_NAME,
                job_description="A non-tech role a facilitator curated.",
                category=NON_TECH_CATEGORY,
                status=JobProfileStatus.PUBLISHED,
            )
            session.add(profile)
            await session.flush()

            for level, count in (questions_per_level or {}).items():
                for index in range(count):
                    session.add(
                        JobProfileQuestion(
                            job_profile_id=profile.id,
                            level=level,
                            difficulty=LEVEL_TO_DIFFICULTY[level],
                            question_text=f"Facilitator level-{level} question {index}",
                        )
                    )
            await session.commit()
            return profile.id

    return _seed


@pytest.fixture
def app(database, monkeypatch):
    from src.api.routes import interviews_v2
    from src.main import initialize_backend_application

    monkeypatch.setattr(interviews_v2, "_get_supplement_map", lambda **_kw: _empty_map())
    monkeypatch.setattr(interviews_v2, "_prepare_audio_for_questions", lambda _q: [])

    application = initialize_backend_application()

    class _Principal:
        id = 1
        resume_text = None
        years_experience = 2.0
        skills = {"items": []}
        target_position = JOB_NAME

    application.dependency_overrides[get_current_user] = lambda: _Principal()
    return application


async def _empty_map():
    return {}


async def _generate(app, profile_id: int):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://probe"
    ) as client:
        return await client.post(
            NON_TECH_PATH,
            json={"jobProfileId": profile_id, "difficulty": "medium", "useResume": False},
        )


def _texts(response) -> list[str]:
    return [item["text"] for item in response.json()["items"]]


def _is_facilitators(texts: list[str]) -> bool:
    return all(text.startswith("Facilitator level-") for text in texts)


async def _recorded_difficulty(database, interview_id: int) -> str:
    sessionmaker = async_sessionmaker(bind=database, expire_on_commit=False)
    async with sessionmaker() as session:
        interview = await session.get(Interview, interview_id)
        return interview.difficulty


# --------------------------------------------------------------------------
# The fix
# --------------------------------------------------------------------------


@pytest.mark.parametrize("level", [1, 3, 4], ids=["easy-only", "hard-only", "expert-only"])
async def test_facilitator_questions_are_served_from_any_level(app, seed, database, level):
    """The defect: only level 2 was ever looked at, so these served nobody."""
    profile_id = await seed({level: MINIMUM_QUESTIONS_PER_LEVEL})

    response = await _generate(app, profile_id)

    assert response.status_code == 201, response.text
    texts = _texts(response)
    assert _is_facilitators(texts), (
        f"Level {level} questions were published but the blueprint was served instead: {texts}"
    )
    assert all(f"level-{level}" in text for text in texts), texts


async def test_the_recorded_difficulty_matches_what_was_served(app, seed, database):
    """The interview must not claim medium while serving easy questions.

    History, reports and the retake counter all read this column, so recording
    a level the student never sat would mislabel their result.
    """
    profile_id = await seed({1: MINIMUM_QUESTIONS_PER_LEVEL})

    response = await _generate(app, profile_id)
    interview_id = response.json()["interviewId"]

    assert await _recorded_difficulty(database, interview_id) == "easy"


async def test_the_configured_difficulty_wins_when_published(app, seed, database):
    """With several levels available, prefer the flow's own fixed difficulty."""
    profile_id = await seed(
        {
            1: MINIMUM_QUESTIONS_PER_LEVEL,
            2: MINIMUM_QUESTIONS_PER_LEVEL,
            3: MINIMUM_QUESTIONS_PER_LEVEL,
        }
    )

    response = await _generate(app, profile_id)
    interview_id = response.json()["interviewId"]

    assert await _recorded_difficulty(database, interview_id) == NON_TECH_FIXED_DIFFICULTY
    assert all("level-2" in text for text in _texts(response))


async def test_curated_and_blueprint_questions_are_never_mixed(app, seed, database):
    """A sitting is either the facilitator's set or the blueprint's, not a blend."""
    profile_id = await seed({1: MINIMUM_QUESTIONS_PER_LEVEL})

    texts = _texts(await _generate(app, profile_id))

    curated = [text for text in texts if text.startswith("Facilitator level-")]
    assert len(curated) == len(texts), f"Blended curated and blueprint questions: {texts}"


# --------------------------------------------------------------------------
# No regression for roles that get the blueprint today
# --------------------------------------------------------------------------


async def test_a_role_with_no_questions_still_gets_the_blueprint(app, seed, database):
    """This endpoint creates the interview, so refusing would block the student."""
    profile_id = await seed({})

    response = await _generate(app, profile_id)

    assert response.status_code == 201, response.text
    texts = _texts(response)
    assert texts, "A role with no curated questions got no interview at all"
    assert not _is_facilitators(texts)


async def test_a_role_with_too_few_questions_still_gets_the_blueprint(app, seed, database):
    """Below the minimum, behave exactly as before rather than serving short."""
    profile_id = await seed({1: MINIMUM_QUESTIONS_PER_LEVEL - 1})

    response = await _generate(app, profile_id)

    assert response.status_code == 201, response.text
    texts = _texts(response)
    assert texts
    assert not _is_facilitators(texts), (
        "A level below the minimum was served anyway, as a short interview"
    )


async def test_the_full_sitting_length_is_unchanged(app, seed, database):
    """Curated and blueprint sittings must be the same length to compare."""
    curated = _texts(await _generate(app, await seed({1: MINIMUM_QUESTIONS_PER_LEVEL})))
    blueprint = _texts(await _generate(app, await seed({})))

    assert len(curated) == len(blueprint), (
        f"Curated sitting has {len(curated)} questions, blueprint has {len(blueprint)}"
    )
