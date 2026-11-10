"""The Openings list counts, the one People list, and the learner's single next step."""

import fastapi
import httpx
import pytest
import sqlalchemy as sa
from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.api.routes import opportunities, staff_access, student_home
from src.models.db.opportunity import Opportunity
from src.models.db.user import User
from src.services import opportunity_selection as service

from .test_fixed_question_rounds import db, pytestmark  # noqa: F401
from .test_opportunity_selection import selection  # noqa: F401


@pytest.fixture
async def api(selection):  # noqa: F811
    _, factory, oid, users = selection
    app = fastapi.FastAPI()
    app.include_router(opportunities.router)
    app.include_router(staff_access.router)
    app.include_router(student_home.router)

    async def session_dep():
        async with factory() as s:
            try:
                yield s
            finally:
                await s.rollback()

    async def actor(request: fastapi.Request, session=fastapi.Depends(get_async_session)):
        return await session.get(User, users[request.headers.get("x-test-actor", "lead")])

    app.dependency_overrides[get_async_session] = session_dep
    app.dependency_overrides[get_current_user] = actor
    app.dependency_overrides[get_current_admin_user] = actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, factory, oid, users


async def test_openings_list_counts_match_the_opening_page(api):
    c, factory, oid, users = api
    board = await c.get("/v2/opportunities/board", headers={"x-test-actor": "lead"})
    assert board.status_code == 200
    row = next(i for i in board.json()["items"] if i["id"] == oid)
    async with factory() as s:
        opening = await s.get(Opportunity, oid)
        lead = await s.get(User, users["lead"])
        predicate = await opportunities.service.authorize(lead, opening.course_id, s, "read")
        view = await service.detail(s, lead, opening, await service.latest(s, oid), predicate)
    need = sum(1 for i in view["items"] if i["evidence"]["state"] != "ready")
    todo = sum(1 for i in view["items"] if i["evidence"]["state"] == "ready")
    assert row["people"] == len(view["items"]) == 1
    assert (row["need"], row["todo"]) == (need, todo)
    assert row["selection"] == view["status"]


async def test_board_query_count_does_not_grow_with_openings(api):
    _, factory, oid, users = api
    statements = []

    def count(_conn, _cursor, statement, *_rest):
        statements.append(statement)

    async with factory() as s:
        opening = await s.get(Opportunity, oid)
        lead = await s.get(User, users["lead"])
        predicate = await opportunities.service.authorize(lead, opening.course_id, s, "read")
        engine = (await s.connection()).engine.sync_engine
        sa.event.listen(engine, "before_cursor_execute", count)
        try:
            await service.board_counts(s, [opening], {opening.course_id: predicate})
            one = len(statements)
            statements.clear()
            await service.board_counts(s, [opening] * 20, {opening.course_id: predicate})
        finally:
            sa.event.remove(engine, "before_cursor_execute", count)
    assert len(statements) == one


async def test_people_list_is_admin_only_searchable_and_grouped(api, monkeypatch):
    c, _, _, users = api
    monkeypatch.setattr(staff_access, "is_admin_user", lambda u: u.email.startswith("lead@"))
    denied = await c.get("/v2/staff-access/people", headers={"x-test-actor": "member"})
    assert denied.status_code == 403
    every = await c.get("/v2/staff-access/people", headers={"x-test-actor": "lead"})
    assert every.status_code == 200
    body = every.json()
    assert {p["id"] for p in body["items"]} >= {users["lead"], users["member"]}
    assert users["student"] not in {p["id"] for p in body["items"]}
    member = next(p for p in body["items"] if p["id"] == users["member"])
    assert member["memberships"] and member["memberships"][0]["version"]
    assert "responsibilities" in body and "catalogue" in body
    found = await c.get("/v2/staff-access/people?q=MEMBER@sel", headers={"x-test-actor": "lead"})
    assert [p["id"] for p in found.json()["items"]] == [users["member"]]
    wild = await c.get("/v2/staff-access/people?q=%25", headers={"x-test-actor": "lead"})
    assert wild.json()["items"] == []


async def test_a_brand_new_learner_is_told_one_thing_to_do(api):
    c, _, _, _ = api
    today = await c.get("/v2/student-home/today", headers={"x-test-actor": "student"})
    assert today.status_code == 200
    action = today.json()["action"]
    assert action["href"].startswith("/")
    assert action["title"]
