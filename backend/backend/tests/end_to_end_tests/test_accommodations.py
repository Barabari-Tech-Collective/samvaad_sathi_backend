"""Real SQL tests for accommodation requests (pack F13): private, restricted, minimal disclosure."""

import uuid

import fastapi
import httpx
import pytest

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.api.routes import accommodations, notifications as notification_routes
from src.models.db.user import User
from src.services.accommodations import confirmed_conditions
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


@pytest.fixture
async def world(db):
    _, factory, _ = db
    async with factory() as s:
        users = {}
        for name, kwargs in [
            ("asha", {}),
            ("bilal", {}),
            ("owner", {"feature_permissions": {"accommodations": ["read", "update"]}}),
            ("viewer", {"feature_permissions": {"accommodations": ["read"]}}),
            ("admin", {"is_admin": True}),
        ]:
            u = User(email=f"{name}@acc.test", name=name, password_hash="test", **kwargs)
            s.add(u)
            await s.flush()
            users[name] = u.id
        await s.commit()
    app = fastapi.FastAPI()
    app.include_router(accommodations.router)
    app.include_router(notification_routes.router)

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
        yield c, factory, users


def as_(who):
    return {"x-test-actor": who}


async def test_request_decide_confirm_and_minimal_disclosure(world):
    c, factory, users = world
    body = {"request_key": str(uuid.uuid4()),
            "description": "I have a stammer and need more time to answer spoken questions."}
    r = await c.post("/v2/accommodations/me", json=body, headers=as_("asha"))
    assert r.status_code == 201, r.text
    request = r.json()
    assert (await c.post("/v2/accommodations/me", json=body, headers=as_("asha"))).status_code == 200
    # Private: another student, a read-only holder and even an administrator without the
    # permission cannot see the queue, and only an update holder can decide.
    assert (await c.get("/v2/accommodations", headers=as_("admin"))).status_code == 403
    queue = (await c.get("/v2/accommodations", headers=as_("viewer"))).json()
    assert queue["can_decide"] is False and len(queue["items"]) == 1
    decide = {"expected_version": 1, "decision": "approve", "reason": "Supported by the college letter on file.",
              "conditions": ["extra_time_50", "rest_breaks"]}
    assert (await c.post(f"/v2/accommodations/{request['id']}/decide", json=decide, headers=as_("viewer"))).status_code == 403
    assert (await c.post(f"/v2/accommodations/{request['id']}/decide", json={**decide, "conditions": ["unlimited"]},
                         headers=as_("owner"))).status_code == 422
    r = await c.post(f"/v2/accommodations/{request['id']}/decide", json=decide, headers=as_("owner"))
    assert r.status_code == 200 and r.json()["status"] == "approved"
    # Approved but not confirmed: nothing is disclosed or applied yet.
    async with factory() as s:
        assert await confirmed_conditions(s, users["asha"]) == []
    mine = (await c.get("/v2/accommodations/me", headers=as_("asha"))).json()["items"][0]
    assert [x["label"] for x in mine["conditions"]] == ["50% extra time", "Short rest breaks"]
    assert (await c.post(f"/v2/accommodations/me/{request['id']}/confirm", json={"expected_version": 2},
                         headers=as_("bilal"))).status_code == 404
    assert (await c.post(f"/v2/accommodations/me/{request['id']}/confirm", json={"expected_version": 2},
                         headers=as_("asha"))).status_code == 200
    async with factory() as s:
        shown = await confirmed_conditions(s, users["asha"])
    # Only labels leave: never the description or the reason.
    assert [x["code"] for x in shown] == ["extra_time_50", "rest_breaks"]
    assert "stammer" not in str(shown) and "college letter" not in str(shown)
    told = (await c.get("/v2/notifications/me", headers=as_("asha"))).json()["items"][0]
    assert told["title"] == "Your support request has an answer" and told["body"] is None


async def test_withdrawing_removes_the_conditions(world):
    c, factory, users = world
    body = {"request_key": str(uuid.uuid4()), "description": "I need the instructions read aloud to me."}
    request = (await c.post("/v2/accommodations/me", json=body, headers=as_("asha"))).json()
    await c.post(f"/v2/accommodations/{request['id']}/decide", headers=as_("owner"), json={
        "expected_version": 1, "decision": "approve", "reason": "Approved after a short conversation.",
        "conditions": ["read_aloud_instructions"]})
    await c.post(f"/v2/accommodations/me/{request['id']}/confirm", json={"expected_version": 2}, headers=as_("asha"))
    r = await c.post(f"/v2/accommodations/me/{request['id']}/withdraw", json={"expected_version": 2}, headers=as_("asha"))
    assert r.status_code == 200 and r.json()["status"] == "withdrawn"
    async with factory() as s:
        assert await confirmed_conditions(s, users["asha"]) == []
