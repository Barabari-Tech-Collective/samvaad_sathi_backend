"""Real SQL tests for reported transcript/feedback issues (pack F11)."""

import uuid

import fastapi
import httpx
import pytest
import sqlalchemy as sa

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.api.routes import feedback_issues, notifications as notification_routes
from src.models.db.interview import Interview
from src.models.db.notification import Notification
from src.models.db.user import User
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401


@pytest.fixture
async def issues(db):
    _, factory, _ = db
    async with factory() as s:
        users = {}
        for name, perms in [("asha", {}), ("bilal", {}), ("reviewer", {"interview_authoring": ["read", "update"]}),
                            ("plain", {})]:
            u = User(email=f"{name}@issue.test", name=name, password_hash="test", feature_permissions=perms)
            s.add(u)
            await s.flush()
            users[name] = u.id
        interview = Interview(user_id=users["asha"], track="Analyst", interview_mode="assessment", status="completed")
        s.add(interview)
        await s.commit()
        users["interview"] = interview.id
    app = fastapi.FastAPI()
    app.include_router(feedback_issues.router)
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


async def test_report_review_and_the_student_is_told(issues):
    c, factory, users = issues
    body = {"request_key": str(uuid.uuid4()), "interview_id": users["interview"], "kind": "transcription",
            "explanation": "My answer about joins was transcribed as 'drones'."}
    r = await c.post("/v2/feedback-issues/me", json=body, headers=as_("asha"))
    assert r.status_code == 201, r.text
    # Retrying the same report creates nothing twice; someone else's interview is refused.
    assert (await c.post("/v2/feedback-issues/me", json=body, headers=as_("asha"))).status_code == 200
    other = {**body, "request_key": str(uuid.uuid4())}
    assert (await c.post("/v2/feedback-issues/me", json=other, headers=as_("bilal"))).status_code == 404
    assert (await c.get("/v2/feedback-issues", headers=as_("plain"))).status_code == 403
    queue = (await c.get("/v2/feedback-issues", headers=as_("reviewer"))).json()["items"]
    assert len(queue) == 1 and queue[0]["track"] == "Analyst" and "reporter" not in str(queue[0])
    issue = queue[0]["id"]
    done = await c.post(f"/v2/feedback-issues/{issue}/resolve", headers=as_("reviewer"),
                        json={"decision": "resolved", "note": "Re-checked the audio; the transcript is corrected."})
    assert done.status_code == 200 and done.json()["status"] == "resolved"
    assert (await c.post(f"/v2/feedback-issues/{issue}/resolve", headers=as_("reviewer"),
                         json={"decision": "dismissed", "note": "Duplicate of an earlier report."})).status_code == 409
    mine = (await c.get("/v2/feedback-issues/me", headers=as_("asha"))).json()["items"][0]
    assert mine["status"] == "resolved" and mine["resolution"].startswith("Re-checked")
    told = (await c.get("/v2/notifications/me", headers=as_("asha"))).json()
    assert told["items"][0]["title"] == "Your reported issue was reviewed"
