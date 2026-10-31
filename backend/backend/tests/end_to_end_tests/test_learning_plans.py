"""Real SQL tests for learning plans (pack sec 4): authoring, versions, assignment, progress."""

import datetime as dt
import uuid

import fastapi
import httpx
import pytest

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.api.routes import learning_plans
from src.models.db.interview import Interview
from src.models.db.job_profile import JobProfile
from src.models.db.practice_drill_completion import PracticeDrillCompletion
from src.models.db.practice_governance import PracticeConsent
from src.models.db.user import User
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


@pytest.fixture
async def plans(db):
    _, factory, _ = db
    now = dt.datetime.now(dt.timezone.utc)
    async with factory() as s:
        admin = User(email="admin@plans.test", name="admin", password_hash="test", is_admin=True)
        staff = User(email="staff@plans.test", name="staff", password_hash="test", feature_permissions={})
        student = User(email="asha@plans.test", name="asha", password_hash="test",
                       enrolled_course_ids=["course-a"], enrollment_source="sampark", enrollment_observed_at=now)
        mock = JobProfile(job_name="Project defence", job_description="Defend your final-year project.",
                          interview_mode="practice", status="published")
        s.add_all([admin, staff, student, mock])
        await s.flush()
        s.add(PracticeConsent(user_id=student.id, purpose="facilitator_coaching", granted=True, policy_version="v1"))
        await s.commit()
        ids = {"admin": admin.id, "staff": staff.id, "student": student.id, "mock": mock.id}
    app = fastapi.FastAPI()
    app.include_router(learning_plans.router)

    async def session_dep():
        async with factory() as s:
            try:
                yield s
            finally:
                await s.rollback()

    async def actor(request: fastapi.Request, session=fastapi.Depends(get_async_session)):
        return await session.get(User, ids[request.headers["x-test-actor"]])

    app.dependency_overrides[get_async_session] = session_dep
    app.dependency_overrides[get_current_admin_user] = actor
    app.dependency_overrides[get_current_user] = actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c, factory, ids


def as_(who):
    return {"x-test-actor": who}


async def published_plan(c, ids, self_start=False):
    plan = (await c.post("/v2/learning-plans", headers=as_("admin"), json={
        "request_key": str(uuid.uuid4()), "title": "Explain your project",
        "goal": "Explain your final-year project clearly in one minute.", "self_start": self_start,
    })).json()
    steps = {"expected_row_version": plan["row_version"], "steps": [
        {"kind": "drill", "module": "warm_up", "title": "Warm up your voice"},
        {"kind": "mock", "job_profile_id": ids["mock"], "title": "Project-defence mock"},
        {"kind": "reflection", "title": "What will you change next time?"},
    ]}
    plan = (await c.put(f"/v2/learning-plans/{plan['id']}/steps", json=steps, headers=as_("admin"))).json()
    r = await c.post(f"/v2/learning-plans/{plan['id']}/publish",
                     json={"expected_row_version": plan["row_version"]}, headers=as_("admin"))
    assert r.status_code == 200, r.text
    return r.json()


async def test_authoring_publish_freezes_and_revise_makes_a_new_version(plans):
    c, _, ids = plans
    assert (await c.get("/v2/learning-plans", headers=as_("staff"))).status_code == 403
    plan = await published_plan(c, ids)
    assert plan["status"] == "published" and len(plan["steps"]) == 3
    r = await c.patch(f"/v2/learning-plans/{plan['id']}", headers=as_("admin"), json={
        "expected_row_version": plan["row_version"], "title": "Changed", "goal": "A different goal entirely."})
    assert r.status_code == 409
    draft = (await c.post(f"/v2/learning-plans/{plan['id']}/revise", headers=as_("admin"))).json()
    assert draft["version"] == 2 and draft["status"] == "draft" and draft["parent_id"] == plan["id"]
    assert [s["title"] for s in draft["steps"]] == [s["title"] for s in plan["steps"]]
    # A mock step must use a published mock template.
    bad = {"expected_row_version": draft["row_version"], "steps": [
        {"kind": "mock", "job_profile_id": 999999, "title": "Missing template"}]}
    assert (await c.put(f"/v2/learning-plans/{draft['id']}/steps", json=bad, headers=as_("admin"))).status_code == 422


async def test_steps_complete_in_order_from_evidence(plans):
    c, factory, ids = plans
    plan = await published_plan(c, ids)
    base = f"/v2/learning-plans/courses/course-a/students/{ids['student']}"
    r = await c.post(base, headers=as_("admin"), json={
        "request_key": str(uuid.uuid4()), "plan_id": plan["id"], "reason": "Prepare for the project viva."})
    assert r.status_code == 201, r.text
    mine = (await c.get("/v2/learning-plans/me/plans", headers=as_("student"))).json()["items"][0]
    assert [s["status"] for s in mine["steps"]] == ["current", "upcoming", "upcoming"]

    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=2)
    async with factory() as s:
        # A finished mock before the drill does not skip ahead: steps go in order.
        s.add(Interview(user_id=ids["student"], track="Defence", course_id="course-a", interview_mode="practice",
                        job_profile_id=ids["mock"], status="completed", completed_at=later))
        await s.commit()
    mine = (await c.get("/v2/learning-plans/me/plans", headers=as_("student"))).json()["items"][0]
    assert [s["status"] for s in mine["steps"]] == ["current", "upcoming", "upcoming"]

    async with factory() as s:
        s.add(PracticeDrillCompletion(user_id=ids["student"], module="warm_up", level="beginner",
                                      client_completion_id="warmup:plan001", evidence_kind="audio",
                                      evidence_units=300, completed_at=later - dt.timedelta(seconds=1)))
        await s.commit()
    mine = (await c.get("/v2/learning-plans/me/plans", headers=as_("student"))).json()["items"][0]
    assert [s["status"] for s in mine["steps"]] == ["done", "done", "current"]

    reflection_step = mine["steps"][2]["id"]
    url = f"/v2/learning-plans/me/{mine['id']}/steps/{mine['steps'][0]['id']}/reflection"
    text = {"text": "I will open with the problem my project solves before the method."}
    assert (await c.post(url, json=text, headers=as_("student"))).status_code == 409
    r = await c.post(f"/v2/learning-plans/me/{mine['id']}/steps/{reflection_step}/reflection",
                     json=text, headers=as_("student"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed" and r.json()["completed_steps"] == 3
    staff_view = (await c.get(base, headers=as_("admin"))).json()["items"][0]
    assert staff_view["steps"][2]["reflection"].startswith("I will open")


async def test_self_start_needs_an_open_plan_and_enrollment(plans):
    c, _, ids = plans
    closed = await published_plan(c, ids)
    start = {"request_key": str(uuid.uuid4()), "plan_id": closed["id"], "course_id": "course-a"}
    assert (await c.post("/v2/learning-plans/me/start", json=start, headers=as_("student"))).status_code == 422
    open_plan = await published_plan(c, ids, self_start=True)
    available = (await c.get("/v2/learning-plans/me/available", headers=as_("student"))).json()["items"]
    assert [p["id"] for p in available] == [open_plan["id"]]
    start["plan_id"] = open_plan["id"]
    r = await c.post("/v2/learning-plans/me/start", json=start, headers=as_("student"))
    assert r.status_code == 201 and r.json()["source"] == "self"
    elsewhere = {**start, "request_key": str(uuid.uuid4()), "course_id": "course-z"}
    assert (await c.post("/v2/learning-plans/me/start", json=elsewhere, headers=as_("student"))).status_code in (403, 404)
