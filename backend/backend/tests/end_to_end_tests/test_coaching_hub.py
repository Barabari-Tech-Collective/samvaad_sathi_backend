"""Real SQL tests for the Coaching page: scope, cards equal lists, five drills, reminders."""

import datetime as dt
import uuid

import fastapi
import httpx
import pytest
import sqlalchemy as sa

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.api.routes import coaching_assignments, coaching_hub, learner_hub
from src.models.db.coaching_assignment import CoachingAssignment
from src.models.db.interview import Interview
from src.models.db.notification import Notification
from src.models.db.pacing_practice import PacingPracticeSession
from src.models.db.summary_report import SummaryReport
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.services import learner_hub as hub
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401

NOW = dt.datetime.now(dt.timezone.utc)


def score(v):
    return {"overallScoreSummary": {"knowledgeCompetence": {"percentage": v}, "speechStructure": {"percentage": v - 10}}}


@pytest.fixture
async def ctx(db):
    _, factory, _ = db
    hub._cache.clear()
    async with factory() as s:
        await s.execute(sa.delete(User))
        users = {}
        for name, college, courses, policy, admin in [
            ("admin", None, [], None, True),
            ("coach", None, [], {"practice_coaching": ["read", "create", "update"], "course_workspace": ["read"]}, False),
            ("placement", None, [], {"course_workspace": ["read"]}, False),
            ("asha", "City College", ["course-a"], None, False),
            ("bilal", "City College", ["course-a"], None, False),
            ("chitra", "Hill College", ["course-b"], None, False),
        ]:
            u = User(email=f"{name}@c.test", name=name, password_hash="t", university=college, enrolled_course_ids=courses,
                     enrollment_source="sampark", enrollment_observed_at=NOW, feature_permissions=policy, is_admin=admin)
            s.add(u)
            await s.flush()
            users[name] = u.id
        team = Team(name="Programme")
        s.add(team)
        await s.flush()
        s.add(TeamMember(team_id=team.id, user_id=users["coach"], access_role="Sr Facilitator", course_ids=["course-a"]))
        for days, v in [(1, 40), (2, 42)]:
            iv = Interview(user_id=users["bilal"], track="Dev", interview_mode="practice", status="completed",
                           created_at=NOW - dt.timedelta(days=days), completed_at=NOW - dt.timedelta(days=days))
            s.add(iv)
            await s.flush()
            s.add(SummaryReport(interview_id=iv.id, report_json=score(v)))
        await s.commit()
    app = fastapi.FastAPI()
    for r in (coaching_hub.router, learner_hub.router, coaching_assignments.router):
        app.include_router(r)

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
    app.dependency_overrides[get_current_user] = actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, users, factory


def as_(w):
    return {"x-test-actor": w}


async def assign(c, users, module="pace", due=None, who="bilal"):
    r = await c.post("/v2/learner-hub/actions/assign", headers=as_("admin"), json={
        "selection": {"ids": [users[who]]}, "course_id": "course-a", "request_key": str(uuid.uuid4()), "kind": "drill",
        "module": module, "reason": "This will help you pace answers.", "due_date": due,
    })
    assert r.status_code == 200, r.text
    assert r.json()["done"], r.text


async def test_need_card_suggests_from_weaker_side_and_counts_match(ctx):
    c, users, _ = ctx
    r = (await c.get("/v2/coaching-hub/learners?card=need", headers=as_("admin"))).json()
    assert r["counts"]["need"] == r["total"] == len(r["rows"])
    bilal = next(x for x in r["rows"] if x["id"] == users["bilal"])
    assert bilal["suggest"]["module"] == "pace" and "Speech" in bilal["suggest"]["why"]
    assert r["rows"][0]["status"] == "low"  # people we can help first
    assert r["can"]["coach"] is True and r["scope"]


async def test_scope_limits_coach_to_their_course(ctx):
    c, users, _ = ctx
    r = (await c.get("/v2/coaching-hub/learners?card=need&sub=every", headers=as_("coach"))).json()
    assert users["chitra"] not in {x["id"] for x in r["rows"]}
    assert users["asha"] in {x["id"] for x in r["rows"]}


async def test_all_five_drills_assign_and_leave_need_list(ctx):
    c, users, _ = ctx
    for m in ("warm_up", "think_fast", "pronunciation", "structure", "pace"):
        await assign(c, users, m)
    r = (await c.get("/v2/coaching-hub/learners?card=open", headers=as_("admin"))).json()
    assert r["counts"]["open"] == 5 == r["total"]
    assert {x["module"] for x in r["rows"]} == {"warm_up", "think_fast", "pronunciation", "structure", "pace"}
    need = (await c.get("/v2/coaching-hub/learners?card=need", headers=as_("admin"))).json()
    assert users["bilal"] not in {x["id"] for x in need["rows"]}
    drills = (await c.get("/v2/coaching-hub/drills", headers=as_("admin"))).json()
    assert all(d["open"] == 1 for d in drills["items"])


async def test_remind_once_and_only_in_scope(ctx):
    c, users, factory = ctx
    await assign(c, users)
    key = (await c.get("/v2/coaching-hub/learners?card=open", headers=as_("admin"))).json()["rows"][0]["key"]
    assert (await c.post("/v2/coaching-hub/remind", headers=as_("admin"), json={"keys": [key]})).json()["sent"] == [key]
    again = (await c.post("/v2/coaching-hub/remind", headers=as_("admin"), json={"keys": [key]})).json()
    assert again["sent"] == [] and again["skipped"]
    async with factory() as s:
        assert (await s.execute(sa.select(sa.func.count()).select_from(Notification).where(Notification.kind == "coaching_reminder"))).scalar_one() == 1
    row = (await c.get("/v2/coaching-hub/learners?card=open", headers=as_("admin"))).json()["rows"][0]
    assert row["reminded"] is True
    assert (await c.post("/v2/coaching-hub/remind", headers=as_("placement"), json={"keys": [key]})).status_code == 403


async def test_overdue_card_and_learner_page(ctx):
    c, users, factory = ctx
    await assign(c, users)
    async with factory() as s:
        await s.execute(sa.update(CoachingAssignment).values(due_date=(NOW - dt.timedelta(days=3)).date()))
        await s.commit()
    r = (await c.get("/v2/coaching-hub/learners?card=overdue", headers=as_("admin"))).json()
    assert r["counts"]["overdue"] == 1 and r["rows"][0]["due_days"] == -3
    page = (await c.get(f"/v2/coaching-hub/learners/{users['bilal']}/coaching", headers=as_("admin"))).json()
    assert page["items"][0]["type"] == "drill"
    assert (await c.get(f"/v2/coaching-hub/learners/{users['chitra']}/coaching", headers=as_("coach"))).status_code == 404


async def test_session_drill_finishes_only_with_own_finished_session(ctx):
    c, users, factory = ctx
    await assign(c, users, "pace")
    async with factory() as s:
        row = (await s.execute(sa.select(CoachingAssignment))).scalar_one()
        aid = row.id
        columns = PacingPracticeSession.__table__.columns
        values = {"user_id": users["bilal"], "status": "completed"}
        for col in columns:
            if col.name not in values and not col.nullable and col.server_default is None and col.default is None and not col.primary_key:
                values[col.name] = {"INTEGER": 1, "VARCHAR": "x", "TEXT": "x", "JSONB": {}, "JSON": {}, "FLOAT": 1.0}.get(str(col.type).split("(")[0], "x")
        done = PacingPracticeSession(**values)
        other = PacingPracticeSession(**{**values, "user_id": users["asha"]})
        s.add_all([done, other])
        await s.commit()
        done_id, other_id = done.id, other.id
    bad = await c.post(f"/v2/coaching-assignments/me/{aid}/drill-session", headers=as_("bilal"), json={"session_id": other_id})
    assert bad.status_code == 409
    ok = await c.post(f"/v2/coaching-assignments/me/{aid}/drill-session", headers=as_("bilal"), json={"session_id": done_id})
    assert ok.status_code == 200 and ok.json()["status"] == "completed"
    r = (await c.get("/v2/coaching-hub/learners?card=done", headers=as_("admin"))).json()
    assert r["counts"]["done"] == 1


async def test_practice_board_numbers_and_signals(ctx):
    c, users, factory = ctx
    async with factory() as s:
        s.add(Interview(user_id=users["asha"], track="Dev", interview_mode="practice", status="failed", created_at=NOW - dt.timedelta(hours=3)))
        s.add(Interview(user_id=users["asha"], track="Dev", interview_mode="practice", status="active", created_at=NOW - dt.timedelta(hours=3)))
        await s.commit()
    r = (await c.get("/v2/coaching-hub/practice", headers=as_("admin"))).json()
    assert r["numbers"]["blocked"] == 1 and r["numbers"]["can_continue"] == 1 and r["numbers"]["completed"] == 2
    assert r["total"] == 2 and all(i["guidance"] for i in r["items"])
    only = (await c.get("/v2/coaching-hub/practice?signal=session_blocked", headers=as_("admin"))).json()
    assert only["total"] == 1
