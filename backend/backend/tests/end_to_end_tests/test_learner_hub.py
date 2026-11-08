"""Real SQL tests for the Learners hub: scope first, then every figure, list and action."""

import csv
import datetime as dt
import io
import uuid

import fastapi
import httpx
import pytest
import sqlalchemy as sa

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes import learner_hub
from src.models.db.assessment_assignment import AssessmentAssignment
from src.models.db.interview import Interview
from src.models.db.job_profile import JobProfile
from src.models.db.notification import Notification
from src.models.db.practice_drill_completion import PracticeDrillCompletion  # noqa: F401
from src.models.db.coaching_assignment import CoachingAssignment
from src.models.db.student_placement import StudentPlacement
from src.models.db.summary_report import SummaryReport
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.services import learner_hub as hub
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


NOW = dt.datetime.now(dt.timezone.utc)
DRILL = {"module": "warm_up", "level": "easy", "evidence_kind": "completed", "evidence_units": 1}


def score(value):
    return {"overallScoreSummary": {"knowledgeCompetence": {"percentage": value}, "speechStructure": {"percentage": value}}}


@pytest.fixture
async def hub_client(db):
    _, factory, _ = db
    hub._cache.clear()
    async with factory() as s:
        await s.execute(sa.delete(User))
        users = {}
        people = [
            ("admin", None, [], None, True),
            ("lead", None, [], None, False),
            ("junior", None, [], None, False),
            ("viewer", None, [], None, False),
            ("coach", None, [], {"practice_coaching": ["read", "create"], "course_workspace": ["read", "export"], "analytics": ["read"]}, False),
            ("asha", "City College", ["course-a"], None, False),
            ("bilal", "  city  college ", ["course-a"], None, False),
            ("chitra", "Hill College", ["course-b"], None, False),
            ("dev", None, [], None, False),
            ("=evil", "City College", ["course-a"], None, False),
        ]
        for name, college, courses, policy, admin in people:
            user = User(
                email=f"{name.strip('=')}@hub.test", name=name, password_hash="test", university=college,
                enrolled_course_ids=courses, enrollment_source="sampark", enrollment_observed_at=NOW,
                feature_permissions=policy, is_admin=admin,
            )
            s.add(user)
            await s.flush()
            users[name] = user.id
        team = Team(name="Programme")
        s.add(team)
        await s.flush()
        for name, role, purposes, colleges, course_ids in [
            ("lead", "Lead", None, None, None),
            ("junior", "Jr Facilitator", None, ["City College"], None),
            ("viewer", "Team Member", [], None, None),
            ("coach", "Sr Facilitator", None, None, ["course-a"]),
        ]:
            s.add(TeamMember(team_id=team.id, user_id=users[name], access_role=role, purposes=purposes, colleges=colleges, course_ids=course_ids))
        s.add(StudentPlacement(student_id=users["asha"], batch_key="a-1", batch_name="Batch A", course_id="course-a", course_name="Data Science"))
        # asha: active and doing well. chitra: last seen 20 days ago with two low scores.
        for who, days, value, mode in [("asha", 1, 80, "assessment"), ("chitra", 20, 40, "practice"), ("chitra", 25, 42, "practice")]:
            iv = Interview(
                user_id=users[who], track="Dev", course_id=None, interview_mode=mode, status="completed",
                created_at=NOW - dt.timedelta(days=days), completed_at=NOW - dt.timedelta(days=days),
            )
            s.add(iv)
            await s.flush()
            s.add(SummaryReport(interview_id=iv.id, report_json=score(value)))
        await s.commit()
    app = fastapi.FastAPI()
    app.include_router(learner_hub.router)

    async def session_dep():
        async with factory() as s:
            try:
                yield s
            finally:
                await s.rollback()

    async def actor(request: fastapi.Request, session=fastapi.Depends(get_async_session)):
        hub._cache.clear()
        return await session.get(User, users[request.headers["x-test-actor"]])

    app.dependency_overrides[get_async_session] = session_dep
    app.dependency_overrides[get_current_admin_user] = actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, users, factory


def as_(who):
    return {"x-test-actor": who}


async def get(c, path, who="admin", **params):
    r = await c.get(f"/v2/learner-hub/{path}", headers=as_(who), params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def test_scope_decides_everything(hub_client):
    c, users, _ = hub_client
    everyone = await get(c, "learners", limit=100)
    assert everyone["total"] == 5  # staff never count as learners
    # A team member with no tags is refused here too, not only hidden from a menu.
    for path in ("summary", "groups", "learners"):
        assert (await c.get(f"/v2/learner-hub/{path}", headers=as_("viewer"))).status_code == 403
    junior = await get(c, "learners", "junior", limit=100)
    assert {i["name"] for i in junior["items"]} == {"asha", "bilal", "=evil"}
    coach = await get(c, "learners", "coach", limit=100)
    assert {i["name"] for i in coach["items"]} == {"asha", "bilal", "=evil"}


async def test_cannot_select_a_learner_outside_scope(hub_client):
    c, users, factory = hub_client
    r = await c.post(
        "/v2/learner-hub/actions/nudge", headers=as_("coach"),
        json={"selection": {"ids": [users["chitra"], users["asha"]]}},
    )
    assert r.status_code == 200, r.text
    assert [d["name"] for d in r.json()["done"]] == ["asha"]
    async with factory() as s:
        got = (await s.execute(sa.select(Notification.recipient_id))).scalars().all()
    assert got == [users["asha"]]


async def test_colleges_group_spelling_and_put_worst_first(hub_client):
    c, *_ = hub_client
    data = await get(c, "groups", of="colleges")
    rows = {r["key"]: r for r in data["items"]}
    assert rows["city college"]["learners"] == 3 and rows["city college"]["label"] == "City College"
    assert rows[hub.NOT_SET]["label"] == "College not set"
    shares = [r["need_share"] for r in data["items"]]
    assert shares == sorted(shares, reverse=True)


async def test_batches_never_count_the_no_batch_group(hub_client):
    c, *_ = hub_client
    data = await get(c, "groups", of="batches", course="course-a")
    assert [r["label"] for r in data["items"]] == ["Batch A"]
    assert data["no_batch"] == 2
    none = await get(c, "learners", course="course-a", batch="none", limit=100)
    assert {i["name"] for i in none["items"]} == {"bilal", "=evil"}


async def test_status_figures_and_oldest_first(hub_client):
    c, *_ = hub_client
    items = {i["name"]: i for i in (await get(c, "learners", limit=100))["items"]}
    assert items["bilal"]["status"] == "never" and items["bilal"]["last_active_days"] is None
    assert items["chitra"]["status"] == "idle" and items["asha"]["status"] == "ok"
    summary = await get(c, "summary", days=7)
    ages = [a["oldest_days"] for a in summary["actions"]]
    assert ages == sorted(ages, reverse=True)
    assert summary["metrics"]["assessments"]["value"] == 1
    assert summary["metrics"]["average_score"]["value"] is None  # one attempt is too few to average
    assert next(a for a in summary["actions"] if a["id"] == "idle")["overdue"] is True
    assert summary["funnel"][0]["count"] == 5


async def test_periods_are_real(hub_client):
    c, *_ = hub_client
    for days in (1, 3, 7, 15, 30):
        data = await get(c, "summary", days=days)
        assert len(data["metrics"]["active"]["series"]) == days
    assert (await c.get("/v2/learner-hub/summary", headers=as_("admin"), params={"days": 2})).status_code == 422
    assert (await get(c, "summary", days=30))["metrics"]["active"]["value"] == 2
    assert (await get(c, "summary", days=7))["metrics"]["active"]["value"] == 1


async def test_low_score_needs_recent_activity_and_two_scores(hub_client):
    c, users, factory = hub_client
    async with factory() as s:
        iv = Interview(user_id=users["chitra"], track="Dev", interview_mode="practice", status="in_progress", created_at=NOW)
        s.add(iv)
        await s.commit()
    items = {i["name"]: i for i in (await get(c, "learners", limit=100))["items"]}
    assert items["chitra"]["status"] == "low"


async def test_filters_narrow_each_other_and_page(hub_client):
    c, *_ = hub_client
    data = await get(c, "learners", filter_college="hill college", limit=100)
    assert [i["name"] for i in data["items"]] == ["chitra"]
    assert [o["value"] for o in data["options"]["courses"]] == ["course-b"]
    assert len(data["options"]["colleges"]) == 3  # a filter never hides its own alternatives
    first = await get(c, "learners", limit=2, page=1, sort="name")
    second = await get(c, "learners", limit=2, page=2, sort="name")
    assert first["total"] == 5 and not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    assert (await get(c, "learners", q="zzz"))["total"] == 0


async def test_select_all_matching_honours_exclusions(hub_client):
    c, users, _ = hub_client
    r = await c.post(
        "/v2/learner-hub/actions/nudge", headers=as_("admin"),
        json={"selection": {"all_matching": True, "status": "never", "exclude": [users["dev"]]}},
    )
    assert r.status_code == 200, r.text
    assert {d["name"] for d in r.json()["done"]} == {"bilal", "=evil"}
    again = await c.post(
        "/v2/learner-hub/actions/nudge", headers=as_("admin"),
        json={"selection": {"ids": [users["bilal"]]}},
    )
    assert again.json()["done"] == [] and "Already reminded" in again.json()["skipped"][0]["reason"]


async def test_bulk_assign_is_real_and_skips_people_outside_the_course(hub_client):
    c, users, factory = hub_client
    key = str(uuid.uuid4())
    body = {
        "selection": {"ids": [users["asha"], users["chitra"]]}, "course_id": "course-a", "request_key": key,
        "kind": "drill", "module": "warm_up", "reason": "Needs a short warm up today.",
    }
    r = await c.post("/v2/learner-hub/actions/assign", headers=as_("admin"), json=body)
    assert r.status_code == 200, r.text
    assert [d["name"] for d in r.json()["done"]] == ["asha"]
    assert r.json()["skipped"][0]["reason"] == "Not enrolled in this course."
    again = await c.post("/v2/learner-hub/actions/assign", headers=as_("admin"), json=body)
    assert again.status_code == 200
    async with factory() as s:
        n = (await s.execute(sa.select(sa.func.count(CoachingAssignment.id)))).scalar_one()
    assert n == 1  # retrying never doubles it


async def test_export_is_scoped_audited_and_formula_safe(hub_client):
    c, users, _ = hub_client
    denied = await c.post(
        "/v2/learner-hub/export", headers=as_("lead"),
        json={"selection": {"all_matching": True}, "reason": "Monthly programme review."},
    )
    assert denied.status_code == 403
    r = await c.post(
        "/v2/learner-hub/export", headers=as_("coach"),
        json={"selection": {"all_matching": True}, "reason": "Monthly programme review."},
    )
    assert r.status_code == 200, r.text
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
    names = {row[1] for row in rows[1:]}
    assert names == {"asha", "bilal", "'=evil"}


async def test_single_scope_staff_start_inside_their_college(hub_client):
    c, *_ = hub_client
    data = await get(c, "summary", "junior")
    assert data["level"]["college"]["label"] == "City College" and data["root"] is True
    assert data["lens"] == "program"
    assert data["weak_skills"] is not None


async def test_select_all_means_the_list_shown_and_reports_people_outside_the_course(hub_client):
    c, users, _ = hub_client
    body = {
        "selection": {"all_matching": True, "status": "never"}, "course_id": "course-a",
        "request_key": str(uuid.uuid4()), "kind": "drill", "module": "warm_up", "reason": "Needs a short warm up today.",
    }
    r = await c.post("/v2/learner-hub/actions/assign", headers=as_("admin"), json=body)
    assert r.status_code == 200, r.text
    assert {d["name"] for d in r.json()["done"]} == {"bilal", "=evil"}
    assert {s["name"]: s["reason"] for s in r.json()["skipped"]} == {
        "dev": "Not enrolled in this course.", "Round Student": "Not enrolled in this course.",
    } or {s["name"] for s in r.json()["skipped"]} == {"dev"}


async def test_ready_uses_the_course_rule_and_put_forward_leaves_the_queue(hub_client):
    from src.models.db.opportunity import Opportunity, OpportunityCandidate

    c, users, factory = hub_client
    async with factory() as s:
        for days in (1, 2, 3):
            iv = Interview(
                user_id=users["asha"], track="Dev", course_id="course-a", interview_mode="practice",
                status="completed", created_at=NOW - dt.timedelta(days=days), completed_at=NOW - dt.timedelta(days=days),
            )
            s.add(iv)
            await s.flush()
            s.add(SummaryReport(interview_id=iv.id, report_json={"overallScoreSummary": {"overallPercentage": 80}}))
        for n in range(5):
            s.add(PracticeDrillCompletion(user_id=users["asha"], completed_at=NOW - dt.timedelta(days=1), client_completion_id=str(uuid.uuid4()), **DRILL))
        await s.commit()
    items = {i["name"]: i for i in (await get(c, "learners", limit=100))["items"]}
    assert items["asha"]["status"] == "ready"
    summary = await get(c, "summary")
    assert next(a for a in summary["actions"] if a["id"] == "ready")["count"] == 1
    assert summary["funnel"][3]["count"] == 1
    async with factory() as s:
        row = Opportunity(course_id="course-a", title="Intern", organisation="Acme", created_by=users["admin"], request_key=str(uuid.uuid4()), request_hash="h")
        s.add(row)
        await s.flush()
        s.add(OpportunityCandidate(opportunity_id=row.id, student_id=users["asha"], added_by=users["admin"]))
        await s.commit()
    summary = await get(c, "summary")
    assert not [a for a in summary["actions"] if a["id"] == "ready"]
    assert (await get(c, "learners", status="ready_open"))["total"] == 0
    assert (await get(c, "learners", status="ready"))["total"] == 1


async def test_merged_spellings_become_one_college_and_keep_staff_scope(hub_client):
    c, users, factory = hub_client
    async with factory() as s:
        s.add(User(email="clg@hub.test", name="clg", password_hash="t", university="City Clg", enrolled_course_ids=["course-a"], enrollment_source="sampark", enrollment_observed_at=NOW))
        await s.commit()
    data = (await c.get("/v2/learner-hub/colleges/spellings", headers=as_("admin"))).json()
    assert any({x["key"] for x in g["spellings"]} >= {"city clg", "city college"} for g in data["suggestions"])
    assert (await c.get("/v2/learner-hub/colleges/spellings", headers=as_("lead"))).status_code == 403
    r = await c.post("/v2/learner-hub/colleges/merge", headers=as_("admin"), json={"spellings": ["city clg"], "label": "City College"})
    assert r.status_code == 200, r.text
    rows = {g["key"]: g for g in (await get(c, "groups", of="colleges"))["items"]}
    assert "city clg" not in rows and rows["city college"]["learners"] == 4
    assert "clg" in {i["name"] for i in (await get(c, "learners", "junior", limit=100))["items"]}
    assert (await c.delete("/v2/learner-hub/colleges/merge/city clg", headers=as_("admin"))).status_code == 200
    assert "city clg" in {g["key"] for g in (await get(c, "groups", of="colleges"))["items"]}


async def test_set_college_and_import_sheet_college(hub_client):
    c, users, factory = hub_client
    r = await c.post("/v2/learner-hub/import-colleges", headers=as_("admin"), json={"rows": [{"email": "DEV@hub.test", "college": "Hill College"}]})
    assert r.status_code == 200, r.text
    items = {i["name"]: i for i in (await get(c, "learners", limit=100))["items"]}
    assert items["dev"]["college"] == "Hill College"
    denied = await c.post("/v2/learner-hub/actions/set-college", headers=as_("junior"), json={"selection": {"ids": [users["bilal"]]}, "college": "Hill College"})
    assert denied.status_code == 403
    done = await c.post("/v2/learner-hub/actions/set-college", headers=as_("admin"), json={"selection": {"ids": [users["bilal"]]}, "college": "  Hill   College "})
    assert done.status_code == 200, done.text
    async with factory() as s:
        assert (await s.get(User, users["bilal"])).university == "Hill College"


async def test_nudge_queues_one_email_and_dummy_addresses_never_send(hub_client):
    from src.models.db.notification import NotificationDelivery
    from src.services import notifications

    c, users, factory = hub_client
    await c.post("/v2/learner-hub/actions/nudge", headers=as_("admin"), json={"selection": {"ids": [users["asha"]]}, "message": "Try one today."})
    sent = []

    def send(event, payload):
        sent.append((event, payload))
        return "sent"

    async with factory() as s:
        counts = await notifications.dispatch_due(s, send=send)
        await s.commit()
        row = (await s.execute(sa.select(NotificationDelivery))).scalar_one()
    assert counts["sent"] == 1 and row.status == "sent"
    assert sent[0][0] == "nudge" and sent[0][1]["to"] == "asha@hub.test" and sent[0][1]["message"] == "Try one today."
    assert notifications.send_now("nudge", {"to": "x@demo.samvaad.invalid"}) == "skipped"


async def test_learner_detail_is_scoped(hub_client):
    c, users, _ = hub_client
    assert (await c.get(f"/v2/learner-hub/learners/{users['chitra']}", headers=as_("junior"))).status_code == 404
    data = await get(c, f"learners/{users['asha']}", "junior")
    assert data["assessments"] == 1 and data["status"] == "ok"


async def test_old_analytics_refuses_pages_the_person_does_not_have(hub_client):
    from src.services.course_access import require_analytics_area

    _, users, factory = hub_client
    async with factory() as s:
        viewer = await s.get(User, users["viewer"])
        lead = await s.get(User, users["lead"])
        for path in ("/v2/analytics/students", "/v2/analytics/dashboard/pulse", "/v2/analytics/colleges", "/v2/analytics/system"):
            with pytest.raises(fastapi.HTTPException):
                await require_analytics_area(path, viewer, s)
        await require_analytics_area("/v2/analytics/students", lead, s)
        viewer.feature_permissions = {"analytics": ["read"]}
        await require_analytics_area("/v2/analytics/students", viewer, s)


def test_seed_refuses_anything_but_staging():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).parents[2] / "scripts" / "seed_learner_hub_demo.py"
    spec = importlib.util.spec_from_file_location("demo_seed", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(SystemExit):
        module.refuse_unless_staging(sa.engine.make_url("postgresql+asyncpg://u:p@prod.example.com/samvaad"))
    with pytest.raises(SystemExit):
        module.refuse_unless_staging(sa.engine.make_url("postgresql+asyncpg://u:p@localhost/samvaad_saathi"))
    module.refuse_unless_staging(sa.engine.make_url("postgresql+asyncpg://u:p@localhost/ss_count_tests"))


async def _attempt(s, uid, value, age, role="Dev", level="medium", mode="assessment", speech=None):
    iv = Interview(
        user_id=uid, track=role, course_id=None, interview_mode=mode, status="completed", difficulty=level,
        created_at=NOW - dt.timedelta(days=age), completed_at=NOW - dt.timedelta(days=age),
    )
    s.add(iv)
    await s.flush()
    report = score(value)
    if speech is not None:
        report["overallScoreSummary"]["speechStructure"] = {"percentage": speech}
    s.add(SummaryReport(interview_id=iv.id, report_json=report))


async def test_active_counts_drills_as_well_as_interviews(hub_client):
    c, users, factory = hub_client
    async with factory() as s:
        s.add(PracticeDrillCompletion(user_id=users["bilal"], client_completion_id="d1", completed_at=NOW - dt.timedelta(days=1), **DRILL))
        await s.commit()
    summary = await get(c, "summary", days=7)
    assert summary["metrics"]["active"]["value"] == 2
    assert {i["name"] for i in (await get(c, "learners", days=7, status="active", limit=100))["items"]} == {"asha", "bilal"}
    assert "chitra" in {i["name"] for i in (await get(c, "learners", days=7, status="inactive", limit=100))["items"]}


async def test_average_stays_blank_until_there_is_enough_evidence(hub_client):
    c, users, factory = hub_client
    first = await get(c, "summary", days=7)
    assert first["metrics"]["average_score"]["value"] is None and first["outcomes"]["scored_attempts"] == 1
    assert first["metrics"]["average_score"]["series"][-2] == 80  # the daily line still draws
    async with factory() as s:
        for n in range(4):
            await _attempt(s, users["bilal"], 60, n + 1)
        await s.commit()
    assert (await get(c, "summary", days=7))["metrics"]["average_score"]["value"] == 64


async def test_improving_only_compares_same_role_and_level(hub_client):
    c, users, factory = hub_client
    async with factory() as s:
        await _attempt(s, users["bilal"], 50, 10)
        await _attempt(s, users["bilal"], 60, 2)
        # A much higher score at another level must not count as improvement.
        await _attempt(s, users["dev"], 50, 10, level="hard")
        await _attempt(s, users["dev"], 90, 2, level="easy")
        await s.commit()
    rows = {i["name"]: i for i in (await get(c, "learners", limit=100))["items"]}
    assert rows["bilal"]["improvement"] == 10 and rows["dev"]["improvement"] is None
    summary = await get(c, "summary", days=7)
    assert summary["outcomes"]["improving"]["compared"] == 2  # bilal, and chitra's two earlier mocks
    assert summary["outcomes"]["improving"]["share"] is None  # one learner is not a trend
    assert [i["name"] for i in (await get(c, "learners", status="improving", limit=100))["items"]] == ["bilal"]


async def test_low_twice_compares_like_with_like(hub_client):
    c, users, factory = hub_client
    async with factory() as s:
        await _attempt(s, users["bilal"], 40, 1, level="hard")
        await _attempt(s, users["bilal"], 80, 3, level="easy")
        await _attempt(s, users["bilal"], 45, 5, level="hard")
        await s.commit()
    assert (await get(c, "learners", status="low", limit=100))["items"][0]["name"] == "bilal"


async def test_bands_journey_and_their_lists_agree(hub_client):
    c, users, factory = hub_client
    async with factory() as s:
        await _attempt(s, users["bilal"], 65, 2)
        await _attempt(s, users["dev"], 45, 2)
        await s.commit()
    summary = await get(c, "summary", days=7)
    for band in summary["outcomes"]["bands"]:
        assert (await get(c, "learners", status=band["status"], limit=100))["total"] == band["count"]
    for step in summary["funnel"]:
        assert (await get(c, "learners", status=step["status"], limit=100))["total"] == step["count"]
    assert [b["count"] for b in summary["outcomes"]["bands"]] == [2, 1, 1]
    assert [f["label"] for f in summary["funnel"]][-1] == "Put forward"


async def test_assignments_and_coaching_finished(hub_client):
    c, users, factory = hub_client
    async with factory() as s:
        profile = JobProfile(job_name="Role", job_description="x" * 30, status="published", interview_mode="assessment")
        s.add(profile)
        await s.flush()
        for n, name in enumerate(["asha", "bilal", "dev", "chitra", "asha"]):
            s.add(AssessmentAssignment(
                student_id=users[name], assigned_by=users["admin"], course_id=f"c{n}", job_profile_id=profile.id, pinned_revision=0,
                difficulty="medium", opens_at=NOW - dt.timedelta(days=5), closes_at=NOW - dt.timedelta(days=1),
                request_key=str(uuid.uuid4()), status="completed" if n < 3 else "assigned", version=1,
            ))
        s.add(CoachingAssignment(student_id=users["asha"], assigned_by=users["admin"], course_id="course-a", module="warm_up", reason="Needs a warm up.", due_date=(NOW - dt.timedelta(days=1)).date(), request_key=str(uuid.uuid4()), request_hash="x", status="assigned", version=1))
        await s.commit()
    o = (await get(c, "summary", days=7))["outcomes"]
    assert o["assignments"] == {"total": 5, "finished": 3, "share": 60}
    assert o["coaching"] == {"total": 1, "finished": 0, "share": None}
    missed = await get(c, "learners", days=7, status="missed_assignment", limit=100)
    assert {i["name"] for i in missed["items"]} == {"chitra", "asha"}
    assert [i["name"] for i in (await get(c, "learners", days=7, status="coaching_overdue", limit=100))["items"]] == ["asha"]


async def test_knowledge_and_speech_need_enough_learners(hub_client):
    c, users, factory = hub_client
    async with factory() as s:
        for name in ("asha", "bilal", "chitra", "dev"):
            await _attempt(s, users[name], 70, 1, speech=50)
        await s.commit()
    skills = (await get(c, "summary", days=7))["outcomes"]["skills"]
    assert skills == {"learners": 4, "knowledge": None, "speech": None}
    async with factory() as s:
        await _attempt(s, users["=evil"], 70, 1, speech=50)
        await s.commit()
    skills = (await get(c, "summary", days=7))["outcomes"]["skills"]
    assert (skills["knowledge"], skills["speech"]) == (70, 50)
