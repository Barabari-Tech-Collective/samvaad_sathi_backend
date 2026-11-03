"""Real SQL test for the Overview's support follow-ups (pack F23)."""

import datetime as dt

import fastapi
import httpx
import pytest

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes import staff_tasks
from src.models.db.interview import Interview
from src.models.db.practice_governance import PracticeConsent
from src.models.db.user import User
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


@pytest.fixture
async def overview(db):
    _, factory, _ = db
    now = dt.datetime.now(dt.timezone.utc)
    async with factory() as s:
        admin = User(email="admin@support.test", name="admin", password_hash="test", is_admin=True)
        plain = User(email="plain@support.test", name="plain", password_hash="test", feature_permissions={})
        sharing = User(email="asha@support.test", name="asha", password_hash="test",
                       enrolled_course_ids=["course-a"], enrollment_source="sampark", enrollment_observed_at=now)
        private = User(email="bilal@support.test", name="bilal", password_hash="test",
                       enrolled_course_ids=["course-a"], enrollment_source="sampark", enrollment_observed_at=now)
        s.add_all([admin, plain, sharing, private])
        await s.flush()
        s.add(PracticeConsent(user_id=sharing.id, purpose="facilitator_coaching", granted=True, policy_version="v1"))
        # Both sessions are blocked, but only the one shared for coaching may be counted.
        for student in (sharing, private):
            s.add(Interview(user_id=student.id, track="Analyst", course_id="course-a",
                            interview_mode="practice", status="failed",
                            created_at=now - dt.timedelta(hours=1)))
        await s.commit()
        users = {"admin": admin.id, "plain": plain.id}
    app = fastapi.FastAPI()
    app.include_router(staff_tasks.router)

    async def session_dep():
        async with factory() as s:
            try:
                yield s
            finally:
                await s.rollback()

    async def actor(request: fastapi.Request, session=fastapi.Depends(get_async_session)):
        return await session.get(User, users[request.headers["x-test-actor"]])

    app.dependency_overrides[get_async_session] = session_dep
    app.dependency_overrides[get_current_admin_user] = actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_support_follow_ups_count_only_shared_practice(overview):
    tasks = (await overview.get("/v2/staff/tasks", headers={"x-test-actor": "admin"})).json()["tasks"]
    task = next(t for t in tasks if t["id"] == "support_follow_ups")
    assert task["count"] == 2
    assert task["action"]["href"] == "/dashboard/samvaad/courses/course-a/support"
    assert task["oldest_at"] is not None
    tasks = (await overview.get("/v2/staff/tasks", headers={"x-test-actor": "plain"})).json()["tasks"]
    assert all(t["id"] != "support_follow_ups" for t in tasks)
