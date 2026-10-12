"""Concurrent generate-questions requests must agree, through the real app.

The unit tests prove the response builder cannot mix text across requests, and
the integration tests prove the advisory lock really excludes. Neither proves
the endpoints wire them together: a route could take the lock and still build
its reply from the wrong place, or take it after reading whether questions
already exist, which is the same race one line later.

So this drives the actual FastAPI app over ASGI against a real database and
fires overlapping requests for one interview.

It deliberately exercises `/api/interviews/generate-questions` (v1) and the mix
of v1 and v2, because those were the unlocked paths. Pointing this at v2 alone
proves very little: v2 already held a lock before the fix, so a v2-only test
passes either way. The v1 and mixed cases are the ones that fail on the old
code, with two full question sets persisted for one interview.

The stubbed generators return different text on every call on purpose, so a
crossover shows up as a plain mismatch rather than as a flake.

Set SCHEMA_TEST_DATABASE_URL to a throwaway database to enable.
"""

import asyncio
import os

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import src.models.db  # noqa: F401  # registers every model on Base.metadata
from src.api.dependencies.auth import get_current_user
from src.models.db.interview import Interview
from src.models.db.interview_question import InterviewQuestion
from src.models.db.user import User
from src.repository.table import Base

_RAW_URL = os.environ.get("SCHEMA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="Set SCHEMA_TEST_DATABASE_URL to a throwaway PostgreSQL database to run",
)

V1_PATH = "/api/interviews/generate-questions"
V2_PATH = "/api/v2/interviews/generate-questions"
#: Any track other than FULL_STACK_ROLE: that one short-circuits to a static
#: "add your resume" reply on medium difficulty, which never reaches the lock.
TRACK = "Data Science"
#: Not "easy": v1 serves easy difficulty from a static bank without an LLM call.
DIFFICULTY = "hard"
EXPECTED_QUESTION_COUNT = 5


def _async_url(url: str) -> str:
    """CI hands us a psycopg2 URL; the application is async."""
    return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.fixture
async def database():
    """A schema built from the models, and `async_db` repointed at it.

    Built with create_all rather than Alembic deliberately: this test is about
    concurrent request behaviour and should not fail for migration reasons that
    `test_migrations_build_the_model_schema` already covers.
    """
    from src.repository.database import async_db

    engine = create_async_engine(_async_url(_RAW_URL))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    original_factory = async_db.async_session_factory
    # Each request must get its own connection, or there is no concurrency to
    # test and the advisory lock would be trivially re-entrant.
    async_db.async_session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield engine
    finally:
        async_db.async_session_factory = original_factory
        await engine.dispose()


@pytest.fixture
async def interview_id(database) -> int:
    """One user with one active interview, ready to have questions generated."""
    sessionmaker = async_sessionmaker(bind=database, expire_on_commit=False)
    async with sessionmaker() as session:
        user = User(
            email="concurrency@example.test",
            password_hash="not-a-real-hash",
            name="Concurrency Probe",
        )
        session.add(user)
        await session.flush()

        interview = Interview(
            user_id=user.id, track=TRACK, difficulty=DIFFICULTY, status="active"
        )
        session.add(interview)
        await session.commit()
        session.info["user_id"] = user.id
        return interview.id


@pytest.fixture
def app(database, interview_id, monkeypatch):
    """The real app, with the LLM and media side effects stubbed out."""
    from src.api.routes import interviews, interviews_v2
    from src.main import initialize_backend_application

    calls = {"n": 0}

    async def fake_generate(*_args, **_kwargs):
        """Distinct questions per call, slow enough that the requests overlap.

        If generation is serialized, only one call ever happens. If it is not,
        the callers hold different text and any crossover becomes visible.
        """
        calls["n"] += 1
        generation = calls["n"]
        await asyncio.sleep(0.25)
        items = [
            {
                "text": f"Generation {generation} question {i}",
                "topic": f"topic-{i}",
                "category": "tech",
            }
            for i in range(1, EXPECTED_QUESTION_COUNT + 1)
        ]
        return [item["text"] for item in items], None, 10, f"stub-model-{generation}", items

    # v1 and v2 reach different providers; both must be stubbed so neither path
    # makes a real call, and both share the counter so a crossover between
    # endpoints is detectable.
    monkeypatch.setattr(interviews, "generate_interview_questions_with_llm", fake_generate)
    monkeypatch.setattr(
        interviews_v2, "generate_full_stack_questions_with_llm", fake_generate
    )
    # Supplements and text-to-speech both reach external services and are not
    # what this test is about.
    monkeypatch.setattr(interviews_v2, "_get_supplement_map", lambda **_kwargs: _empty_map())
    monkeypatch.setattr(interviews_v2, "_prepare_audio_for_questions", lambda _questions: [])

    application = initialize_backend_application()

    class _Principal:
        id = 1
        resume_text = None
        years_experience = 2.0
        skills = {"items": ["python"]}
        target_position = TRACK

    application.dependency_overrides[get_current_user] = lambda: _Principal()
    application.state.generation_calls = calls
    return application


async def _empty_map():
    return {}


async def _generate(app, path: str, interview_id: int):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://probe"
    ) as client:
        return await client.post(path, json={"interviewId": interview_id, "useResume": False})


async def _stored_questions(engine, interview_id: int) -> dict[int, str]:
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                sqlalchemy.select(InterviewQuestion)
                .where(InterviewQuestion.interview_id == interview_id)
                .order_by(InterviewQuestion.order)
            )
        ).scalars().all()
    return {row.id: row.text for row in rows}


def _assert_matches_database(response, stored: dict[int, str]) -> None:
    """Every question shown must be the question stored under that id.

    This is the assertion the old code failed: text from one request returned
    under IDs persisted by another, so a refresh showed different questions.
    """
    body = response.json()
    for item in body["items"]:
        question_id = item["interviewQuestionId"]
        assert question_id in stored, (
            f"Question id {question_id} was returned to the student but is not in the database"
        )
        assert item["text"] == stored[question_id], (
            f"Question {question_id} was shown as {item['text']!r} "
            f"but is stored as {stored[question_id]!r}"
        )
    # The top-level array must not contradict items[].
    assert body["questions"] == [item["text"] for item in body["items"]]


@pytest.mark.parametrize(
    "paths",
    [
        pytest.param((V1_PATH, V1_PATH), id="v1-twice"),
        pytest.param((V2_PATH, V2_PATH), id="v2-twice"),
        pytest.param((V1_PATH, V2_PATH), id="v1-and-v2"),
    ],
)
async def test_concurrent_requests_yield_one_agreed_question_set(
    app, database, interview_id, paths
):
    first_path, second_path = paths

    first, second = await asyncio.gather(
        _generate(app, first_path, interview_id),
        _generate(app, second_path, interview_id),
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    # One set was persisted, not two interleaved sets. This is what fails on the
    # old code for the v1 and mixed cases: both requests read an empty question
    # table and both insert, leaving ten rows for a five-question interview.
    stored = await _stored_questions(database, interview_id)
    assert len(stored) == EXPECTED_QUESTION_COUNT, (
        f"Expected one set of {EXPECTED_QUESTION_COUNT} questions, found {len(stored)}. "
        "Two requests generated for the same interview."
    )

    # The generator ran once: the second request waited, then reused the first
    # request's questions instead of generating its own.
    assert app.state.generation_calls["n"] == 1

    # Both callers were told the same thing...
    assert [(i["interviewQuestionId"], i["text"]) for i in first.json()["items"]] == [
        (i["interviewQuestionId"], i["text"]) for i in second.json()["items"]
    ], "The two callers were shown different questions for the same interview"

    # ...and what they were told is what the database holds.
    _assert_matches_database(first, stored)
    _assert_matches_database(second, stored)


@pytest.mark.parametrize(
    "path", [pytest.param(V1_PATH, id="v1"), pytest.param(V2_PATH, id="v2")]
)
async def test_a_refresh_returns_exactly_what_was_shown(app, database, interview_id, path):
    """The refresh that made the bug visible to students."""
    original = await _generate(app, path, interview_id)
    refreshed = await _generate(app, path, interview_id)

    assert original.status_code == 201, original.text
    assert refreshed.status_code == 201, refreshed.text
    assert refreshed.json()["cached"] is True

    assert [(i["interviewQuestionId"], i["text"]) for i in original.json()["items"]] == [
        (i["interviewQuestionId"], i["text"]) for i in refreshed.json()["items"]
    ], "A refresh showed different questions than the interview was generated with"

    _assert_matches_database(refreshed, await _stored_questions(database, interview_id))
