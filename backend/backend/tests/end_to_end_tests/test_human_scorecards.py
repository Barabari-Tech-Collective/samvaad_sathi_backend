"""Real SQL tests for human scorecards (pack F17, F20): interviewer, review, release."""

import datetime as dt
import uuid

import fastapi
import httpx
import pytest

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes import human_interviews, human_scorecards
from src.models.db.human_interview import HumanInterview
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.services import human_interviews as service
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


@pytest.fixture
async def cards(db, monkeypatch):
    monkeypatch.setattr(human_interviews, "send_interview_email", lambda *a, **k: None)
    _, factory, _ = db
    now = dt.datetime.now(dt.timezone.utc)
    async with factory() as s:
        users = {}
        for name, perms in [
            ("host", {"human_interviews": ["read", "create", "update"]}),
            ("interviewer", {"human_interviews": ["read", "update"]}),
            ("reviewer", {"human_interviews": ["read", "review"]}),
            ("outsider", {"human_interviews": ["read", "update"]}),
            ("student", {}),
        ]:
            user = User(email=f"{name}@card.test", name=name, password_hash="test",
                        enrolled_course_ids=["course-a"] if name == "student" else [],
                        enrollment_source="sampark", enrollment_observed_at=now,
                        feature_permissions=perms)
            s.add(user)
            await s.flush()
            users[name] = user.id
        team = Team(name="Interviews")
        s.add(team)
        await s.flush()
        for name in ("host", "interviewer", "reviewer"):
            s.add(TeamMember(team_id=team.id, user_id=users[name], access_role="team member",
                             purposes=["facilitator"], course_ids=["course-a"]))
        row = HumanInterview(
            course_id="course-a", student_id=users["student"], host_id=users["host"],
            interviewer_id=users["interviewer"], title="Technical panel",
            meet_url="https://meet.google.com/abc-defg-hij",
            starts_at=now - dt.timedelta(hours=2), ends_at=now - dt.timedelta(hours=1),
            request_key=str(uuid.uuid4()), request_hash="x", status="completed",
        )
        s.add(row)
        await s.commit()
        appointment = row.id
    app = fastapi.FastAPI()
    app.include_router(human_interviews.router)
    app.include_router(human_scorecards.router)

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
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c, factory, users, appointment


def as_(who):
    return {"x-test-actor": who}


DRAFT = {
    "expected_version": 0,
    "criteria": [
        {"name": "Communication", "score": 4, "evidence": "Explained the join clearly."},
        {"name": "Technical depth", "score": 3, "evidence": None},
    ],
    "suggested_result": "next_round",
    "internal_notes": "Hesitant on indexing; panel agreed to proceed.",
    "student_comments": "Clear structure. Practise explaining indexes.",
}


async def test_interviewer_drafts_reviewer_approves_and_releases(cards):
    c, factory, users, appointment = cards
    url = f"/v2/human-interviews/courses/course-a/{appointment}/scorecard"
    # The assigned interviewer sees and edits it; someone else with no link to it cannot.
    assert (await c.get(url, headers=as_("interviewer"))).json()["can_edit"] is True
    assert (await c.get(url, headers=as_("outsider"))).status_code in (403, 404)
    # A reviewer cannot write the draft; only the interviewer or host can.
    assert (await c.put(url, json=DRAFT, headers=as_("reviewer"))).status_code in (403, 404, 409)
    r = await c.put(url, json=DRAFT, headers=as_("interviewer"))
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["status"] == "draft" and card["version"] == 1
    # Cannot be reviewed or released before submission.
    review = {"expected_version": 1, "decision": "approve", "note": "Evidence supports the result."}
    assert (await c.post(f"{url}/review", json=review, headers=as_("reviewer"))).status_code == 409
    # The quick outcome form no longer bypasses review.
    quick = await c.post(
        f"/v2/human-interviews/courses/course-a/{appointment}/outcome",
        json={"expected_version": 1, "result": "selected", "feedback": "Great interview overall.", "release": True},
        headers=as_("host"),
    )
    assert quick.status_code == 409 and "scorecard" in quick.text
    r = await c.post(f"{url}/submit", json={"expected_version": 1}, headers=as_("interviewer"))
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    # The interviewer cannot approve their own scorecard, and a stale version is refused.
    assert (await c.post(f"{url}/review", json={**review, "expected_version": 2}, headers=as_("interviewer"))).status_code == 403
    assert (await c.post(f"{url}/review", json={**review, "expected_version": 1}, headers=as_("reviewer"))).status_code == 409
    r = await c.post(f"{url}/review", json={**review, "expected_version": 2}, headers=as_("reviewer"))
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["can_release"] is True
    # Approved but not yet shared: the student sees nothing.
    async with factory() as s:
        row = await s.get(HumanInterview, appointment)
        shown = service.metadata(row, dt.datetime.now(dt.timezone.utc))
        assert shown["result"] is None and shown["feedback"] is None
    r = await c.post(f"{url}/release", json={"expected_version": 3}, headers=as_("reviewer"))
    assert r.status_code == 200 and r.json()["released"] is True
    async with factory() as s:
        row = await s.get(HumanInterview, appointment)
        shown = service.metadata(row, dt.datetime.now(dt.timezone.utc))
        assert shown["result"] == "next_round"
        assert shown["feedback"] == "Clear structure. Practise explaining indexes."
        assert "Hesitant" not in str(shown)
    assert (await c.post(f"{url}/release", json={"expected_version": 3}, headers=as_("reviewer"))).status_code == 409


async def test_returned_scorecard_goes_back_to_the_interviewer(cards):
    c, _, users, appointment = cards
    url = f"/v2/human-interviews/courses/course-a/{appointment}/scorecard"
    await c.put(url, json=DRAFT, headers=as_("host"))
    await c.post(f"{url}/submit", json={"expected_version": 1}, headers=as_("host"))
    r = await c.post(f"{url}/review", headers=as_("reviewer"), json={
        "expected_version": 2, "decision": "return", "note": "Add evidence for the technical score."})
    assert r.status_code == 200 and r.json()["status"] == "returned"
    mine = (await c.get(url, headers=as_("interviewer"))).json()
    assert mine["can_edit"] is True and mine["review_note"].startswith("Add evidence")
    incomplete = {**DRAFT, "expected_version": 3, "student_comments": None}
    await c.put(url, json=incomplete, headers=as_("interviewer"))
    r = await c.post(f"{url}/submit", json={"expected_version": 4}, headers=as_("interviewer"))
    assert r.status_code == 422


async def test_interviewer_choices_are_staff_who_may_take_interviews(cards):
    c, _, users, _ = cards
    r = await c.get("/v2/human-interviews/courses/course-a/interviewers", headers=as_("host"))
    assert r.status_code == 200, r.text
    assert sorted(i["name"] for i in r.json()["items"]) == ["interviewer"]
