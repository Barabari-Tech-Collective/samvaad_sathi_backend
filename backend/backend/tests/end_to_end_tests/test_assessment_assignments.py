"""Real SQL tests for assigning AI assessments (pack F14)."""

import datetime as dt
import uuid

import fastapi
import httpx
import pytest
import sqlalchemy as sa

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.api.routes import assessment_assignments as routes
from src.models.db.accommodation import AccommodationRequest
from src.models.db.cohort import Cohort, CohortMember
from src.models.db.job_profile import JobProfile
from src.models.db.job_profile_question import JobProfileQuestion
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.services import assessment_assignments as service
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401

UTC = dt.timezone.utc


@pytest.fixture
async def world(db):
    _, factory, _ = db
    now = dt.datetime.now(UTC)
    async with factory() as s:
        users = {}
        for name, college, courses, permissions in [
            ("planner", None, [], {"assessment_assignments": ["read", "create", "update"]}),
            ("reader", None, [], {"assessment_assignments": ["read"]}),
            ("asha", "City College", ["course-a"], {}),
            ("bilal", "City College", ["course-a"], {}),
            ("chitra", "City College", ["course-b"], {}),
        ]:
            u = User(
                email=f"{name}@aa.test", name=name, password_hash="test", university=college,
                enrolled_course_ids=courses, enrollment_source="sampark", enrollment_observed_at=now,
                feature_permissions=permissions,
            )
            s.add(u)
            await s.flush()
            users[name] = u.id
        team = Team(name="Programme")
        s.add(team)
        await s.flush()
        for name in ("planner", "reader"):
            s.add(
                TeamMember(
                    team_id=team.id, user_id=users[name], access_role="team member",
                    purposes=["facilitator"], course_ids=["course-a"], colleges=None,
                )
            )
        role = JobProfile(
            job_name="Backend role", job_description="d", status="published",
            interview_mode="assessment", questions_per_interview=2, content_revision=1,
        )
        s.add(role)
        await s.flush()
        s.add_all(
            JobProfileQuestion(job_profile_id=role.id, question_text=f"Q{i}", level=1, difficulty="easy")
            for i in range(3)
        )
        cohort = Cohort(course_id="course-a", name="Batch", request_key=str(uuid.uuid4()))
        s.add(cohort)
        await s.flush()
        s.add_all(CohortMember(cohort_id=cohort.id, student_id=users[n]) for n in ("asha", "bilal"))
        await s.commit()
        ids = {"role": role.id, "cohort": cohort.id}
    app = fastapi.FastAPI()
    app.include_router(routes.router)

    async def session_dep():
        async with factory() as s:
            try:
                yield s
            finally:
                await s.rollback()

    async def actor(request: fastapi.Request, session=fastapi.Depends(get_async_session)):
        return await session.get(User, users[request.headers["x-test-actor"]])

    app.dependency_overrides[get_async_session] = session_dep
    app.dependency_overrides[get_current_user] = actor
    app.dependency_overrides[get_current_admin_user] = actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c, factory, users, ids


def as_(who):
    return {"x-test-actor": who}


def body(ids, **extra):
    opens = dt.datetime.now(UTC) + dt.timedelta(days=1)
    return {
        "request_key": str(uuid.uuid4()), "job_profile_id": ids["role"], "difficulty": "easy",
        "opens_at": opens.isoformat(), "closes_at": (opens + dt.timedelta(days=2)).isoformat(),
    } | extra


async def test_cohort_bulk_assign_replay_scope_and_permissions(world):
    c, factory, users, ids = world
    payload = body(ids, cohort_id=ids["cohort"])
    r = await c.post("/v2/assessment-assignments/courses/course-a", json=payload, headers=as_("planner"))
    assert r.status_code == 201 and r.json()["created"] == 2, r.text
    again = await c.post("/v2/assessment-assignments/courses/course-a", json=payload, headers=as_("planner"))
    assert again.json()["created"] == 0 and again.json()["already_assigned"] == 2
    assert (await c.post("/v2/assessment-assignments/courses/course-a", json=body(ids, student_ids=[users["asha"]]), headers=as_("reader"))).status_code == 403
    out = await c.post(
        "/v2/assessment-assignments/courses/course-a", json=body(ids, student_ids=[users["chitra"]]), headers=as_("planner")
    )
    assert out.status_code == 404
    mine = (await c.get("/v2/assessment-assignments/me", headers=as_("asha"))).json()["items"]
    assert len(mine) == 1 and mine[0]["phase"] == "upcoming" and mine[0]["job_name"] == "Backend role"
    assert (await c.get("/v2/assessment-assignments/courses/course-a", headers=as_("reader"))).json()["can_create"] is False


async def test_validation(world):
    c, _, users, ids = world
    past = body(ids, student_ids=[users["asha"]], opens_at=(dt.datetime.now(UTC) - dt.timedelta(days=3)).isoformat(),
                closes_at=(dt.datetime.now(UTC) - dt.timedelta(days=2)).isoformat())
    assert (await c.post("/v2/assessment-assignments/courses/course-a", json=past, headers=as_("planner"))).status_code == 422
    both = body(ids, student_ids=[users["asha"]], cohort_id=ids["cohort"])
    assert (await c.post("/v2/assessment-assignments/courses/course-a", json=both, headers=as_("planner"))).status_code == 422
    bad = body(ids, student_ids=[users["asha"]], difficulty="hard")
    assert (await c.post("/v2/assessment-assignments/courses/course-a", json=bad, headers=as_("planner"))).status_code == 422


async def test_snapshot_window_gate_pinned_revision_and_reschedule(world):
    c, factory, users, ids = world
    async with factory() as s:
        s.add(
            AccommodationRequest(
                student_id=users["asha"], request_key=str(uuid.uuid4()), description="private words",
                status="approved", conditions=["extra_time_25"], confirmed_at=dt.datetime.now(UTC), decided_by=users["planner"],
            )
        )
        await s.commit()
    r = await c.post(
        "/v2/assessment-assignments/courses/course-a", json=body(ids, student_ids=[users["asha"]]), headers=as_("planner")
    )
    assert r.status_code == 201, r.text
    item = r.json()["items"][0]
    assert item["accommodations"] and "private words" not in str(item)
    async with factory() as s:
        profile = await s.get(JobProfile, ids["role"])
        row = (await s.execute(sa.select(service.AssessmentAssignment))).scalars().one()
        opens = row.opens_at
        with pytest.raises(fastapi.HTTPException) as early:
            await service.start_check(s, users["asha"], profile, "course-a", now=opens - dt.timedelta(hours=1))
        assert early.value.status_code == 409 and "opens on" in early.value.detail
        assert (await service.start_check(s, users["asha"], profile, "course-a", now=opens + dt.timedelta(hours=1))).id == row.id
        with pytest.raises(fastapi.HTTPException) as late:
            await service.start_check(s, users["asha"], profile, "course-a", now=row.closes_at + dt.timedelta(minutes=1))
        assert "reschedule" in late.value.detail
        # Nothing assigned to bilal: open access is unchanged.
        assert await service.start_check(s, users["bilal"], profile, "course-a") is None
        profile.content_revision = 2
        with pytest.raises(fastapi.HTTPException) as changed:
            await service.start_check(s, users["asha"], profile, "course-a", now=opens + dt.timedelta(hours=1))
        assert "assign it again" in changed.value.detail
        await s.rollback()
    new_open = dt.datetime.now(UTC) + dt.timedelta(days=5)
    moved = await c.post(
        f"/v2/assessment-assignments/courses/course-a/{item['id']}/reschedule",
        json={"expected_version": 1, "reason": "Moved after the outage.", "opens_at": new_open.isoformat()},
        headers=as_("planner"),
    )
    assert moved.status_code == 200 and moved.json()["version"] == 2
    stale = await c.post(
        f"/v2/assessment-assignments/courses/course-a/{item['id']}/cancel",
        json={"expected_version": 1, "reason": "Stale attempt here."}, headers=as_("planner"),
    )
    assert stale.status_code == 409
    done = await c.post(
        f"/v2/assessment-assignments/courses/course-a/{item['id']}/cancel",
        json={"expected_version": 2, "reason": "No longer needed."}, headers=as_("planner"),
    )
    assert done.json()["status"] == "cancelled"
    assert (await c.get("/v2/assessment-assignments/me", headers=as_("asha"))).json()["items"] == []
