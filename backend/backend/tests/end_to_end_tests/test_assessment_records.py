"""Real SQL tests for the one assessment record (pack sec 3): both kinds, and who may open them."""

import datetime as dt
import uuid

import fastapi
import httpx
import pytest

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes import assessment_records
from src.models.db.human_interview import HumanInterview
from src.models.db.interview import Interview
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


@pytest.fixture
async def records(db):
    _, factory, _ = db
    now = dt.datetime.now(dt.timezone.utc)
    async with factory() as s:
        users = {}
        for name, permissions in [
            ("host", {"human_interviews": ["read", "update"]}),
            ("other_host", {"human_interviews": ["read", "update"]}),
            ("analyst", {"analytics": ["read"]}),
            ("outsider", {}),
            ("student", {}),
        ]:
            user = User(
                email=f"{name}@record.test",
                name=name,
                password_hash="test",
                university="City College",
                enrolled_course_ids=["course-a"] if name == "student" else [],
                enrollment_source="sampark",
                enrollment_observed_at=now,
                feature_permissions=permissions,
            )
            s.add(user)
            await s.flush()
            users[name] = user.id
        team = Team(name="Interviewers")
        s.add(team)
        await s.flush()
        for name, courses in (
            ("host", ["course-a"]),
            ("other_host", ["course-a"]),
            # Analytics reach comes from a membership, not from a course grant.
            ("analyst", []),
        ):
            s.add(
                TeamMember(
                    team_id=team.id,
                    user_id=users[name],
                    access_role="team member",
                    purposes=["facilitator"],
                    course_ids=courses,
                )
            )
        ids = {}
        for mode in ("assessment", "practice"):
            interview = Interview(
                user_id=users["student"],
                track="Analyst",
                course_id="course-a",
                interview_mode=mode,
                status="completed",
                completed_at=now,
            )
            s.add(interview)
            await s.flush()
            ids[mode] = interview.id
        round_ = HumanInterview(
            course_id="course-a",
            student_id=users["student"],
            host_id=users["host"],
            title="Technical panel",
            meet_url="https://meet.google.com/abc-defg-hij",
            starts_at=now - dt.timedelta(hours=2),
            ends_at=now - dt.timedelta(hours=1),
            request_key=str(uuid.uuid4()),
            request_hash="x",
            status="completed",
            result="next_round",
            feedback="Clear structure; practise SQL joins.",
            close_reason="Panel agreed the student attended in full.",
        )
        s.add(round_)
        await s.flush()
        ids["human"] = round_.id
        s.add(
            PracticeDashboardAuditEvent(
                actor_id=users["host"],
                action="human_interview_closed",
                purpose="human_interview",
                target_type="human_interview",
                target_id=str(round_.id),
                reason="Panel agreed the student attended in full.",
                event_metadata={},
            )
        )
        await s.commit()
    app = fastapi.FastAPI()
    app.include_router(assessment_records.router)

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
        yield client, users, ids


async def get(c, path, who):
    return await c.get(path, headers={"x-test-actor": who})


async def test_ai_record_for_analytics_staff_only(records):
    c, users, ids = records
    r = await get(c, f"/v2/assessment-records/ai/{ids['assessment']}", "analyst")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "ai" and body["status_label"] == "Completed"
    assert body["participants"]["student"]["id"] == users["student"]
    assert body["result"]["release_rule"].startswith("Shown to the student")
    assert body["integrity"] is None and body["history"] == []
    assert body["result"]["held"] is False and body["can_release"] is False
    assert (await get(c, f"/v2/assessment-records/ai/{ids['assessment']}", "outsider")).status_code == 404


async def test_mock_interviews_are_not_assessment_records(records):
    c, _, ids = records
    r = await get(c, f"/v2/assessment-records/ai/{ids['practice']}", "analyst")
    assert r.status_code == 404


async def test_human_record_separates_feedback_from_internal_reasons(records):
    c, _, ids = records
    r = await get(c, f"/v2/assessment-records/human/{ids['human']}", "host")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status_label"] == "Outcome recorded"
    assert body["result"] == {
        "label": "next_round",
        "scores": None,
        "released": False,
        "released_at": None,
        "release_rule": "The interviewer releases the outcome to the student.",
    }
    assert body["student_feedback"] == "Clear structure; practise SQL joins."
    assert body["execution"]["close_reason"] == "Panel agreed the student attended in full."
    assert body["participants"]["interviewer"]["is_you"] is True
    assert [e["action"] for e in body["history"]] == ["human_interview_closed"]
    # The list's own entry, so the record can offer the list's actions.
    appointment = body["appointment"]
    assert appointment["id"] == ids["human"] and appointment["student_name"] == "student"
    assert appointment["can_record_result"] is True and appointment["can_manage"] is False
    assert appointment["enrollment_current"] is True


async def test_human_record_hidden_from_other_hosts_and_outsiders(records):
    c, _, ids = records
    for who in ("other_host", "outsider", "analyst"):
        r = await get(c, f"/v2/assessment-records/human/{ids['human']}", who)
        assert r.status_code == 404, who
    assert (await get(c, "/v2/assessment-records/human/999999", "host")).status_code == 404
