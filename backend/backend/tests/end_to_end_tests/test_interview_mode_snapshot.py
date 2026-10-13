"""An interview's mode is a snapshot, not a live read of the job profile.

The whole reason this column exists on Interview rather than being read off
JobProfile at display time: job_profile_id goes NULL if the profile is later
deleted (ON DELETE SET NULL), and nothing stops a facilitator editing a
published profile's mode at any time. Without a snapshot, either event would
retroactively reclassify every interview already sat against that role - in
a student's history, and in every facilitator-facing analytics surface - after
the fact. `test_changing_the_profiles_mode_does_not_touch_past_interviews` is
the test that would fail if that guarantee were ever accidentally reintroduced
as a live join instead of a copy.

Set SCHEMA_TEST_DATABASE_URL to a throwaway database to enable.
"""

import os

import pytest
import sqlalchemy
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import src.models.db  # noqa: F401  # registers every model on Base.metadata
from src.models.db.job_profile import JobProfile, JobProfileStatus
from src.repository.table import Base

_RAW_URL = os.environ.get("SCHEMA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="Set SCHEMA_TEST_DATABASE_URL to a throwaway PostgreSQL database to run",
)


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
async def interview_repo(database):
    from src.repository.crud.interview import InterviewCRUDRepository
    from src.repository.database import async_db

    session = async_db.get_session()
    try:
        yield InterviewCRUDRepository(async_session=session)
    finally:
        await session.close()


@pytest.fixture
async def job_profile_repo(database):
    from src.repository.crud.job_profile import JobProfileCRUDRepository
    from src.repository.database import async_db

    session = async_db.get_session()
    try:
        yield JobProfileCRUDRepository(async_session=session)
    finally:
        await session.close()


async def _seed_user(database) -> int:
    from src.models.db.user import User

    sessionmaker = async_sessionmaker(bind=database, expire_on_commit=False)
    async with sessionmaker() as session:
        user = User(email="snapshot@example.test", password_hash="not-a-real-hash", name="Snapshot Probe")
        session.add(user)
        await session.commit()
        return user.id


async def test_free_practice_interview_is_practice(database, interview_repo):
    """No job profile at all: free generation is practice by definition."""
    user_id = await _seed_user(database)

    interview = await interview_repo.create_interview(user_id=user_id, track="Backend", difficulty="easy")

    assert interview.interview_mode == "practice"


async def test_curated_interview_inherits_the_profiles_mode_at_creation(
    database, interview_repo, job_profile_repo
):
    user_id = await _seed_user(database)
    profile = await job_profile_repo.create_profile(job_name="QA Engineer", interview_mode="assessment")

    interview = await interview_repo.create_interview(
        user_id=user_id, track="QA Engineer", difficulty="easy", job_profile_id=profile.id
    )

    assert interview.interview_mode == "assessment"


async def test_changing_the_profiles_mode_does_not_touch_past_interviews(
    database, interview_repo, job_profile_repo
):
    """The guarantee this column exists for.

    If this ever regresses to a live join against JobProfile.interview_mode
    instead of a stored copy, this is the test that catches it: a facilitator
    flipping a role's mode after students have already sat it must not
    reclassify what they already did.
    """
    user_id = await _seed_user(database)
    profile = await job_profile_repo.create_profile(job_name="QA Engineer", interview_mode="practice")

    interview = await interview_repo.create_interview(
        user_id=user_id, track="QA Engineer", difficulty="easy", job_profile_id=profile.id
    )
    assert interview.interview_mode == "practice"

    await job_profile_repo.update_profile(profile.id, {"interview_mode": "assessment"})

    reloaded = await interview_repo.get_active_by_user(user_id=user_id)
    assert reloaded.interview_mode == "practice", (
        "Changing the job profile's mode retroactively reclassified an interview "
        "that was already sat under the old mode"
    )


async def test_deleting_the_profile_does_not_touch_the_interviews_mode(
    database, interview_repo, job_profile_repo
):
    """job_profile_id goes NULL on delete (ON DELETE SET NULL); mode must not."""
    user_id = await _seed_user(database)
    profile = await job_profile_repo.create_profile(job_name="QA Engineer", interview_mode="assessment")
    interview = await interview_repo.create_interview(
        user_id=user_id, track="QA Engineer", difficulty="easy", job_profile_id=profile.id
    )
    interview_id = interview.id

    sessionmaker = async_sessionmaker(bind=database, expire_on_commit=False)
    async with sessionmaker() as session:
        await session.execute(sqlalchemy.delete(JobProfile).where(JobProfile.id == profile.id))
        await session.commit()

    from src.models.db.interview import Interview

    async with sessionmaker() as session:
        reloaded = await session.get(Interview, interview_id)
        assert reloaded.job_profile_id is None, "Expected ON DELETE SET NULL to have fired"
        assert reloaded.interview_mode == "assessment"


# A job_profile_id pointing at an already-deleted profile cannot reach
# create_interview's mode lookup at all: interview.job_profile_id carries a
# foreign key, so Postgres rejects that INSERT before any application code
# runs. The defensive "profile not found -> practice" branch in
# create_interview is dead code for that race specifically - it is not tested
# here because the state it guards against cannot occur through this column.


async def test_migration_backfills_a_curated_row_to_assessment(database):
    """Mirrors the migration's UPDATE: a pre-existing row with a job profile
    was, in effect, always an assessment - there was no other option."""
    sessionmaker = async_sessionmaker(bind=database, expire_on_commit=False)
    async with sessionmaker() as session:
        profile = JobProfile(job_name="Legacy Role", job_description="d", status=JobProfileStatus.PUBLISHED)
        session.add(profile)
        await session.commit()
        profile_id = profile.id

    user_id = await _seed_user(database)
    async with sessionmaker() as session:
        # Plain INSERT with no interview_mode, exactly like a row written
        # before this column existed: it lands on the column's own default.
        await session.execute(
            sqlalchemy.text(
                "INSERT INTO interview (user_id, track, difficulty, status, job_profile_id) "
                "VALUES (:user_id, 'Legacy', 'easy', 'completed', :profile_id)"
            ),
            {"user_id": user_id, "profile_id": profile_id},
        )
        # The migration's own backfill statement, run against this row exactly
        # as it runs against production: default lands "practice", then this
        # corrects every row that actually has a job profile.
        await session.execute(
            sqlalchemy.text(
                "UPDATE interview SET interview_mode = 'assessment' WHERE job_profile_id IS NOT NULL"
            )
        )
        await session.commit()

        from src.models.db.interview import Interview

        result = await session.execute(
            sqlalchemy.select(Interview).where(Interview.job_profile_id == profile_id)
        )
        legacy_interview = result.scalar_one()

    assert legacy_interview.interview_mode == "assessment"
