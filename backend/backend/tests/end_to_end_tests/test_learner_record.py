"""Real SQL tests for the one learner record (pack sec 3) and purpose-split learner analytics."""

import datetime as dt
import uuid

import fastapi
import httpx
import pytest

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes import learners
from src.models.db.interview import Interview
from src.models.db.opportunity import Opportunity, OpportunityCandidate
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.services.analytics import AnalyticsService
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


@pytest.fixture
async def record(db):
    _, factory, _ = db
    now = dt.datetime.now(dt.timezone.utc)
    async with factory() as s:
        users = {}
        for name, courses, permissions in [
            ("lead", ["course-a"], {"placement_shortlists": ["read"]}),
            ("outsider", [], {}),
            ("student", ["course-a", "course-b"], {}),
        ]:
            user = User(
                email=f"{name}@learner.test",
                name=name,
                password_hash="test",
                university="City College",
                enrolled_course_ids=courses,
                enrollment_source="sampark",
                enrollment_observed_at=now,
                feature_permissions=permissions,
            )
            s.add(user)
            await s.flush()
            users[name] = user.id
        team = Team(name="Placement")
        s.add(team)
        await s.flush()
        s.add(
            TeamMember(
                team_id=team.id,
                user_id=users["lead"],
                access_role="team member",
                purposes=["placement"],
                course_ids=["course-a"],
            )
        )
        for course in ("course-a", "course-b"):
            opening = Opportunity(
                course_id=course,
                title=f"Analyst {course}",
                organisation="Example",
                created_by=users["lead"],
                request_key=str(uuid.uuid4()),
                request_hash="x",
                max_evidence_age_days=30,
                min_assessments=1,
                status="open",
                eligibility_version=1,
            )
            s.add(opening)
            await s.flush()
            s.add(
                OpportunityCandidate(
                    opportunity_id=opening.id,
                    student_id=users["student"],
                    added_by=users["lead"],
                )
            )
        for mode in ("practice", "assessment"):
            s.add(
                Interview(
                    user_id=users["student"],
                    track="Analyst",
                    course_id="course-a",
                    interview_mode=mode,
                    status="completed",
                    completed_at=now,
                )
            )
        await s.commit()
    app = fastapi.FastAPI()
    app.include_router(learners.router)

    async def session_dep():
        async with factory() as s:
            try:
                yield s
            finally:
                await s.rollback()

    async def actor(
        request: fastapi.Request, session=fastapi.Depends(get_async_session)
    ):
        return await session.get(User, users[request.headers["x-test-actor"]])

    app.dependency_overrides[get_async_session] = session_dep
    app.dependency_overrides[get_current_admin_user] = actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, factory, users


async def test_record_lists_only_courses_and_openings_in_scope(record):
    c, _, users = record
    r = await c.get(f"/v2/learners/{users['student']}", headers={"x-test-actor": "lead"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert r.headers["cache-control"] == "no-store"
    assert body["student"]["name"] == "student"
    # course-b is the learner's, but outside this person's grant.
    assert body["courses"] == ["course-a"]
    assert [o["title"] for o in body["opportunities"]] == ["Analyst course-a"]
    assert body["opportunities"][0]["decision"] is None
    assert body["sections"]["opportunities"] is True
    assert body["sections"]["summary"] is False
    assert body["sections"]["coaching"] is True
    assert body["sections"]["mock_interviews"] is False
    assert body["human_rounds"] is None
    assert body["actions"] == {"assign_coaching": False, "schedule_assessment": False}


async def test_record_hidden_without_any_reach(record):
    c, _, users = record
    r = await c.get(
        f"/v2/learners/{users['student']}", headers={"x-test-actor": "outsider"}
    )
    assert r.status_code == 404
    assert "student" not in r.text


async def test_staff_are_not_learner_records(record):
    c, _, users = record
    r = await c.get(f"/v2/learners/{users['lead']}", headers={"x-test-actor": "lead"})
    assert r.status_code == 404


async def test_learner_analytics_split_by_purpose(record):
    _, factory, users = record
    async with factory() as s:
        service = AnalyticsService(s)
        everything = await service._list_interviews(user_id=users["student"])
        formal = await service._list_interviews(
            user_id=users["student"], interview_mode="assessment"
        )
    assert {i.interview_mode for i in everything} == {"practice", "assessment"}
    assert [i.interview_mode for i in formal] == ["assessment"]
