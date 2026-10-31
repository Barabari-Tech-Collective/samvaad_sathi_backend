"""Real SQL tests for data export and deletion requests (pack F32)."""

import uuid

import fastapi
import httpx
import pytest
import sqlalchemy as sa

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.api.routes import data_requests
from src.models.db.learner_context import LearnerContext  # noqa: F401
from src.models.db.user import User
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


@pytest.fixture
async def world(db):
    _, factory, _ = db
    async with factory() as s:
        users = {}
        for name, kwargs in [
            ("asha", {}),
            ("owner", {"feature_permissions": {"data_requests": ["read", "update"]}}),
            ("admin", {"is_admin": True}),
        ]:
            u = User(email=f"{name}@dr.test", name=name, password_hash="test", **kwargs)
            s.add(u)
            await s.flush()
            users[name] = u.id
        await s.commit()
    app = fastapi.FastAPI()
    app.include_router(data_requests.router)

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


async def ask(c, kind):
    r = await c.post("/v2/data-requests", json={"request_key": str(uuid.uuid4()), "kind": kind}, headers=as_("asha"))
    assert r.status_code == 201, r.text
    return r.json()


async def test_export_flow_and_permissions(world):
    c, _, _ = world
    req = await ask(c, "export")
    dup = await c.post("/v2/data-requests", json={"request_key": str(uuid.uuid4()), "kind": "export"}, headers=as_("asha"))
    assert dup.status_code == 409
    note = {"expected_version": 1, "note": "Identity checked by phone."}
    assert (await c.get("/v2/data-requests", headers=as_("admin"))).status_code == 403
    assert (await c.post(f"/v2/data-requests/{req['id']}/verify", json=note, headers=as_("admin"))).status_code == 403
    assert (await c.get(f"/v2/data-requests/{req['id']}/export", headers=as_("asha"))).status_code == 409
    assert (await c.post(f"/v2/data-requests/{req['id']}/verify", json=note, headers=as_("owner"))).status_code == 200
    # Cannot complete from a stale version.
    stale = await c.post(f"/v2/data-requests/{req['id']}/complete", json=note, headers=as_("owner"))
    assert stale.status_code == 409
    done = await c.post(
        f"/v2/data-requests/{req['id']}/complete", json=note | {"expected_version": 2}, headers=as_("owner")
    )
    assert done.status_code == 200 and done.json()["status"] == "completed"
    out = (await c.get(f"/v2/data-requests/{req['id']}/export", headers=as_("asha"))).json()
    assert out["profile"][0]["name"] == "asha" and out["not_included"]


async def test_deletion_removes_practice_data_and_reports_retained(world, monkeypatch):
    c, factory, users = world
    async with factory() as s:
        s.add(LearnerContext(user_id=users["asha"], kind="project", title="Project", summary="x" * 20, status="confirmed"))
        await s.commit()
    req = await ask(c, "deletion")
    n = {"note": "Identity confirmed.", "expected_version": 1}
    await c.post(f"/v2/data-requests/{req['id']}/verify", json=n, headers=as_("owner"))
    done = await c.post(
        f"/v2/data-requests/{req['id']}/complete", json=n | {"expected_version": 2}, headers=as_("owner")
    )
    assert done.status_code == 200, done.text
    outcome = done.json()["outcome"]
    ctx = next(d for d in outcome["deleted"] if d["category"] == "Confirmed profile context")
    assert ctx["count"] == 1
    assert any(r["category"].startswith("Assessment") for r in outcome["retained"])
    assert any(p["category"] == "Answer recordings" for p in outcome["pending"])
    async with factory() as s:
        left = (await s.execute(sa.text("SELECT count(*) FROM learner_context WHERE user_id=:u"), {"u": users["asha"]})).scalar_one()
        assert left == 0


async def test_withdraw_only_before_verification(world):
    c, _, _ = world
    req = await ask(c, "deletion")
    assert (await c.post(f"/v2/data-requests/{req['id']}/withdraw", headers=as_("asha"))).status_code == 200
    again = await c.post(f"/v2/data-requests/{req['id']}/withdraw", headers=as_("asha"))
    assert again.status_code == 409
