"""The Overview work queue: right tasks, right people, nothing pending means nothing shown."""

import datetime as dt
import os

import fastapi
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api.routes.staff_tasks import my_tasks
from src.models.db.human_interview import HumanInterview
from src.models.db.job_profile import JobProfile
from src.models.db.user import User
from src.repository.table import Base

URL = os.environ.get("FIXED_ROUND_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not URL, reason="Requires an explicitly isolated local PostgreSQL test database"
)


@pytest.fixture
async def factory():
    target = sa.engine.make_url(URL)
    if target.host not in ("127.0.0.1", "localhost") or target.database != "ss_count_tests":
        raise RuntimeError("Refusing schema mutation outside the isolated ss_count_tests database")
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _person(email, **kw):
    return User(email=email, name=email.split("@")[0], password_hash="x", **kw)


def _interview(n, student, host, start, **kw):
    return HumanInterview(
        course_id="c1", student_id=student.id, host_id=host.id, title="Round",
        meet_url="https://meet.google.com/abc-defg-hij", starts_at=start,
        ends_at=start + dt.timedelta(minutes=30), request_key=f"00000000-0000-0000-0000-00000000000{n}",
        request_hash=str(n) * 64, **kw,
    )


async def _tasks(session, user):
    return {t["id"]: t for t in (await my_tasks(fastapi.Response(), user, session))["tasks"]}


async def _today(session, user):
    return (await my_tasks(fastapi.Response(), user, session))["today"]


async def test_tasks_follow_ownership_and_omit_empty(factory):
    now = dt.datetime.now(dt.timezone.utc)
    async with factory() as session:
        admin = _person("admin@example.test", is_admin=True)
        host = _person("host@example.test", feature_permissions={"human_interviews": ["read"]})
        other = _person("other@example.test", feature_permissions={"human_interviews": ["read"]})
        nobody = _person("nobody@example.test")
        student = _person("student@example.test")
        session.add_all([admin, host, other, nobody, student])
        await session.flush()
        session.add_all([
            _interview(1, student, host, now + dt.timedelta(days=2)),            # upcoming
            _interview(2, student, host, now - dt.timedelta(hours=3)),           # attendance overdue
            _interview(3, student, other, now - dt.timedelta(days=1),            # someone else's
                       status="completed"),
            _interview(4, student, host, now - dt.timedelta(days=2),             # result not shared
                       status="completed", result="selected"),
            JobProfile(job_name="Data", job_description="d", status="under_review",
                       interview_mode="assessment"),
        ])
        await session.commit()

        mine = await _tasks(session, host)
        assert mine["interviews_awaiting_attendance"]["oldest_at"] is not None
        assert mine["interviews_upcoming"]["count"] == 1
        assert mine["interviews_awaiting_attendance"]["count"] == 1
        assert mine["interviews_release_pending"]["count"] == 1
        # The other host's interview waits for feedback, but not on this host.
        assert "interviews_awaiting_feedback" not in mine
        # Only admins review templates or see operations.
        assert "templates_review_assessment" not in mine
        assert "processing_failures" not in mine

        everything = await _tasks(session, admin)
        assert everything["interviews_awaiting_feedback"]["count"] == 1
        assert everything["templates_review_assessment"]["count"] == 1
        assert everything["templates_review_assessment"]["action"]["href"] == "/dashboard/samvaad/roles"
        # Nothing to repair: the task is absent, not a zero.
        assert "processing_failures" not in everything

        # No human-interview permission and not an admin: an empty queue.
        assert await _tasks(session, nobody) == {}

        # Today's interviews: an interview starting in 10 minutes is joinable, in order.
        # Joinable and within today's Indian day, whatever time the test runs.
        ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
        start = now + dt.timedelta(minutes=10)
        if start.astimezone(ist).date() != now.astimezone(ist).date():
            start = now - dt.timedelta(minutes=5)
        soon = _interview(5, student, host, start)
        session.add(soon)
        await session.commit()
        today = await _today(session, host)
        joinable = [t for t in today if t["id"] == soon.id]
        assert joinable and joinable[0]["can_join"] is True
        assert joinable[0]["student_name"] == "student"
        assert await _today(session, nobody) == []

        # Things to act on come before things to know about.
        order = [t["id"] for t in (await my_tasks(fastapi.Response(), host, session))["tasks"]]
        assert order[-1] == "interviews_upcoming"


async def test_today_splits_job_rounds_and_counts_coaching_due(factory):
    import uuid

    from src.models.db.coaching_assignment import CoachingAssignment
    from src.models.db.opportunity import Opportunity

    now = dt.datetime.now(dt.timezone.utc)
    async with factory() as session:
        admin = _person("boss@example.test", is_admin=True)
        student = _person("kid@example.test")
        plain = _person("plain@example.test")
        session.add_all([admin, student, plain])
        await session.flush()
        job = Opportunity(course_id="c1", title="Analyst", organisation="Acme", created_by=admin.id,
                          request_key=str(uuid.uuid4()), request_hash="h")
        session.add(job)
        await session.flush()
        soon = now + dt.timedelta(minutes=5)
        session.add_all([
            _interview(1, student, admin, soon, opportunity_id=job.id),
            _interview(2, student, admin, soon + dt.timedelta(minutes=1)),
        ])
        today = now.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).date()
        for i, due in enumerate([today, today, today - dt.timedelta(days=2), today + dt.timedelta(days=3)]):
            session.add(CoachingAssignment(
                student_id=student.id, assigned_by=admin.id, course_id="c1", module="pace",
                reason="Practise pacing", due_date=due, request_key=str(uuid.uuid4()), request_hash=str(i),
            ))
        await session.commit()
        out = await my_tasks(fastapi.Response(), admin, session)
        kinds = sorted(t["kind"] for t in out["today"])
        assert kinds == ["interview", "job"]
        assert [t["opening"] for t in out["today"] if t["kind"] == "job"] == ["Analyst · Acme"]
        assert out["coaching_today"]["due_today"] == 2 and out["coaching_today"]["overdue"] == 1
        # Someone with no coaching access gets no counts at all.
        assert (await my_tasks(fastapi.Response(), plain, session))["coaching_today"] is None
