"""Interview updates on WhatsApp through the Barabari messaging hub (suite phase 9B).

Rows with channel 'whatsapp' are written to notification_delivery in the same transaction as
the interview change (see notifications.human_event), so an update is never lost or sent for a
change that rolled back. The worker turns each due row into an envelope v2 job
(contracts/whatsapp-job v2) and publishes it to the hub's exchange with publisher confirms.

The job carries **no personal data**: only the student's Sampark id and the interview fields
the template needs. The hub looks up name and phone, checks STOP, picks the template from its
rules and records the message in its ledger. eventId and dispatchKey are derived from the row
id, so a retried publish is applied once by the hub.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from zoneinfo import ZoneInfo

from src.config.manager import settings

IST = ZoneInfo("Asia/Kolkata")
_NS = uuid.UUID("4c2f7a3e-9a1b-4f0e-8d2c-6b5a4e3d2c1b")  # fixed namespace for Samvaad events

EVENT_TYPES = {
    "scheduled": "INTERVIEW_SCHEDULED",
    "rescheduled": "INTERVIEW_RESCHEDULED",
    "cancelled": "INTERVIEW_CANCELLED",
    "reminder": "INTERVIEW_REMINDER",
    "result": "INTERVIEW_RESULT_RELEASED",
}


def enabled_events() -> set[str]:
    return {e.strip() for e in (settings.WHATSAPP_EVENTS or "").split(",") if e.strip() in EVENT_TYPES}


def when_ist(value: dt.datetime | None) -> str:
    if not value:
        return ""
    local = value.astimezone(IST)
    return local.strftime("%d %b %Y, %I:%M %p").lstrip("0").replace(" 0", " ").replace("AM", "am").replace("PM", "pm")


def envelope(delivery_id: int, event: str, student_unique_id: str, interview, now: dt.datetime) -> dict:
    key = f"samvaad:notification_delivery:{delivery_id}"
    variables = {"title": getattr(interview, "title", "") or "", "startsAt": when_ist(getattr(interview, "starts_at", None))}
    return {
        "schemaVersion": 2,
        "eventId": str(uuid.uuid5(_NS, key)),
        "requestId": f"samvaad-hi-{getattr(interview, 'id', '')}",
        "eventType": EVENT_TYPES[event],
        "source": "samvaad",
        "occurredAt": now.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload": [{"studentUniqueId": student_unique_id, "dispatchKey": key, "variables": variables}],
    }


def _url() -> str:
    protocol = "amqps" if settings.RABBITMQ_SSL_ENABLED else "amqp"
    vhost = "" if settings.RABBITMQ_VIRTUAL_HOST == "/" else settings.RABBITMQ_VIRTUAL_HOST
    return f"{protocol}://{settings.RABBITMQ_USERNAME}:{settings.RABBITMQ_PASSWORD}@{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}/{vhost}"


async def publish(job: dict) -> None:
    """Publish one job and wait for the broker's confirm. Raises on failure."""
    import aio_pika

    connection = await aio_pika.connect_robust(_url(), timeout=10)
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        exchange = await channel.get_exchange(settings.WHATSAPP_HUB_EXCHANGE, ensure=True)
        await exchange.publish(
            aio_pika.Message(
                body=json.dumps(job).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=job["eventId"],
            ),
            routing_key=settings.WHATSAPP_HUB_ROUTING_KEY,
        )
