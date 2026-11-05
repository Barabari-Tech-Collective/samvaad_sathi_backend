from __future__ import annotations

"""In-app notifications for the signed-in person (students and staff), architecture pack F30."""

import datetime as dt

import fastapi
import sqlalchemy as sa

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.notification import Notification

router = fastapi.APIRouter(prefix="/v2/notifications", tags=["notifications"])


@router.get("/me")
async def my_notifications(
    response: fastapi.Response,
    unread_only: bool = False,
    limit: int = fastapi.Query(30, ge=1, le=100),
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    mine = Notification.recipient_id == user.id
    stmt = sa.select(Notification).where(mine)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = (
        await session.execute(stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit))
    ).scalars().all()
    unread = (
        await session.execute(
            sa.select(sa.func.count(Notification.id)).where(mine, Notification.read_at.is_(None))
        )
    ).scalar_one()
    return {
        "unread": int(unread),
        "items": [
            {
                "id": n.id,
                "kind": n.kind,
                "title": n.title,
                "body": n.body,
                "href": n.href,
                "created_at": n.created_at,
                "read": n.read_at is not None,
                "metadata": getattr(n, "notification_metadata", None) or {},
            }
            for n in rows
        ],
    }


@router.post("/me/read-all")
async def read_all(
    user=fastapi.Depends(get_current_user), session=fastapi.Depends(get_async_session)
):
    await session.execute(
        sa.update(Notification)
        .where(Notification.recipient_id == user.id, Notification.read_at.is_(None))
        .values(read_at=dt.datetime.now(dt.timezone.utc))
    )
    await session.commit()
    return {"ok": True}


@router.post("/me/{notification_id}/read")
async def read_one(
    notification_id: int,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    result = await session.execute(
        sa.update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.recipient_id == user.id,
            Notification.read_at.is_(None),
        )
        .values(read_at=dt.datetime.now(dt.timezone.utc))
    )
    await session.commit()
    return {"updated": result.rowcount}
