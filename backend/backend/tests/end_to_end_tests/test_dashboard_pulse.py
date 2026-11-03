"""Programme pulse: current vs previous period, per-day series, students only, no fake zeros."""

import datetime as dt
import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api.routes.analytics_v2 import _IST, get_dashboard_pulse
from src.models.db.interview import Interview
from src.models.db.report import Report
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


async def test_pulse_compares_periods_and_counts_students_only(factory):
    noon_today = dt.datetime.now(_IST).replace(hour=12, minute=0, second=0, microsecond=0)
    async with factory() as session:
        a = User(email="a@example.test", name="a", password_hash="x")
        b = User(email="b@example.test", name="b", password_hash="x")
        staff = User(email="s@example.test", name="s", password_hash="x", is_admin=True)
        session.add_all([a, b, staff])
        await session.flush()
        # Both learners signed up today, so this week has two sign-ups and last week none.
        for user in (a, b, staff):
            user.created_at = noon_today

        def interview(user, mode, status, days_ago):
            return Interview(
                user_id=user.id, track="Data", difficulty="easy", status=status,
                interview_mode=mode, created_at=noon_today - dt.timedelta(days=days_ago),
            )

        this_week = [
            interview(a, "assessment", "completed", 0),
            interview(a, "practice", "completed", 1),
            interview(b, "assessment", "completed", 2),
            interview(b, "practice", "started", 2),
            interview(staff, "assessment", "completed", 0),  # staff test: never counted
        ]
        last_week = [interview(a, "assessment", "completed", 8)]
        session.add_all(this_week + last_week)
        await session.flush()
        session.add_all([
            Report(interview_id=this_week[0].id, overall_score=60),
            Report(interview_id=this_week[2].id, overall_score=40),
            Report(interview_id=this_week[4].id, overall_score=100),
            Report(interview_id=last_week[0].id, overall_score=30),
        ])
        await session.commit()

        pulse = await get_dashboard_pulse(days=7, current_user=staff, session=session)
        m = pulse["metrics"]
        # Staff accounts are never counted as sign-ups.
        assert m["sign_ups"]["value"] == 2 and m["sign_ups"]["previous"] == 0
        assert m["sign_ups"]["series"][-1] == 2 and len(m["sign_ups"]["series"]) == 7
        assert m["active_learners"]["value"] == 2 and m["active_learners"]["previous"] == 1
        assert m["assessments_taken"]["value"] == 2 and m["assessments_taken"]["change"] == 1
        assert m["average_assessment_score"]["value"] == 50.0
        assert m["average_assessment_score"]["change"] == 20.0
        assert m["mock_interviews"]["value"] == 2

        series = m["average_assessment_score"]["series"]
        assert len(series) == 7 and series[-1] == 60.0
        # A day with no finished assessment has no average, not a zero.
        assert None in series
        days = pulse["interviews_by_day"]
        assert days[-1] == {"date": noon_today.date().isoformat(), "mock": 0, "assessment": 1}
