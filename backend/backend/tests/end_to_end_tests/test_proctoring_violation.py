"""A tab switch or fullscreen exit ends an Assessment interview - never a Practice one.

Mode is read from the interview's own stored snapshot, never trusted from the
request. That matters because the client sends this endpoint exactly the
signal a motivated student would want to fake: "I did not leave the tab."
Reading interview.interview_mode server-side - the same snapshot that already
can't be retroactively changed by editing a job profile (see
test_interview_mode_snapshot.py) - means a client claiming a practice
interview is really an assessment, or the reverse, has no effect either way.

Set SCHEMA_TEST_DATABASE_URL to a throwaway database to enable.
"""

import os

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import src.models.db  # noqa: F401  # registers every model on Base.metadata
from src.api.dependencies.auth import get_current_user
from src.repository.table import Base

_RAW_URL = os.environ.get("SCHEMA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="Set SCHEMA_TEST_DATABASE_URL to a throwaway PostgreSQL database to run",
)

VIOLATION_PATH = "/api/interviews/proctoring-violation"
OTHER_USER_ID = 999999


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
def app(database):
    from src.main import initialize_backend_application

    application = initialize_backend_application()

    class _Principal:
        id = 1

    application.dependency_overrides[get_current_user] = lambda: _Principal()
    return application


async def _create_interview(database, *, interview_mode: str, user_id: int = 1, status: str = "active") -> int:
    from src.repository.crud.interview import InterviewCRUDRepository
    from src.repository.database import async_db
    from src.models.db.user import User

    sessionmaker = async_sessionmaker(bind=database, expire_on_commit=False)
    async with sessionmaker() as session:
        result = await session.execute(sqlalchemy.select(User).where(User.id == user_id))
        if result.scalar_one_or_none() is None:
            session.add(User(id=user_id, email=f"proctor{user_id}@example.test", password_hash="x", name="Proctor Probe"))
            await session.commit()

    # Closed explicitly: an unclosed session leaves a transaction open that
    # the next test's drop_all blocks on - a hang, not a slow test.
    repo_session = async_db.get_session()
    repo = InterviewCRUDRepository(async_session=repo_session)
    # create_interview always derives "practice" with no job profile; set the
    # mode directly afterward since these tests need either value on demand.
    interview = await repo.create_interview(user_id=user_id, track="Backend", difficulty="easy")
    await repo_session.close()
    async with sessionmaker() as session:
        from src.models.db.interview import Interview

        row = await session.get(Interview, interview.id)
        row.interview_mode = interview_mode
        row.status = status
        await session.commit()
    return interview.id


async def _post_violation(app, interview_id: int, reason: str = "tab_switch"):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://probe") as client:
        return await client.post(VIOLATION_PATH, json={"interviewId": interview_id, "reason": reason})


async def test_tab_switch_ends_an_assessment_interview(app, database):
    interview_id = await _create_interview(database, interview_mode="assessment")

    response = await _post_violation(app, interview_id, reason="tab_switch")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "terminated"
    assert body["proctoringTerminated"] is True
    assert body["proctoringViolationReason"] == "tab_switch"


async def test_fullscreen_exit_ends_an_assessment_interview(app, database):
    interview_id = await _create_interview(database, interview_mode="assessment")

    response = await _post_violation(app, interview_id, reason="fullscreen_exit")

    assert response.status_code == 200, response.text
    assert response.json()["proctoringViolationReason"] == "fullscreen_exit"


async def test_a_practice_interview_is_never_terminated(app, database):
    """The decision this whole feature rests on: practice stays low-stakes."""
    interview_id = await _create_interview(database, interview_mode="practice")

    response = await _post_violation(app, interview_id, reason="tab_switch")

    assert response.status_code == 409, response.text

    # Confirm the interview itself was genuinely untouched, not just that the
    # endpoint returned an error while silently terminating anyway.
    from src.models.db.interview import Interview
    from src.repository.database import async_db

    session = async_db.get_session()
    row = await session.get(Interview, interview_id)
    await session.close()
    assert row.status == "active"
    assert row.proctoring_terminated is False


async def test_only_the_owning_student_can_report_a_violation(app, database):
    interview_id = await _create_interview(database, interview_mode="assessment", user_id=OTHER_USER_ID)

    response = await _post_violation(app, interview_id)

    assert response.status_code == 404


async def test_repeating_the_report_is_idempotent(app, database):
    """A flaky network retry, or the client firing both detectors at once,
    must not error and must not overwrite which reason actually fired first."""
    interview_id = await _create_interview(database, interview_mode="assessment")

    first = await _post_violation(app, interview_id, reason="tab_switch")
    second = await _post_violation(app, interview_id, reason="fullscreen_exit")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["proctoringViolationReason"] == "tab_switch", (
        "A repeated report overwrote the original violation reason"
    )


async def test_a_normally_completed_interview_cannot_be_retroactively_terminated(app, database):
    interview_id = await _create_interview(database, interview_mode="assessment", status="completed")

    response = await _post_violation(app, interview_id)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["proctoringTerminated"] is False, (
        "A finished interview was marked as proctoring-terminated after the fact"
    )


async def test_an_interview_that_never_started_cannot_be_terminated(app, database):
    """status defaults to active on create, so emulate an interview that was
    never activated - e.g. created in some other terminal state."""
    interview_id = await _create_interview(database, interview_mode="assessment", status="draft")

    response = await _post_violation(app, interview_id)

    assert response.status_code == 400


async def test_an_unknown_reason_is_rejected(app, database):
    """The remark reads from a fixed set, not free text the client supplies."""
    interview_id = await _create_interview(database, interview_mode="assessment")

    response = await _post_violation(app, interview_id, reason="student_was_cheating_i_swear")

    assert response.status_code == 422
