"""Real SQL tests for held AI assessment results (pack F16): the student sees no result until
someone allowed to publish assessments releases it; templates choose the rule."""

import datetime as dt

import fastapi
import httpx
import pytest

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.api.routes import assessment_records, staff_tasks, summary_report_v2
from src.models.db.interview import Interview
from src.models.db.job_profile import JobProfile
from src.models.db.summary_report import SummaryReport
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.repository.crud.interview import InterviewCRUDRepository
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401

REPORT = {
    "reportId": "r-1",
    "reportVersion": "practice-report-v1",
    "reportStatus": "ready",
    "candidateInfo": {"interviewDate": "2026-10-30", "roleTopic": "Analyst"},
    "questionAnalysis": [],
    "finalTip": {"title": "Lead with the result", "description": "Then explain the query."},
    "speechFluencyFeedback": {
        "strengths": "Clear voice.",
        "areasOfImprovement": "Fewer pauses.",
        "ratingEmoji": "Good",
        "ratingTitle": "Good delivery",
        "ratingDescription": "Mostly fluent.",
    },
    "scoreSummary": {
        "knowledgeCompetence": {
            "score": 15, "maxScore": 25, "average": 3.0, "maxAverage": 5.0,
            "percentage": 60, "criteria": {},
        },
        "speechAndStructure": {
            "score": 14, "maxScore": 20, "average": 3.5, "maxAverage": 5.0,
            "percentage": 70, "criteria": {},
        },
    },
}


@pytest.fixture
async def held(db):
    _, factory, _ = db
    now = dt.datetime.now(dt.timezone.utc)
    async with factory() as s:
        users = {}
        for name, permissions in [
            ("publisher", {"interview_authoring": ["read", "update", "publish"], "analytics": ["read"]}),
            ("viewer", {"analytics": ["read"]}),
            ("student", {}),
        ]:
            user = User(
                email=f"{name}@release.test",
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
        team = Team(name="Assessments")
        s.add(team)
        await s.flush()
        for name in ("publisher", "viewer"):
            s.add(
                TeamMember(
                    team_id=team.id,
                    user_id=users[name],
                    access_role="team member",
                    purposes=["facilitator"],
                    course_ids=[],
                )
            )
        profile = JobProfile(
            job_name="Analyst",
            job_description="Analyse data and explain findings to people.",
            interview_mode="assessment",
            result_release="review",
            status="published",
        )
        s.add(profile)
        await s.flush()
        interview = Interview(
            user_id=users["student"],
            track="Analyst",
            course_id="course-a",
            interview_mode="assessment",
            job_profile_id=profile.id,
            result_release="review",
            status="completed",
            completed_at=now,
        )
        s.add(interview)
        await s.flush()
        s.add(SummaryReport(interview_id=interview.id, report_json=REPORT))
        await s.commit()
        ids = {"interview": interview.id, "profile": profile.id}
    app = fastapi.FastAPI()
    for module in (assessment_records, staff_tasks, summary_report_v2):
        app.include_router(module.router)

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
    app.dependency_overrides[get_current_user] = actor
    app.dependency_overrides[get_current_admin_user] = actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, factory, users, ids


def as_(who):
    return {"x-test-actor": who}


async def test_student_sees_review_notice_until_release(held):
    c, _, _, ids = held
    url = f"/v2/summary-report/{ids['interview']}"
    r = await c.get(url, headers=as_("student"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reportStatus"] == "needs_review"
    assert "being reviewed" in body["uncertaintyNotice"]
    assert "finalTip" not in body and "scoreSummary" not in body
    assert "Lead with the result" not in r.text

    record = (await c.get(f"/v2/assessment-records/ai/{ids['interview']}", headers=as_("publisher"))).json()
    assert record["result"]["held"] is True and record["can_release"] is True
    viewer = (await c.get(f"/v2/assessment-records/ai/{ids['interview']}", headers=as_("viewer"))).json()
    assert viewer["can_release"] is False

    reason = {"reason": "Checked the answers against the rubric."}
    release = f"/v2/assessment-records/ai/{ids['interview']}/release"
    assert (await c.post(release, json=reason, headers=as_("viewer"))).status_code == 403
    r = await c.post(release, json=reason, headers=as_("publisher"))
    assert r.status_code == 200, r.text
    assert (await c.post(release, json=reason, headers=as_("publisher"))).status_code == 409

    body = (await c.get(url, headers=as_("student"))).json()
    assert body["reportStatus"] == "ready"
    assert body["finalTip"]["title"] == "Lead with the result"
    record = (await c.get(f"/v2/assessment-records/ai/{ids['interview']}", headers=as_("viewer"))).json()
    assert [e["action"] for e in record["history"]] == ["assessment_result_released"]


async def test_overview_lists_held_results_for_releasers_only(held):
    c, _, _, ids = held
    tasks = (await c.get("/v2/staff/tasks", headers=as_("publisher"))).json()["tasks"]
    task = next(t for t in tasks if t["id"] == "assessment_results_to_release")
    assert task["count"] == 1
    assert task["action"]["href"] == f"/dashboard/samvaad/assessments/ai/{ids['interview']}"
    tasks = (await c.get("/v2/staff/tasks", headers=as_("viewer"))).json()["tasks"]
    assert all(t["id"] != "assessment_results_to_release" for t in tasks)


async def test_template_rule_needs_template_editing_and_is_frozen_per_sitting(held):
    c, factory, users, ids = held
    url = f"/v2/assessment-records/templates/{ids['profile']}/release-rule"
    body = {"result_release": "immediate", "reason": "Pilot cohort: share results straight away."}
    assert (await c.put(url, json=body, headers=as_("viewer"))).status_code == 403
    r = await c.put(url, json=body, headers=as_("publisher"))
    assert r.status_code == 200 and r.json()["result_release"] == "immediate"
    # The sitting already taken keeps the rule it started with.
    async with factory() as s:
        assert (await s.get(Interview, ids["interview"])).result_release == "review"
        # A sitting started now takes the new rule; flip it back to prove the copy.
        (await s.get(JobProfile, ids["profile"])).result_release = "review"
        await s.commit()
        fresh = await InterviewCRUDRepository(async_session=s).create_interview(
            user_id=users["student"],
            track="Analyst",
            difficulty="easy",
            job_profile_id=ids["profile"],
            course_id="course-a",
        )
        assert fresh.result_release == "review"


async def test_corrections_keep_every_version_and_reach_the_student(held):
    c, _, _, ids = held
    base = f"/v2/assessment-records/ai/{ids['interview']}"
    await c.post(f"{base}/release", json={"reason": "Checked the answers carefully."}, headers=as_("publisher"))
    fix = {
        "expected_version": 0,
        "knowledge": 72,
        "speech": 70,
        "reason": "Two answers were marked wrong by a transcription error.",
        "student_note": "Two answers were re-marked after a transcript check.",
    }
    assert (await c.post(f"{base}/corrections", json=fix, headers=as_("viewer"))).status_code == 403
    r = await c.post(f"{base}/corrections", json=fix, headers=as_("publisher"))
    assert r.status_code == 200 and r.json()["version"] == 1, r.text
    # A stale form cannot overwrite the newer version.
    assert (await c.post(f"{base}/corrections", json=fix, headers=as_("publisher"))).status_code == 409
    again = {**fix, "expected_version": 1, "knowledge": 75, "student_note": None,
             "reason": "Second reviewer agreed one more answer was correct."}
    assert (await c.post(f"{base}/corrections", json=again, headers=as_("publisher"))).status_code == 200

    student = (await c.get(f"/v2/summary-report/{ids['interview']}", headers=as_("student"))).json()
    kc = student["scoreSummary"]["knowledgeCompetence"]
    assert kc["percentage"] == 75 and kc["score"] == 19 and kc["average"] == 3.75
    assert student["scoreSummary"]["speechAndStructure"]["percentage"] == 70
    assert student["uncertaintyNotice"].startswith("Your score was updated after review.")
    # Speech stayed at 70 (still "Good"), so the AI's rating text is untouched.
    assert student["speechFluencyFeedback"]["ratingDescription"] == "Mostly fluent."

    record = (await c.get(base, headers=as_("viewer"))).json()
    assert [v["previous"] for v in record["corrections"]] == [
        {"knowledge": 60, "speech": 70},
        {"knowledge": 72, "speech": 70},
    ]
    assert record["result"]["scores"]["overall"] == 72.5
    assert record["can_correct"] is False
    assert [e["action"] for e in record["history"]][-2:] == [
        "assessment_score_corrected", "assessment_score_corrected",
    ]


def test_rating_label_follows_a_band_change():
    from src.services.result_release import corrected_report

    out = corrected_report(REPORT, 60, 45, None)
    assert out["speechFluencyFeedback"]["ratingEmoji"] == "Needs-Improvement"
    assert out["speechFluencyFeedback"]["ratingDescription"].endswith("45%.")
    assert REPORT["speechFluencyFeedback"]["ratingEmoji"] == "Good"
