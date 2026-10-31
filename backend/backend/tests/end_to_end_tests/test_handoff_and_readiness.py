"""Company handoff, AI round assignments and readiness suggestions (real SQL)."""

import datetime as dt
import uuid

from src.models.db.assessment_assignment import AssessmentAssignment
from src.models.db.interview import Interview
from src.models.db.job_profile import JobProfile
from src.models.db.job_profile_question import JobProfileQuestion
from src.models.db.opportunity import OpportunityRound
from src.models.db.practice_drill_completion import PracticeDrillCompletion  # noqa: F401
from src.models.db.summary_report import SummaryReport
from src.api.routes import readiness
from src.services import readiness as readiness_service
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401
from .test_opportunity_selection import change, decide, selection  # noqa: F401
import sqlalchemy as sa

CONTACT = {"contact_name": "Asha Rao", "contact_email": "asha@company.test"}


async def locked_selection(c, oid, users):
    base = f"/v2/opportunities/{oid}/selection"
    v = (
        await c.put(base + "/recommendation", json=change(0, student_id=users["student"], decision="include"))
    ).json()["version"]
    v = (await decide(c, base, users["student"], v)).json()["version"]
    assert (await c.post(base + "/lock", json=change(v))).status_code == 200


async def test_handoff_needs_a_lock_then_tracks_company_status(selection):
    c, factory, oid, users = selection
    url = f"/v2/opportunities/{oid}/handoff"
    assert (await c.get(url)).json()["selection_ready"] is False
    assert (await c.post(url, json=CONTACT)).status_code == 409
    await locked_selection(c, oid, users)
    assert (await c.post(url, json=CONTACT, headers={"x-test-actor": "member"})).status_code == 403
    made = await c.post(url, json=CONTACT)
    assert made.status_code == 201, made.text
    body = made.json()
    assert [x["name"] for x in body["candidates"]] == ["student"]
    assert body["candidates"][0]["company_status"] == "not_sent"
    assert (await c.post(url, json=CONTACT)).status_code == 409
    csv_text = (await c.get(url + "/export")).text
    assert "student@selection.test" in csv_text and "Example" in csv_text
    status = {"expected_version": 1, "company_status": "interviewing"}
    assert (await c.put(f"{url}/candidates/{users['student']}", json=status)).status_code == 409
    shared = await c.post(url + "/shared", json={"expected_version": 1, "channel": "email"})
    assert shared.status_code == 200 and shared.json()["candidates"][0]["company_status"] == "sent"
    assert (await c.post(url + "/shared", json={"expected_version": 1, "channel": "email"})).status_code == 409
    done = await c.put(
        f"{url}/candidates/{users['student']}",
        json={"expected_version": 2, "company_status": "offered", "note": "Offer letter sent."},
    )
    assert done.status_code == 200 and done.json()["candidates"][0]["company_status"] == "offered"
    assert (await c.put(f"{url}/candidates/999", json={"expected_version": 2, "company_status": "joined"})).status_code == 404


async def test_ai_round_assigns_every_candidate_once(selection):
    c, factory, oid, users = selection
    async with factory() as s:
        role = JobProfile(
            job_name="Backend", job_description="d", status="published", interview_mode="assessment",
            questions_per_interview=2, content_revision=1,
        )
        s.add(role)
        await s.flush()
        s.add_all(JobProfileQuestion(job_profile_id=role.id, question_text=f"Q{i}", level=1, difficulty="easy") for i in range(3))
        await s.commit()
        rid = role.id
    plan = await c.post(f"/v2/opportunities/{oid}/rounds", json={"kind": "ai", "name": "AI screen", "job_profile_id": rid})
    assert plan.status_code == 201, plan.text
    opens = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    payload = {
        "request_key": str(uuid.uuid4()), "difficulty": "easy", "opens_at": opens.isoformat(),
        "closes_at": (opens + dt.timedelta(days=2)).isoformat(),
    }
    url = f"/v2/opportunities/{oid}/rounds/{plan.json()['id']}/assign"
    assert (await c.post(url, json=payload, headers={"x-test-actor": "other"})).status_code == 403
    made = await c.post(url, json=payload)
    assert made.status_code == 200 and made.json() == {"created": 1, "already_assigned": 0}
    assert (await c.post(url, json=payload)).json() == {"created": 0, "already_assigned": 1}
    detail = (await c.get(f"/v2/opportunities/{oid}")).json()
    assert detail["rounds"][0]["assigned"] == 1 and detail["rounds"][0]["completed"] == 0
    async with factory() as s:
        row = (await s.execute(sa.select(AssessmentAssignment))).scalars().one()
        assert row.opportunity_round_id == plan.json()["id"] and row.student_id == users["student"]


async def test_readiness_shows_why_and_changes_with_rules(selection):
    c, factory, oid, users = selection
    now = dt.datetime.now(dt.timezone.utc)
    async with factory() as s:
        for days, score in ((20, 55.0), (10, 70.0), (3, 85.0)):
            iv = Interview(
                user_id=users["student"], track="Dev", course_id="course-a",
                interview_mode="practice", status="completed", completed_at=now - dt.timedelta(days=days),
            )
            s.add(iv)
            await s.flush()
            s.add(SummaryReport(interview_id=iv.id, report_json={"overallScoreSummary": {"overallPercentage": score}}))
        await s.commit()
    url = "/v2/readiness/courses/course-a"
    first_response = await c.get(url)
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    row = next(i for i in first["items"] if i["student_id"] == users["student"])
    # 3 mocks, average 70, but only 0 of 5 drills: one failed check -> "almost".
    assert row["status"] == "almost" and [k["key"] for k in row["checks"] if not k["passed"]] == ["drills"]
    assert first["rule"]["is_default"] is True and first["can_edit"] is True
    new_rule = {
        "expected_version": 0, "window_days": 60, "min_mock_interviews": 3, "min_average_score": 60,
        "min_drills": 0, "require_improving": True, "reason": "Drills are optional this term.",
    }
    assert (await c.put(url + "/rule", json=new_rule, headers={"x-test-actor": "other"})).status_code == 403
    assert (await c.put(url + "/rule", json=new_rule)).status_code == 200
    assert (await c.put(url + "/rule", json=new_rule)).status_code == 409
    again = (await c.get(url)).json()
    assert next(i for i in again["items"] if i["student_id"] == users["student"])["status"] == "ready"
    assert again["counts"]["ready"] == 1


def test_judge_marks_not_yet_when_two_checks_fail():
    rule = readiness_service.DEFAULTS
    status, checks = readiness_service.judge(rule, [], 0, 0)
    assert status == "not_yet" and not any(c["passed"] for c in checks)
    assert readiness is not None
