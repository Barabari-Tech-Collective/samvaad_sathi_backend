"""Real SQL tests for the student's confirmed project context (pack F10)."""

import fastapi
import httpx
import pytest

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.api.routes import profile_context
from src.models.db.user import User
from src.services.learner_context import confirmed_text
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


@pytest.fixture
async def ctx(db):
    _, factory, _ = db
    async with factory() as s:
        users = {}
        for name in ("asha", "bilal"):
            u = User(email=f"{name}@ctx.test", name=name, password_hash="test")
            s.add(u)
            await s.flush()
            users[name] = u.id
        await s.commit()
    app = fastapi.FastAPI()
    app.include_router(profile_context.router)

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
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c, factory, users


def as_(who):
    return {"x-test-actor": who}


async def test_only_confirmed_items_shape_questions_and_edits_need_reconfirming(ctx):
    c, factory, users = ctx
    body = {"kind": "project", "title": "Attendance tracker",
            "summary": "A web app that records class attendance and sends weekly summaries to teachers."}
    item = (await c.post("/v2/profile-context/me", json=body, headers=as_("asha"))).json()
    assert item["status"] == "draft"
    async with factory() as s:
        assert await confirmed_text(s, users["asha"]) == ""
    item = (await c.post(f"/v2/profile-context/me/{item['id']}/confirm",
                         json={"expected_version": 1}, headers=as_("asha"))).json()
    assert item["status"] == "confirmed"
    async with factory() as s:
        text = await confirmed_text(s, users["asha"])
    assert "Attendance tracker" in text and text.startswith("Confirmed by the student:")
    # Editing sends it back to draft, and a stale version is refused.
    edit = {"title": "Attendance tracker v2", "summary": "Now also exports a monthly report for the principal.",
            "expected_version": 2}
    edited = (await c.put(f"/v2/profile-context/me/{item['id']}", json=edit, headers=as_("asha"))).json()
    assert edited["status"] == "draft" and edited["version"] == 3
    assert (await c.put(f"/v2/profile-context/me/{item['id']}", json=edit, headers=as_("asha"))).status_code == 409
    async with factory() as s:
        assert await confirmed_text(s, users["asha"]) == ""


async def test_context_is_private_and_limited(ctx):
    c, _, _ = ctx
    body = {"kind": "experience", "title": "Library volunteer",
            "summary": "Helped catalogue and issue books at my college library for a year."}
    item = (await c.post("/v2/profile-context/me", json=body, headers=as_("asha"))).json()
    assert (await c.get("/v2/profile-context/me", headers=as_("bilal"))).json()["items"] == []
    assert (await c.post(f"/v2/profile-context/me/{item['id']}/confirm", json={"expected_version": 1},
                         headers=as_("bilal"))).status_code == 404
    assert (await c.delete(f"/v2/profile-context/me/{item['id']}", headers=as_("bilal"))).status_code == 404
    for i in range(9):
        await c.post("/v2/profile-context/me", headers=as_("asha"),
                     json={**body, "title": f"Another {i}"})
    assert (await c.post("/v2/profile-context/me", json=body, headers=as_("asha"))).status_code == 409
    assert (await c.delete(f"/v2/profile-context/me/{item['id']}", headers=as_("asha"))).status_code == 204
