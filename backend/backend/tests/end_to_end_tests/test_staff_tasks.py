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

        # Things to act on come before things to know about.
        order = [t["id"] for t in (await my_tasks(fastapi.Response(), host, session))["tasks"]]
        assert order[-1] == "interviews_upcoming"
