"""Templates can be meant for particular courses; learners outside them neither see nor start them.

Runs against a real PostgreSQL database: the learner's catalogue is a JSONB query. Set
SCHEMA_TEST_DATABASE_URL (CI always does).
"""

import os
from types import SimpleNamespace
from unittest.mock import patch

import fastapi
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import src.models.db  # noqa: F401  # registers every model on Base.metadata
from src.api.routes import job_profiles_v2
from src.models.db.assessment_assignment import AssessmentAssignment
from src.models.db.job_profile import JobProfile, JobProfileStatus
from src.models.db.user import User
from src.models.schemas.job_profile import (
    AssessmentRoleUpdate,
    JobProfileCreateV2,
    clean_course_ids,
)
from src.repository.crud.job_profile import JobProfileCRUDRepository
from src.repository.table import Base
from src.services import sampark_enrollment_sync as sync
from src.services import template_scope as scope

DATABASE_URL = os.environ.get("SCHEMA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="Set SCHEMA_TEST_DATABASE_URL to a throwaway PostgreSQL database to run"
)


@pytest_asyncio.fixture
async def db():
    url = DATABASE_URL.replace("+psycopg2", "+asyncpg")
    if "+" not in url.split("://", 1)[0]:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(sa.text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def make_user(factory, email, **fields):
    async with factory() as session:
        user = User(email=email, name="Person", password_hash="x", **fields)
        session.add(user)
        await session.commit()
        return user.id


async def make_template(factory, name, course_ids=None, mode="assessment", status=JobProfileStatus.PUBLISHED):
    async with factory() as session:
        row = JobProfile(
            job_name=name, job_description="A description long enough.", interview_mode=mode,
            status=status, course_ids=course_ids,
        )
        session.add(row)
        await session.commit()
        return row.id


async def learner_catalogue(factory, user_id):
    async with factory() as session:
        repo = JobProfileCRUDRepository(session)
        user = await session.get(User, user_id)
        response = await job_profiles_v2.list_job_profiles(
            category=None, limit=None, is_admin=False, current_user=user, job_profile_repo=repo
        )
        return sorted(item.job_name for item in response.items)


# --- what creators may send ----------------------------------------------------------------------


def test_course_ids_are_cleaned_the_same_way_everywhere():
    assert clean_course_ids([7, " 8 ", "7"]) == ["7", "8"]
    assert clean_course_ids([]) is None  # nothing chosen means everyone
    assert clean_course_ids(None) is None
    for bad in ("7", {"a": 1}, [True], [""], ["x" * 129], [str(i) for i in range(21)], [[1]]):
        with pytest.raises(ValueError):
            clean_course_ids(bad)


def test_create_and_update_payloads_accept_course_ids_in_camel_case():
    created = JobProfileCreateV2(jobName="Backend round", jobDescription="x" * 30, courseIds=[7, 8])
    assert created.course_ids == ["7", "8"]
    cleared = AssessmentRoleUpdate(courseIds=[])
    assert cleared.model_dump(exclude_unset=True)["course_ids"] is None  # sent, so it clears
    assert "course_ids" not in AssessmentRoleUpdate().model_dump(exclude_unset=True)  # not sent: untouched


@pytest.mark.asyncio
async def test_a_draft_can_be_scoped_cleared_and_cloned(db):
    async with db() as session:
        repo = JobProfileCRUDRepository(session)
        draft = await repo.create_profile(job_name="Scoped", job_description="d", course_ids=["7"])
        assert draft.course_ids == ["7"]
        draft = await repo.update_profile(draft.id, {"course_ids": ["7", "8"]})
        assert draft.course_ids == ["7", "8"]
        draft = await repo.update_profile(draft.id, {"course_ids": None})
        assert draft.course_ids is None


@pytest.mark.asyncio
async def test_a_new_version_of_a_template_keeps_its_courses(db):
    original = await make_template(db, "Published for full stack", course_ids=["7", "8"])
    author = await make_user(db, "creator@example.org")
    async with db() as session:
        copy = await JobProfileCRUDRepository(session).clone_profile(
            original, user_id=author, purpose="assessment"
        )
        assert copy.course_ids == ["7", "8"]
        assert copy.status == JobProfileStatus.DRAFT and copy.parent_profile_id == original


# --- who sees what ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_learner_sees_open_templates_and_those_for_their_courses_only(db):
    await make_template(db, "For everyone")
    await make_template(db, "Empty list is everyone", course_ids=[])
    await make_template(db, "Full stack only", course_ids=["7"])
    await make_template(db, "Data only", course_ids=["8"])
    await make_template(db, "Both courses", course_ids=["7", "8"])
    await make_template(db, "Draft for full stack", course_ids=["7"], status=JobProfileStatus.DRAFT)
    full_stack = await make_user(db, "fs@example.org", enrolled_course_ids=["7"], enrollment_source="self")
    nobody = await make_user(db, "none@example.org")
    both = await make_user(db, "both@example.org", enrolled_course_ids=["7", "8"], enrollment_source="self")
    assert await learner_catalogue(db, full_stack) == [
        "Both courses", "Empty list is everyone", "For everyone", "Full stack only"
    ]
    assert await learner_catalogue(db, nobody) == ["Empty list is everyone", "For everyone"]
    assert await learner_catalogue(db, both) == [
        "Both courses", "Data only", "Empty list is everyone", "For everyone", "Full stack only"
    ]


@pytest.mark.asyncio
async def test_staff_are_never_filtered(db):
    await make_template(db, "Data only", course_ids=["8"])
    admin = await make_user(db, "boss@example.org", is_admin=True)
    async with db() as session:
        repo = JobProfileCRUDRepository(session)
        user = await session.get(User, admin)
        listed = await job_profiles_v2.list_job_profiles(
            category=None, limit=None, is_admin=True, current_user=user, job_profile_repo=repo
        )
        assert [i.job_name for i in listed.items] == ["Data only"]
        assert await scope.is_learner(session, user) is False


@pytest.mark.asyncio
async def test_an_assignment_beats_the_course_filter(db):
    template = await make_template(db, "Data only", course_ids=["8"])
    learner = await make_user(db, "fs@example.org", enrolled_course_ids=["7"], enrollment_source="self")
    assert await learner_catalogue(db, learner) == []
    async with db() as session:
        session.add(
            AssessmentAssignment(
                student_id=learner, assigned_by=learner, course_id="7", job_profile_id=template,
                pinned_revision=0, difficulty="easy", opens_at=sa.func.now(), request_key="k1",
            )
        )
        await session.commit()
    assert await learner_catalogue(db, learner) == ["Data only"]
    async with db() as session:
        user, row = await session.get(User, learner), await session.get(JobProfile, template)
        await scope.require_start_allowed(session, user, row)  # no refusal


# --- starting ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_starting_a_template_for_another_course_is_refused_with_a_next_step(db):
    template = await make_template(db, "Data only", course_ids=["8"])
    learner = await make_user(db, "fs@example.org", enrolled_course_ids=["7"], enrollment_source="self")
    async with db() as session:
        user, row = await session.get(User, learner), await session.get(JobProfile, template)
        with pytest.raises(fastapi.HTTPException) as err:
            await scope.require_start_allowed(session, user, row)
    assert err.value.status_code == 403 and "Profile" in err.value.detail


@pytest.mark.asyncio
async def test_starting_inside_the_course_works_and_the_course_tag_must_match(db):
    template = await make_template(db, "Both", course_ids=["7", "8"])
    learner = await make_user(db, "fs@example.org", enrolled_course_ids=["7"], enrollment_source="self")
    async with db() as session:
        user, row = await session.get(User, learner), await session.get(JobProfile, template)
        await scope.require_start_allowed(session, user, row)
        await scope.require_start_allowed(session, user, row, "7")
        with pytest.raises(fastapi.HTTPException) as err:
            await scope.require_start_allowed(session, user, row, "9")
    assert err.value.status_code == 422


@pytest.mark.asyncio
async def test_open_templates_and_staff_are_never_refused(db):
    open_template = await make_template(db, "Open")
    scoped = await make_template(db, "Data only", course_ids=["8"])
    nobody = await make_user(db, "none@example.org")
    admin = await make_user(db, "boss@example.org", is_admin=True)
    async with db() as session:
        learner, boss = await session.get(User, nobody), await session.get(User, admin)
        await scope.require_start_allowed(session, learner, await session.get(JobProfile, open_template))
        await scope.require_start_allowed(session, boss, await session.get(JobProfile, scoped))


@pytest.mark.asyncio
async def test_an_out_of_date_enrollment_counts_as_no_course_when_the_sampark_sync_is_on(db):
    template = await make_template(db, "Full stack only", course_ids=["7"])
    stale = await make_user(db, "old@example.org", enrolled_course_ids=["7"], enrollment_source="sampark")
    chosen = await make_user(db, "own@example.org", enrolled_course_ids=["7"], enrollment_source="self")
    with patch.object(sync, "enabled", return_value=True):
        async with db() as session:
            row = await session.get(JobProfile, template)
            with pytest.raises(fastapi.HTTPException):
                await scope.require_start_allowed(session, await session.get(User, stale), row)
            await scope.require_start_allowed(session, await session.get(User, chosen), row)


# --- assigning --------------------------------------------------------------------------------------


def test_staff_can_only_assign_a_template_inside_its_courses():
    scope.require_assignable(SimpleNamespace(course_ids=None), "7")
    scope.require_assignable(SimpleNamespace(course_ids=[]), "7")
    scope.require_assignable(SimpleNamespace(course_ids=["7", "8"]), "8")
    with pytest.raises(fastapi.HTTPException) as err:
        scope.require_assignable(SimpleNamespace(course_ids=["8"]), "7")
    assert err.value.status_code == 422 and "other courses" in err.value.detail
