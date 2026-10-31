"""Real SQL tests for cohorts (pack sec 4, F31): grouping inside a course, never access."""

import datetime as dt
import uuid

import fastapi
import httpx
import pytest

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes import cohorts, learners
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


@pytest.fixture
async def course(db):
    _, factory, _ = db
    now = dt.datetime.now(dt.timezone.utc)
    async with factory() as s:
        users = {}
        for name, college, courses, permissions in [
            ("manager", None, [], {"course_workspace": ["read", "update"]}),
            ("viewer", None, [], {"course_workspace": ["read"]}),
            ("outsider", None, [], {}),
            ("asha", "City College", ["course-a"], {}),
            ("bilal", "Hill College", ["course-a"], {}),
            ("chitra", "City College", ["course-b"], {}),
        ]:
            user = User(
                email=f"{name}@cohort.test",
                name=name,
                password_hash="test",
                university=college,
                enrolled_course_ids=courses,
                enrollment_source="sampark",
                enrollment_observed_at=now,
                feature_permissions=permissions,
            )
            s.add(user)
            await s.flush()
            users[name] = user.id
        team = Team(name="Programme")
        s.add(team)
        await s.flush()
        # The manager covers the whole course; the viewer only City College.
        for name, colleges in (("manager", None), ("viewer", ["City College"]), ("outsider", None)):
            s.add(
                TeamMember(
                    team_id=team.id,
                    user_id=users[name],
                    access_role="team member",
                    purposes=["facilitator"],
                    course_ids=["course-a"],
                    colleges=colleges,
                )
            )
        await s.commit()
    app = fastapi.FastAPI()
    app.include_router(cohorts.router)
    app.include_router(learners.router)

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
        yield client, users


def as_(who):
    return {"x-test-actor": who}


async def make(c, name="Graduating batch A"):
    r = await c.post(
        "/v2/cohorts/courses/course-a",
        json={"request_key": str(uuid.uuid4()), "name": name},
        headers=as_("manager"),
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_members_must_be_in_the_course_and_scope(course):
    c, users = course
    cohort = await make(c)
    base = f"/v2/cohorts/courses/course-a/{cohort['id']}"
    body = {"expected_version": 1, "add": [users["asha"], users["chitra"]], "reason": "Final-year group for placements."}
    # chitra is not enrolled in course-a.
    assert (await c.put(f"{base}/members", json=body, headers=as_("manager"))).status_code == 422
    body["add"] = [users["asha"], users["bilal"]]
    assert (await c.put(f"{base}/members", json=body, headers=as_("manager"))).status_code == 200
    # A stale version is refused.
    assert (await c.put(f"{base}/members", json=body, headers=as_("manager"))).status_code == 409

    full = (await c.get(base, headers=as_("manager"))).json()
    assert [m["name"] for m in full["items"]] == ["asha", "bilal"]
    # The viewer covers City College only: bilal is counted, never named.
    scoped = (await c.get(base, headers=as_("viewer"))).json()
    assert [m["name"] for m in scoped["items"]] == ["asha"]
    assert scoped["members_outside_your_scope"] == 1 and scoped["can_manage"] is False
    listed = (await c.get("/v2/cohorts/courses/course-a", headers=as_("viewer"))).json()
    assert listed["items"][0]["members"] == 1 and listed["can_manage"] is False


async def test_managing_needs_update_and_course_access(course):
    c, users = course
    cohort = await make(c)
    base = f"/v2/cohorts/courses/course-a/{cohort['id']}"
    body = {"expected_version": 1, "add": [users["asha"]], "reason": "Adding a learner to the batch."}
    assert (await c.put(f"{base}/members", json=body, headers=as_("viewer"))).status_code == 403
    assert (await c.get(base, headers=as_("outsider"))).status_code == 403
    assert (await c.get("/v2/cohorts/courses/course-b", headers=as_("manager"))).status_code == 403
    # Archiving freezes membership but keeps it.
    archive = {"expected_version": 1, "archived": True, "reason": "Batch graduated in October."}
    assert (await c.patch(base, json=archive, headers=as_("manager"))).status_code == 200
    body["expected_version"] = 2
    assert (await c.put(f"{base}/members", json=body, headers=as_("manager"))).status_code == 409
    assert (await c.get("/v2/cohorts/courses/course-a", headers=as_("manager"))).json()["items"] == []


async def test_learner_page_lists_cohorts(course):
    c, users = course
    cohort = await make(c, "Evening group")
    await c.put(
        f"/v2/cohorts/courses/course-a/{cohort['id']}/members",
        json={"expected_version": 1, "add": [users["asha"]], "reason": "Evening classes group."},
        headers=as_("manager"),
    )
    record = (await c.get(f"/v2/learners/{users['asha']}", headers=as_("manager"))).json()
    assert record["cohorts"] == [{"id": cohort["id"], "name": "Evening group", "course_id": "course-a"}]


async def test_learner_search_is_scoped_names_only(course):
    c, users = course
    found = (await c.get("/v2/cohorts/courses/course-a/learners?q=a", headers=as_("manager"))).json()
    assert [r["name"] for r in found["items"]] == ["asha", "bilal"]
    assert set(found["items"][0]) == {"id", "name", "college"}
    # Searching to add members is a managing action.
    assert (await c.get("/v2/cohorts/courses/course-a/learners", headers=as_("viewer"))).status_code == 403
