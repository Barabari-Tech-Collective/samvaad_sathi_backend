"""A learner chooses their own course and batch from what the program team opened.

Runs against a real PostgreSQL database because the rules lean on it: one active batch per course
is enforced by a partial unique index, and group membership and the learner's course list must
change together in one commit. Set SCHEMA_TEST_DATABASE_URL (CI always does).
"""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import fastapi
import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import src.models.db  # noqa: F401  # registers every model on Base.metadata
from src.models.db.cohort import Cohort, CohortMember
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.student_placement import StudentPlacement
from src.models.db.user import User
from src.repository.table import Base
from src.services import student_placement as service

DATABASE_URL = os.environ.get("SCHEMA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="Set SCHEMA_TEST_DATABASE_URL to a throwaway PostgreSQL database to run"
)


def batch(key, course, name="Batch 2026", course_name=None):
    return service.OpenBatch(
        batchUniqueId=key, batchId=f"id-{key}", batchName=name, courseId=course,
        courseName=course_name or f"Course {course}",
    )


FULL_STACK_2026 = batch("fs-2026", 7, "Batch 2026", "Full Stack Development")
FULL_STACK_2025 = batch("fs-2025", 7, "Batch 2025", "Full Stack Development")
DATA_2025 = batch("da-2025", 8, "Batch 2025", "Data Analytics")


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
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def make_user(factory, email="s@example.org", **fields):
    async with factory() as session:
        user = User(email=email, name="Learner", password_hash="x", **fields)
        session.add(user)
        await session.commit()
        return user.id


def opened(*items):
    return patch.object(service, "open_batches", AsyncMock(return_value=(list(items), False)))


async def choose(factory, user_id, *keys):
    async with factory() as session:
        return await service.replace(session, SimpleNamespace(id=user_id), list(keys))


async def load_user(factory, user_id):
    async with factory() as session:
        return await session.get(User, user_id)


async def active(factory, user_id):
    async with factory() as session:
        return (
            await session.execute(
                sa.select(StudentPlacement).where(
                    StudentPlacement.student_id == user_id, StudentPlacement.ended_at.is_(None)
                )
            )
        ).scalars().all()


async def members(factory, user_id):
    async with factory() as session:
        return (
            await session.execute(
                sa.select(Cohort.course_id, Cohort.name, Cohort.request_key)
                .join(CohortMember, CohortMember.cohort_id == Cohort.id)
                .where(CohortMember.student_id == user_id)
                .order_by(Cohort.course_id)
            )
        ).all()


@pytest.mark.asyncio
async def test_choosing_sets_the_course_joins_the_batch_group_and_is_logged(db):
    uid = await make_user(db)
    with opened(FULL_STACK_2026, DATA_2025):
        result = await choose(db, uid, "fs-2026", "da-2025")
    assert [s["label"] for s in result["selected"]] == [
        "Full Stack Development · Batch 2026", "Data Analytics · Batch 2025"
    ]
    assert result["needs_placement"] is False
    user = await load_user(db, uid)
    assert user.enrolled_course_ids == ["7", "8"]
    assert user.enrollment_source == "self"
    assert [(c, n, k) for c, n, k in await members(db, uid)] == [
        ("7", "Batch 2026", "sampark:fs-2026"), ("8", "Batch 2025", "sampark:da-2025")
    ]
    async with db() as session:
        events = (await session.execute(sa.select(PracticeDashboardAuditEvent))).scalars().all()
    assert [e.action for e in events] == ["student_placement_changed"]
    assert events[0].actor_id == uid and len(events[0].event_metadata["added"]) == 2


@pytest.mark.asyncio
async def test_two_students_in_one_batch_share_one_group(db):
    a, b = await make_user(db, "a@example.org"), await make_user(db, "b@example.org")
    with opened(FULL_STACK_2026):
        await choose(db, a, "fs-2026")
        await choose(db, b, "fs-2026")
    async with db() as session:
        groups = (await session.execute(sa.select(Cohort))).scalars().all()
        count = (await session.execute(sa.select(sa.func.count(CohortMember.id)))).scalar_one()
    assert len(groups) == 1 and count == 2


@pytest.mark.asyncio
async def test_only_one_batch_for_each_course(db):
    uid = await make_user(db)
    with opened(FULL_STACK_2026, FULL_STACK_2025):
        with pytest.raises(fastapi.HTTPException) as err:
            await choose(db, uid, "fs-2026", "fs-2025")
    assert err.value.status_code == 422 and "one batch for each course" in err.value.detail
    assert await active(db, uid) == []
    assert (await load_user(db, uid)).enrolled_course_ids is None


@pytest.mark.asyncio
async def test_changing_a_choice_keeps_the_history_and_moves_the_group(db):
    uid = await make_user(db)
    with opened(FULL_STACK_2026, FULL_STACK_2025):
        await choose(db, uid, "fs-2025")
        await choose(db, uid, "fs-2026")  # same course, different batch
    rows = await active(db, uid)
    assert [r.batch_key for r in rows] == ["fs-2026"]
    async with db() as session:
        ended = (
            await session.execute(sa.select(StudentPlacement).where(StudentPlacement.ended_at.is_not(None)))
        ).scalars().all()
    assert [r.batch_key for r in ended] == ["fs-2025"]
    assert [(c, k) for c, _, k in await members(db, uid)] == [("7", "sampark:fs-2026")]
    assert (await load_user(db, uid)).enrolled_course_ids == ["7"]
    with opened(FULL_STACK_2026, FULL_STACK_2025):
        await choose(db, uid, "fs-2025")  # going back is allowed, history grows
    async with db() as session:
        total = (await session.execute(sa.select(sa.func.count(StudentPlacement.id)))).scalar_one()
    assert total == 3


@pytest.mark.asyncio
async def test_a_batch_that_is_not_open_cannot_be_chosen_but_a_held_one_can_stay(db):
    uid = await make_user(db)
    with opened(FULL_STACK_2026):
        await choose(db, uid, "fs-2026")
    with opened(DATA_2025):  # the program team closed Full Stack 2026 afterwards
        with pytest.raises(fastapi.HTTPException) as err:
            await choose(db, uid, "fs-2026", "fs-2025")
        assert err.value.status_code == 422
        result = await choose(db, uid, "fs-2026", "da-2025")  # keeps what they hold, adds an open one
    assert sorted(s["batch_key"] for s in result["selected"]) == ["da-2025", "fs-2026"]
    assert (await load_user(db, uid)).enrolled_course_ids == ["7", "8"]


@pytest.mark.asyncio
async def test_keeping_the_same_choice_needs_no_call_to_sampark(db):
    uid = await make_user(db)
    with opened(FULL_STACK_2026):
        await choose(db, uid, "fs-2026")
    down = AsyncMock(side_effect=fastapi.HTTPException(503, service.UNAVAILABLE))
    with patch.object(service, "open_batches", down):
        result = await choose(db, uid, "fs-2026")
    assert [s["batch_key"] for s in result["selected"]] == ["fs-2026"]
    down.assert_not_called()


@pytest.mark.asyncio
async def test_a_course_an_administrator_gave_is_left_alone(db):
    uid = await make_user(db, enrolled_course_ids=["99"])
    with opened(FULL_STACK_2026, DATA_2025):
        await choose(db, uid, "fs-2026")
        assert (await load_user(db, uid)).enrolled_course_ids == ["7", "99"]
        await choose(db, uid, "da-2025")  # swaps course 7 for 8
    assert (await load_user(db, uid)).enrolled_course_ids == ["8", "99"]


@pytest.mark.asyncio
async def test_courses_set_by_the_sampark_sync_are_not_overwritten(db):
    uid = await make_user(db, enrolled_course_ids=["7"], enrollment_source="sampark")
    with opened(DATA_2025):
        with pytest.raises(fastapi.HTTPException) as err:
            await choose(db, uid, "da-2025")
    assert err.value.status_code == 409
    assert (await load_user(db, uid)).enrolled_course_ids == ["7"]


@pytest.mark.asyncio
async def test_staff_have_no_course_to_choose(db):
    admin = await make_user(db, "boss@example.org", is_admin=True)
    with opened(FULL_STACK_2026):
        with pytest.raises(fastapi.HTTPException) as err:
            await choose(db, admin, "fs-2026")
    assert err.value.status_code == 403


@pytest.mark.asyncio
async def test_nothing_chosen_or_too_many_is_refused(db):
    uid = await make_user(db)
    with pytest.raises(fastapi.HTTPException) as none:
        await choose(db, uid)
    assert none.value.status_code == 422
    with pytest.raises(fastapi.HTTPException) as many:
        await choose(db, uid, *[f"k{i}" for i in range(service.MAX_CHOICES + 1)])
    assert many.value.status_code == 422


@pytest.mark.asyncio
async def test_overview_says_whether_the_learner_still_needs_to_choose(db):
    fresh = await make_user(db, "new@example.org", university="City College")
    given = await make_user(db, "given@example.org", enrolled_course_ids=["99"])
    async with db() as session:
        first = await service.overview(session, SimpleNamespace(id=fresh))
        second = await service.overview(session, SimpleNamespace(id=given))
    assert first == {"needs_placement": True, "college": "City College", "selected": []}
    assert second["needs_placement"] is False  # placed another way: ask nothing more
    with opened(FULL_STACK_2026):
        await choose(db, fresh, "fs-2026")
    async with db() as session:
        assert (await service.overview(session, SimpleNamespace(id=fresh)))["needs_placement"] is False


# --- reading the list from Sampark -------------------------------------------------------------


def sampark(handler):
    return httpx.MockTransport(handler)


def listen(monkeypatch, caplog):
    """Capture this module's log lines even if an earlier test reconfigured logging."""
    import logging

    monkeypatch.setattr(service.logger, "disabled", False)
    monkeypatch.setattr(service.logger, "propagate", True)
    caplog.set_level(logging.WARNING, logger=service.logger.name)


@pytest.fixture
def connected(monkeypatch):
    monkeypatch.setattr(service.settings, "SAMPARK_SAATHI_BASE_URL", "https://sampark.test/api", raising=False)
    monkeypatch.setattr(service.settings, "SAMPARK_SAATHI_API_KEY", "service-key", raising=False)
    service.reset_cache()
    yield
    service.reset_cache()


GOOD = {"isSuccess": True, "data": [
    {"batchUniqueId": "fs-2026", "batchId": "FS26", "batchName": "Batch 2026", "courseId": 7,
     "courseUniqueId": "c7", "courseName": "Full Stack Development"},
    {"batchUniqueId": "bad", "batchId": "X"},  # missing fields: hidden, never breaks the list
]}


@pytest.mark.asyncio
async def test_open_batches_are_read_with_the_service_key_and_bad_rows_are_skipped(connected):
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("SAMVAAD-SAATHI-API-KEY")))
        return httpx.Response(200, json=GOOD)

    items, stale = await service.open_batches(transport=sampark(handler))
    assert [i.key for i in items] == ["fs-2026"] and stale is False
    assert seen == [("https://sampark.test/api/samvaad-saathi/v1/open-batches", "service-key")]


@pytest.mark.asyncio
async def test_the_list_is_cached_briefly_and_the_last_good_copy_survives_an_outage(connected):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=GOOD)

    await service.open_batches(transport=sampark(handler))
    await service.open_batches(transport=sampark(handler))
    assert len(calls) == 1  # second call came from the cache
    service._cache["at"] -= service.CACHE_SECONDS + 1  # expire it
    down = sampark(lambda request: httpx.Response(500))
    items, stale = await service.open_batches(transport=down)
    assert [i.key for i in items] == ["fs-2026"] and stale is True


@pytest.mark.asyncio
async def test_without_any_good_copy_an_outage_is_a_plain_503(connected):
    with pytest.raises(fastapi.HTTPException) as err:
        await service.open_batches(transport=sampark(lambda request: httpx.Response(500)))
    assert err.value.status_code == 503 and err.value.detail == service.UNAVAILABLE
    service.reset_cache()
    with pytest.raises(fastapi.HTTPException) as refused:
        await service.open_batches(transport=sampark(lambda request: httpx.Response(200, json={"isSuccess": False})))
    assert refused.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, expected",
    [
        (401, "service key was refused"),
        (404, "no open-batches route"),
        (500, "migration has not been run"),
    ],
)
async def test_the_log_says_why_sampark_could_not_be_read(connected, caplog, monkeypatch, status, expected):
    import logging

    listen(monkeypatch, caplog)
    with pytest.raises(fastapi.HTTPException):
        await service.open_batches(transport=sampark(lambda request: httpx.Response(status)))
    assert expected in caplog.text
    assert "service-key" not in caplog.text  # the key itself is never logged


@pytest.mark.asyncio
async def test_a_timeout_and_a_missing_setting_are_described_too(connected, monkeypatch, caplog):
    import logging

    listen(monkeypatch, caplog)

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(fastapi.HTTPException):
        await service.open_batches(transport=sampark(slow))
    assert "did not answer in time" in caplog.text
    service.reset_cache()
    monkeypatch.setattr(service.settings, "SAMPARK_SAATHI_BASE_URL", "", raising=False)
    caplog.clear()
    with pytest.raises(fastapi.HTTPException):
        await service.open_batches()
    assert "not configured" in caplog.text


@pytest.mark.asyncio
async def test_an_unconfigured_connection_is_a_503_not_an_empty_list(monkeypatch):
    monkeypatch.setattr(service.settings, "SAMPARK_SAATHI_BASE_URL", "", raising=False)
    service.reset_cache()
    with pytest.raises(fastapi.HTTPException) as err:
        await service.open_batches()
    assert err.value.status_code == 503


# --- telling Sampark -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_new_choice_is_queued_for_sampark_after_it_is_saved(db):
    uid = await make_user(db, "asha@example.org")
    queued = AsyncMock(return_value="job-1")
    with opened(FULL_STACK_2026, DATA_2025), patch("src.worker.queue.enqueue_job", queued):
        await choose(db, uid, "fs-2026", "da-2025")
        await choose(db, uid, "fs-2026", "da-2025")  # nothing new: nothing more to say
    sent = [(c.args[0], c.kwargs["email"], c.kwargs["batch_key"]) for c in queued.await_args_list]
    assert sent == [
        ("submit_batch_choice_task", "asha@example.org", "fs-2026"),
        ("submit_batch_choice_task", "asha@example.org", "da-2025"),
    ]


@pytest.mark.asyncio
async def test_a_queue_outage_never_undoes_a_saved_choice(db):
    uid = await make_user(db, "asha@example.org")
    for broken in (AsyncMock(return_value=None), AsyncMock(side_effect=RuntimeError("redis down"))):
        with opened(FULL_STACK_2026), patch("src.worker.queue.enqueue_job", broken):
            result = await choose(db, uid, "fs-2026")
        assert [s["batch_key"] for s in result["selected"]] == ["fs-2026"]
        assert (await load_user(db, uid)).enrolled_course_ids == ["7"]
        async with db() as session:  # reset for the second round
            await session.execute(sa.delete(StudentPlacement))
            await session.execute(sa.update(User).values(enrolled_course_ids=None, enrollment_source=None))
            await session.commit()


@pytest.mark.asyncio
async def test_removing_a_choice_tells_sampark_nothing(db):
    uid = await make_user(db, "asha@example.org")
    with opened(FULL_STACK_2026, DATA_2025), patch("src.worker.queue.enqueue_job", AsyncMock(return_value="j")):
        await choose(db, uid, "fs-2026", "da-2025")
    queued = AsyncMock(return_value="j")
    with opened(FULL_STACK_2026, DATA_2025), patch("src.worker.queue.enqueue_job", queued):
        await choose(db, uid, "fs-2026")  # drops Data Analytics
    queued.assert_not_called()


@pytest.mark.asyncio
async def test_the_callback_posts_email_and_batch_with_the_service_key(connected):
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("SAMVAAD-SAATHI-API-KEY"), request.content))
        return httpx.Response(200, json={"isSuccess": True, "data": {"recorded": False, "reason": "not_in_sampark"}})

    from src.services import barabari_integration as integration

    real = httpx.AsyncClient
    with patch.object(integration.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)):
        await integration.submit_batch_choice_to_sampark("asha@example.org", "fs-2026", "req-1")
    url, key, body = seen[0]
    assert url == "https://sampark.test/api/samvaad-saathi/v1/batch-choice"
    assert key == "service-key"
    assert b'"email":"asha@example.org"' in body.replace(b" ", b"") and b'"batchKey":"fs-2026"' in body.replace(b" ", b"")


@pytest.mark.asyncio
async def test_a_failed_callback_raises_so_it_is_retried_and_an_unconfigured_one_is_skipped(connected, monkeypatch):
    from src.services import barabari_integration as integration

    real = httpx.AsyncClient
    down = httpx.MockTransport(lambda request: httpx.Response(503))
    with patch.object(integration.httpx, "AsyncClient", lambda **kw: real(transport=down, **kw)):
        with pytest.raises(httpx.HTTPStatusError):
            await integration.submit_batch_choice_to_sampark("asha@example.org", "fs-2026", "req-2")
    monkeypatch.setattr(integration.settings, "SAMPARK_SAATHI_BASE_URL", "", raising=False)
    await integration.submit_batch_choice_to_sampark("asha@example.org", "fs-2026", "req-3")  # no error


@pytest.mark.asyncio
async def test_the_worker_turns_a_failure_into_a_delayed_retry():
    from arq.worker import Retry

    from src.worker import tasks

    with patch.object(tasks, "submit_batch_choice_to_sampark", AsyncMock(side_effect=RuntimeError("down"))):
        with pytest.raises(Retry):
            await tasks.submit_batch_choice_task({"job_try": 2}, email="a@b.org", batch_key="k", request_id="r")


def test_the_worker_registers_the_task():
    from src.worker import main

    names = {getattr(f, "name", getattr(f, "__name__", "")) for f in main.WorkerSettings.functions}
    assert "submit_batch_choice_task" in names


# --- the rest of the system respects a self-declared course ------------------------------------


def test_a_self_chosen_course_does_not_age_out_when_the_sampark_sync_is_on():
    from src.services import sampark_enrollment_sync as sync

    with patch.object(sync, "enabled", return_value=True):
        assert sync.fresh(SimpleNamespace(enrollment_source="self", enrollment_observed_at=None)) is True
        assert sync.fresh(SimpleNamespace(enrollment_source=None, enrollment_observed_at=None)) is False
        sql = str(sync.freshness_predicate().compile(compile_kwargs={"literal_binds": True}))
    assert "enrollment_source = 'self'" in sql


@pytest.mark.asyncio
async def test_staff_cannot_hand_edit_a_group_that_follows_learner_choices():
    from src.api.routes import cohorts

    system_group = SimpleNamespace(request_key="sampark:fs-2026", archived_at=None, version=1, id=5)
    body = cohorts.Members(expected_version=1, add=[1], remove=[], reason="Moving a learner by hand")
    with patch.object(cohorts, "scope", AsyncMock(return_value=sa.true())), patch.object(
        cohorts, "load", AsyncMock(return_value=system_group)
    ):
        with pytest.raises(fastapi.HTTPException) as err:
            await cohorts.change_members("7", 5, body, user=SimpleNamespace(id=1), session=AsyncMock())
    assert err.value.status_code == 409 and "follows what learners chose" in err.value.detail
