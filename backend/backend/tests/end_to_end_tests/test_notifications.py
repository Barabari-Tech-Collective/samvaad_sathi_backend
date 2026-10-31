"""Real SQL tests for durable notifications and email delivery (pack F30, A10, A11)."""

import datetime as dt
import uuid

import fastapi
import httpx
import pytest
import sqlalchemy as sa

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.api.routes import notifications as notification_routes, operations
from src.models.db.human_interview import HumanInterview
from src.models.db.notification import Notification, NotificationDelivery
from src.models.db.user import User
from src.services import notifications
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401

UTC = dt.timezone.utc


@pytest.fixture
async def world(db):
    _, factory, _ = db
    now = dt.datetime.now(UTC)
    async with factory() as s:
        admin = User(email="admin@notify.test", name="admin", password_hash="test", is_admin=True)
        plain = User(email="plain@notify.test", name="plain", password_hash="test")
        student = User(email="asha@notify.test", name="asha", password_hash="test")
        s.add_all([admin, plain, student])
        await s.flush()
        row = HumanInterview(
            course_id="course-a", student_id=student.id, host_id=admin.id, title="Panel",
            meet_url="https://meet.google.com/abc-defg-hij",
            starts_at=now + dt.timedelta(days=3), ends_at=now + dt.timedelta(days=3, minutes=30),
            request_key=str(uuid.uuid4()), request_hash="x", status="scheduled",
        )
        s.add(row)
        await s.commit()
        ids = {"admin": admin.id, "plain": plain.id, "student": student.id, "row": row.id}
    app = fastapi.FastAPI()
    app.include_router(notification_routes.router)
    app.include_router(operations.router)

    async def session_dep():
        async with factory() as s:
            try:
                yield s
            finally:
                await s.rollback()

    async def actor(request: fastapi.Request, session=fastapi.Depends(get_async_session)):
        return await session.get(User, ids[request.headers["x-test-actor"]])

    app.dependency_overrides[get_async_session] = session_dep
    app.dependency_overrides[get_current_user] = actor
    app.dependency_overrides[get_current_admin_user] = actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c, factory, ids


def as_(who):
    return {"x-test-actor": who}


async def deliveries(factory, event=None):
    async with factory() as s:
        stmt = sa.select(NotificationDelivery).order_by(NotificationDelivery.id)
        if event:
            stmt = stmt.where(NotificationDelivery.event == event)
        return (await s.execute(stmt)).scalars().all()


async def test_scheduling_saves_a_notification_and_queues_email_and_reminders(world):
    c, factory, ids = world
    async with factory() as s:
        await notifications.human_event(s, await s.get(HumanInterview, ids["row"]), "scheduled")
        await s.commit()
    got = (await c.get("/v2/notifications/me", headers=as_("student"))).json()
    assert got["unread"] == 1 and got["items"][0]["title"].startswith("Interview scheduled")
    # Nothing private, and other people see none of it.
    assert (await c.get("/v2/notifications/me", headers=as_("plain"))).json()["items"] == []
    assert [d.event for d in await deliveries(factory)] == ["scheduled", "reminder", "reminder"]
    await c.post(f"/v2/notifications/me/{got['items'][0]['id']}/read", headers=as_("student"))
    assert (await c.get("/v2/notifications/me", headers=as_("student"))).json()["unread"] == 0
    # Someone else cannot mark it read.
    assert (await c.post(f"/v2/notifications/me/{got['items'][0]['id']}/read", headers=as_("plain"))).json()["updated"] == 0


async def test_failed_email_is_retried_then_left_for_an_operator_and_the_interview_stays_valid(world):
    c, factory, ids = world
    async with factory() as s:
        await notifications.human_event(s, await s.get(HumanInterview, ids["row"]), "scheduled")
        await s.commit()
    now = dt.datetime.now(UTC)
    for attempt in range(3):
        async with factory() as s:
            counts = await notifications.dispatch_due(s, now=now + dt.timedelta(hours=attempt), send=lambda e, p: "failed")
        assert counts["failed" if attempt == 2 else "retry"] == 1
    failed = (await c.get("/v2/operations/deliveries?status=failed", headers=as_("admin"))).json()["items"]
    assert [d["event"] for d in failed] == ["scheduled"] and failed[0]["attempts"] == 3
    assert (await c.get("/v2/operations/deliveries", headers=as_("plain"))).status_code == 403
    async with factory() as s:
        assert (await s.get(HumanInterview, ids["row"])).status == "scheduled"
    sent = []
    r = await c.post(f"/v2/operations/deliveries/{failed[0]['id']}/retry", headers=as_("admin"))
    assert r.status_code == 200
    async with factory() as s:
        counts = await notifications.dispatch_due(s, now=now + dt.timedelta(days=1), send=lambda e, p: sent.append((e, p["title"])) or "sent")
    assert counts["sent"] == 1 and sent == [("scheduled", "Panel")]
    assert (await c.post(f"/v2/operations/deliveries/{failed[0]['id']}/retry", headers=as_("admin"))).status_code == 409


async def test_reschedule_and_cancel_supersede_old_reminders(world):
    c, factory, ids = world
    async with factory() as s:
        row = await s.get(HumanInterview, ids["row"])
        await notifications.human_event(s, row, "scheduled")
        await s.commit()
        old = [d.id for d in await deliveries(factory, "reminder")]
        row.starts_at += dt.timedelta(days=1)
        row.ends_at += dt.timedelta(days=1)
        await notifications.human_event(s, row, "rescheduled")
        await s.commit()
    reminders = await deliveries(factory, "reminder")
    assert [d.status for d in reminders if d.id in old] == ["superseded", "superseded"]
    assert [d.status for d in reminders if d.id not in old] == ["queued", "queued"]
    sent = []
    async with factory() as s:
        row = await s.get(HumanInterview, ids["row"])
        row.status = "cancelled"
        await notifications.human_event(s, row, "cancelled")
        await s.commit()
        # Both the older "scheduled" mail and every reminder are suppressed; only the cancellation goes.
        await notifications.dispatch_due(s, now=dt.datetime.now(UTC) + dt.timedelta(days=10),
                                         send=lambda e, p: sent.append(e) or "sent")
    assert sent == ["cancelled"]
    assert all(d.status in ("superseded", "sent") for d in await deliveries(factory))
