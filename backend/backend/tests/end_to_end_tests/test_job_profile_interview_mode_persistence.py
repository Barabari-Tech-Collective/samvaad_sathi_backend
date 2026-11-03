"""interview_mode must survive a real create, a real update, and a real migration.

The schema-level tests prove defaulting and validation; this proves the value
actually reaches Postgres and comes back unchanged, including for rows that
existed before the column did - the migration backfills those to "assessment",
and that backfill is the only thing standing between this feature and quietly
reclassifying every role created before today as Practice.

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
async def repo(database):
    from src.repository.crud.job_profile import JobProfileCRUDRepository
    from src.repository.database import async_db

    session = async_db.get_session()
    try:
        yield JobProfileCRUDRepository(async_session=session)
    finally:
        # A read-only query still opens a transaction on asyncpg. Without this,
        # that transaction outlives the test and holds a lock that the next
        # test's `drop_all` blocks on forever - not a slow test, a hang.
        await session.close()


async def test_create_profile_persists_the_requested_mode(repo):
    profile = await repo.create_profile(job_name="QA Engineer", interview_mode="practice")

    assert profile.interview_mode == "practice"

    reloaded = await repo.get_by_id(job_profile_id=profile.id)
    assert reloaded.interview_mode == "practice"


async def test_create_profile_defaults_to_assessment(repo):
    """Mirrors JobProfileCreateV2's default, one layer lower."""
    profile = await repo.create_profile(job_name="QA Engineer")

    assert profile.interview_mode == "assessment"


async def test_update_profile_cannot_repurpose_existing_template(repo):
    profile = await repo.create_profile(job_name="QA Engineer", interview_mode="assessment")

    import fastapi
    with pytest.raises(fastapi.HTTPException) as error:
        await repo.update_profile(profile.id, {"interview_mode": "practice"})
    assert error.value.status_code == 409
    assert (await repo.get_by_id(job_profile_id=profile.id)).interview_mode == "assessment"


async def test_update_profile_omitting_mode_leaves_it_unchanged(repo):
    """The behaviour JobProfileUpdateV2's None default exists to produce."""
    profile = await repo.create_profile(job_name="QA Engineer", interview_mode="practice")

    updated = await repo.update_profile(profile.id, {"job_name": "Senior QA Engineer"})

    assert updated.interview_mode == "practice"


async def test_a_row_inserted_before_the_column_backfills_to_assessment(database):
    """Simulates a pre-migration row: insert with the column's own DB default.

    This is the production migration path for every existing job profile - the
    column lands with server_default='assessment', so a row written before this
    feature shipped reads back as assessment without anyone having to touch it.
    """
    async with database.begin() as connection:
        await connection.execute(
            sqlalchemy.text(
                "INSERT INTO job_profile (job_name, job_description, status) "
                "VALUES ('Legacy Role', 'd', 'published')"
            )
        )

    sessionmaker = async_sessionmaker(bind=database, expire_on_commit=False)
    async with sessionmaker() as session:
        result = await session.execute(
            sqlalchemy.select(JobProfile).where(JobProfile.job_name == "Legacy Role")
        )
        legacy = result.scalar_one()

    assert legacy.interview_mode == "assessment"


async def test_mode_has_no_effect_on_availability(repo):
    """The behavioural half of the "no integrity change" guarantee.

    Same question counts, same status, different mode - availability must be
    identical. If this diverges, Practice and Assessment stopped enforcing the
    same question integrity.
    """
    from src.models.db.job_profile_question import JobProfileQuestion
    from src.repository.database import async_db
    from src.services.interview_eligibility import (
        MINIMUM_QUESTIONS_PER_LEVEL,
        available_difficulties,
    )

    practice = await repo.create_profile(job_name="Role A", interview_mode="practice")
    assessment = await repo.create_profile(job_name="Role B", interview_mode="assessment")
    # Captured before expiring below - an expired object needs a sync reload
    # to read even its id, which is not valid under an async engine.
    practice_id, assessment_id = practice.id, assessment.id

    session = async_db.get_session()
    for profile_id in (practice_id, assessment_id):
        await repo.update_review_status(
            profile_id=profile_id, status=JobProfileStatus.PUBLISHED.value, admin_comment=None
        )
        for index in range(MINIMUM_QUESTIONS_PER_LEVEL):
            session.add(
                JobProfileQuestion(
                    job_profile_id=profile_id,
                    level=1,
                    difficulty="easy",
                    question_text=f"Q{index}",
                )
            )
    await session.commit()
    await session.close()

    # The questions were committed through a different Session. repo's own
    # Session still holds practice/assessment in its identity map with the
    # empty `questions` it eager-loaded at create time, and get_by_id's
    # selectinload does not re-run for an object the identity map already
    # satisfies - so without expiring first, this would read stale, empty
    # question lists regardless of what was just committed.
    repo.async_session.expire_all()

    practice = await repo.get_by_id(job_profile_id=practice_id)
    assessment = await repo.get_by_id(job_profile_id=assessment_id)

    assert available_difficulties(practice) == available_difficulties(assessment) == ["easy"]
