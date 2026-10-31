"""Real SQL test for the one-time privacy notice."""

import fastapi
import httpx

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.api.routes import privacy_notice
from src.models.db.user import User
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


async def test_accept_once_is_idempotent_and_versioned(db):
    _, factory, _ = db
    async with factory() as s:
        u = User(email="p@pn.test", name="p", password_hash="test")
        s.add(u)
        await s.commit()
        uid = u.id
    app = fastapi.FastAPI()
    app.include_router(privacy_notice.router)

    async def session_dep():
        async with factory() as s:
            yield s

    async def actor(session=fastapi.Depends(get_async_session)):
        return await session.get(User, uid)

    app.dependency_overrides[get_async_session] = session_dep
    app.dependency_overrides[get_current_user] = actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/v2/privacy-notice")).json()["accepted"] is False
        assert (await c.post("/v2/privacy-notice/accept", json={"version": "old"})).status_code == 409
        for _ in range(2):
            assert (await c.post("/v2/privacy-notice/accept", json={"version": privacy_notice.VERSION})).status_code == 200
        assert (await c.get("/v2/privacy-notice")).json()["accepted"] is True
