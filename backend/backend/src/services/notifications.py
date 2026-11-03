"""Notifications and email delivery for human interviews (architecture pack F30).

Called inside the request's transaction, after the business change is made and before commit,
so a notification exists exactly when the change does. Emails are sent later by the worker
(`dispatch_due`): each attempt is recorded, a failure is retried with a delay and then left
for an operator, and the email is built from the appointment as it is at send time, so an old
reminder or an outdated "scheduled" mail is superseded instead of sent.
"""

import asyncio
import datetime as dt
import logging

import sqlalchemy as sa

from src.models.db.human_interview import HumanInterview
from src.models.db.notification import Notification, NotificationDelivery
from src.models.db.user import User

logger = logging.getLogger(__name__)
UTC = dt.timezone.utc
MAX_ATTEMPTS = 3
REMINDER_LEADS = (dt.timedelta(hours=24), dt.timedelta(hours=1))

STUDENT_HREF = "/human-interviews"

COPY = {
    "scheduled": ("Interview scheduled", "A live interview was scheduled for you."),
    "rescheduled": ("Interview time changed", "Your interview has a new time."),
    "cancelled": ("Interview cancelled", "This interview will not take place."),
    "result": ("Interview result ready", "Your interview result is ready."),
}


def notify(session, recipient_id, kind, title, body=None, href=None, related=None):
    """Add an in-app notification. Nothing private goes in title or body."""
    session.add(
        Notification(
            recipient_id=recipient_id, kind=kind, title=title, body=body, href=href,
            related_type=related[0] if related else None,
            related_id=related[1] if related else None,
        )
    )


def supersede_reminders(session):
    """Statement that marks an appointment's queued reminders as superseded."""
    return sa.update(NotificationDelivery).where(
        NotificationDelivery.event == "reminder", NotificationDelivery.status == "queued"
    )


async def human_event(session, row, event, *, now=None):
    """Record the student's notification and queue the email(s) for one interview event."""
    now = now or dt.datetime.now(UTC)
    title, body = COPY[event]
    notify(
        session, row.student_id, f"human_interview_{event}", f"{title}: {row.title}", body,
        STUDENT_HREF, ("human_interview", row.id),
    )
    session.add(
        NotificationDelivery(
            recipient_id=row.student_id, event=event, related_id=row.id, scheduled_for=now,
        )
    )
    # Any reminder made for an older time or a cancelled interview must not go out.
    await session.execute(
        supersede_reminders(session).where(NotificationDelivery.related_id == row.id)
        .values(status="superseded", last_error=f"Replaced by a {event} change.")
    )
    if event in ("scheduled", "rescheduled"):
        for lead in REMINDER_LEADS:
            when = row.starts_at - lead
            if when > now:
                session.add(
                    NotificationDelivery(
                        recipient_id=row.student_id, event="reminder", related_id=row.id,
                        expected_starts_at=row.starts_at, scheduled_for=when,
                    )
                )


async def payload_for(session, row):
    student = await session.get(User, row.student_id)
    return {
        "to": getattr(student, "email", None),
        "student_name": getattr(student, "name", None),
        **{
            key: getattr(row, key)
            for key in (
                "title", "role_name", "interviewer_name", "instructions",
                "starts_at", "ends_at", "result", "feedback",
            )
        },
    }


def send_now(event: str, payload: dict) -> str:
    """Send one email. Returns 'sent', 'skipped' (not configured / no address) or 'failed'."""
    from src.services.email.human_interview_emails import build
    from src.services.email import sender

    if not payload.get("to"):
        return "skipped"
    if not sender.configured():
        return "skipped"
    subject, html, text = build(event, payload)
    return "sent" if sender.send_email(payload["to"], subject, html, text) else "failed"


async def dispatch_due(session, *, now=None, limit=50, send=send_now):
    """Send what is due. Safe to run from several workers at once."""
    now = now or dt.datetime.now(UTC)
    due = (
        await session.execute(
            sa.select(NotificationDelivery)
            .where(NotificationDelivery.status == "queued", NotificationDelivery.scheduled_for <= now)
            .order_by(NotificationDelivery.scheduled_for)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    counts = {"sent": 0, "skipped": 0, "failed": 0, "superseded": 0, "retry": 0}
    for item in due:
        row = await session.get(HumanInterview, item.related_id)
        if row is None:
            item.status, item.last_error = "superseded", "The interview no longer exists."
        elif item.event == "reminder" and (
            row.status != "scheduled"
            or row.starts_at != item.expected_starts_at
            or row.starts_at <= now
        ):
            item.status, item.last_error = "superseded", "The interview changed or already started."
        elif item.event in ("scheduled", "rescheduled") and row.status == "cancelled":
            item.status, item.last_error = "superseded", "Cancelled before this was sent."
        else:
            event = "scheduled" if item.event == "reminder" else item.event
            payload = await payload_for(session, row)
            if item.event == "reminder":
                payload["reminder"] = True
            try:
                outcome = await asyncio.to_thread(send, "reminder" if item.event == "reminder" else event, payload)
            except Exception as error:  # a broken template or transport must not stop the batch
                logger.exception("notification_delivery_failed")
                outcome, item.last_error = "failed", str(error)[:300]
            item.attempts += 1
            if outcome == "sent":
                item.status, item.sent_at, item.last_error = "sent", now, None
            elif outcome == "skipped":
                item.status = "skipped"
                item.last_error = "Email is not configured or the person has no address."
            elif item.attempts < MAX_ATTEMPTS:
                item.scheduled_for = now + dt.timedelta(minutes=5 * item.attempts)
                item.last_error = item.last_error or "Send failed; will retry."
                counts["retry"] += 1
                continue
            else:
                item.status = "failed"
                item.last_error = item.last_error or "Send failed after several attempts."
        counts[item.status] = counts.get(item.status, 0) + 1
    await session.commit()
    return counts
