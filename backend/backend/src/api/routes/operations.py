"""Operations recovery for administrators (architecture pack F28).

Lists what failed (email deliveries here) and offers a safe repair that never edits the
business record: a retry sends the current state of the interview, not a stale copy."""

import datetime as dt
from typing import Literal

import fastapi
import sqlalchemy as sa

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.session import get_async_session
from src.models.db.human_interview import HumanInterview
from src.models.db.notification import NotificationDelivery
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.user import User

router = fastapi.APIRouter(prefix="/v2/operations", tags=["operations"])


def administrators_only(user):
    if not is_admin_user(user):
        raise fastapi.HTTPException(403, "Operations recovery is limited to administrators.")


@router.get("/deliveries")
async def deliveries(
    response: fastapi.Response,
    status: Literal["failed", "queued", "skipped", "sent", "superseded"] = "failed",
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    administrators_only(user)
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await session.execute(
            sa.select(NotificationDelivery, User.name, HumanInterview.title)
            .join(User, User.id == NotificationDelivery.recipient_id)
            .outerjoin(HumanInterview, HumanInterview.id == NotificationDelivery.related_id)
            .where(NotificationDelivery.status == status)
            .order_by(NotificationDelivery.created_at.desc())
            .limit(100)
        )
    ).all()
    return {
        "items": [
            {
                "id": d.id, "event": d.event, "status": d.status, "attempts": d.attempts,
                "error": d.last_error, "recipient": name, "about": title,
                "scheduled_for": d.scheduled_for, "sent_at": d.sent_at,
            }
            for d, name, title in rows
        ]
    }


@router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery(
    delivery_id: int,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    administrators_only(user)
    row = (
        await session.execute(
            sa.select(NotificationDelivery).where(NotificationDelivery.id == delivery_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Delivery not found.")
    if row.status not in ("failed", "skipped"):
        raise fastapi.HTTPException(409, "Only a failed or skipped delivery can be retried.")
    row.status, row.attempts, row.scheduled_for = "queued", 0, dt.datetime.now(dt.timezone.utc)
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action="delivery_retried", purpose="operations",
            target_type="notification_delivery", target_id=str(row.id),
            reason="An administrator queued this email to be sent again.",
            event_metadata={"event": row.event, "related_id": row.related_id},
        )
    )
    await session.commit()
    return {"id": row.id, "status": row.status}
