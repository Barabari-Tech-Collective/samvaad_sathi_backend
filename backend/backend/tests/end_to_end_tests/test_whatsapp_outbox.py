"""Interview updates on WhatsApp through the messaging hub (suite phase 9B), on real Postgres.

The outbox row is written with the interview change; the worker publishes an envelope v2 job
with no personal data; a failed publish is retried; a reminder for a moved interview is dropped.
"""

import datetime as dt
import json
import re
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from src.models.db.human_interview import HumanInterview
from src.models.db.notification import NotificationDelivery
from src.models.db.user import User
from src.services import notifications, whatsapp_hub
from .test_fixed_question_rounds import db, pytestmark  # noqa: F401

UTC = dt.timezone.utc
PHONE = re.compile(r"(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}|\+\d{11,15}")
SCHEMA = json.loads((Path(__file__).parents[1] / "fixtures/contracts/whatsapp-job.v2.schema.json").read_text())


@pytest.fixture
async def world(db, monkeypatch):
    _, factory, _ = db
    monkeypatch.setattr(whatsapp_hub.settings, "WHATSAPP_EVENTS", "scheduled,reminder,result", raising=False)
    now = dt.datetime.now(UTC)
    async with factory() as s:
        host = User(email="host@wa.test", name="host", password_hash="x", is_admin=True)
        student = User(email="asha@wa.test", name="Asha", password_hash="x", student_id="STU-0001", phone_number="+919876543210") \
            if hasattr(User, "phone_number") else User(email="asha@wa.test", name="Asha", password_hash="x", student_id="STU-0001")
        outsider = User(email="guest@wa.test", name="Guest", password_hash="x")
        s.add_all([host, student, outsider])
        await s.flush()
        rows = []
        for who in (student, outsider):
            r = HumanInterview(
                course_id="c", student_id=who.id, host_id=host.id, title="Mock interview",
                meet_url="https://meet.google.com/abc-defg-hij", starts_at=now + dt.timedelta(days=2),
                ends_at=now + dt.timedelta(days=2, minutes=30), request_key=str(uuid.uuid4()), request_hash="x",
                status="scheduled",
            )
            s.add(r)
            rows.append(r)
        await s.commit()
        yield factory, rows[0].id, rows[1].id, now


async def _whatsapp_rows(factory):
    async with factory() as s:
        return (await s.execute(
            sa.select(NotificationDelivery).where(NotificationDelivery.channel == "whatsapp").order_by(NotificationDelivery.id)
        )).scalars().all()


async def _dispatch(factory, publish, now):
    async with factory() as s:
        due = (await s.execute(
            sa.select(NotificationDelivery).where(NotificationDelivery.channel == "whatsapp", NotificationDelivery.status == "queued",
                                                  NotificationDelivery.scheduled_for <= now)
        )).scalars().all()
        counts = {"retry": 0}
        for item in due:
            row = await s.get(HumanInterview, item.related_id)
            await notifications._publish_whatsapp(s, item, row, now, counts, publish=publish)
        await s.commit()
        return counts


async def test_scheduling_queues_whatsapp_with_the_change_and_publishes_v2_without_personal_data(world):
    factory, row_id, _, now = world
    async with factory() as s:
        await notifications.human_event(s, await s.get(HumanInterview, row_id), "scheduled", now=now)
        await s.commit()
    rows = await _whatsapp_rows(factory)
    assert [r.event for r in rows] == ["scheduled", "reminder", "reminder"]

    published = []

    async def publish(job):
        published.append(job)

    await _dispatch(factory, publish, now)
    assert len(published) == 1
    job = published[0]
    assert job["eventType"] == "INTERVIEW_SCHEDULED" and job["source"] == "samvaad"
    assert job["payload"][0]["studentUniqueId"] == "STU-0001"
    body = json.dumps(job)
    assert not PHONE.search(body) and "@wa.test" not in body and "Asha" not in body
    allowed = set(SCHEMA["properties"]["payload"]["items"]["properties"])
    assert set(job["payload"][0]) <= allowed and set(job) <= set(SCHEMA["properties"])
    assert (await _whatsapp_rows(factory))[0].status == "sent"


async def test_the_same_row_always_gives_the_same_event_id():
    class Row:
        id, title, starts_at = 7, "T", dt.datetime(2026, 11, 21, 9, 30, tzinfo=UTC)

    a = whatsapp_hub.envelope(41, "scheduled", "S", Row, dt.datetime.now(UTC))
    b = whatsapp_hub.envelope(41, "scheduled", "S", Row, dt.datetime.now(UTC))
    assert a["eventId"] == b["eventId"] and a["payload"][0]["dispatchKey"] == "samvaad:notification_delivery:41"
    assert a["payload"][0]["variables"]["startsAt"] == "21 Nov 2026, 3:00 pm"


async def test_a_broker_outage_is_retried_with_backoff(world):
    factory, row_id, _, now = world
    async with factory() as s:
        await notifications.human_event(s, await s.get(HumanInterview, row_id), "result", now=now)
        await s.commit()

    async def down(job):
        raise ConnectionError("broker unreachable")

    counts = await _dispatch(factory, down, now)
    row = (await _whatsapp_rows(factory))[0]
    assert counts["retry"] == 1 and row.status == "queued" and row.scheduled_for > now
    assert "messaging hub" in row.last_error


async def test_a_learner_without_a_sampark_id_gets_no_whatsapp(world):
    factory, _, outsider_row, now = world
    async with factory() as s:
        await notifications.human_event(s, await s.get(HumanInterview, outsider_row), "result", now=now)
        await s.commit()
    await _dispatch(factory, lambda job: None, now)
    assert (await _whatsapp_rows(factory))[0].status == "skipped"


async def test_events_not_switched_on_queue_nothing(world, monkeypatch):
    factory, row_id, _, now = world
    monkeypatch.setattr(whatsapp_hub.settings, "WHATSAPP_EVENTS", "", raising=False)
    async with factory() as s:
        await notifications.human_event(s, await s.get(HumanInterview, row_id), "scheduled", now=now)
        await s.commit()
    assert await _whatsapp_rows(factory) == []
