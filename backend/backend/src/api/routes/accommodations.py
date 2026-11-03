"""Accommodation requests (architecture pack F13).

Student: request privately, read their own, confirm approved conditions, withdraw.
Owners (the restricted "accommodations" permission): queue, approve with conditions or decline
with a reason. Everyone else sees nothing here; interviewers and reviewers get only confirmed
condition labels, attached to the assessment record they already have access to.
"""

import datetime as dt
from typing import Literal
from uuid import UUID

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.accommodation import CONDITIONS, AccommodationRequest
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.user import User
from src.services import notifications
from src.services.accommodations import owner

router = fastapi.APIRouter(prefix="/v2/accommodations", tags=["accommodations"])


def _trim(value):
    return value.strip() if isinstance(value, str) else value


class Request(BaseModel):
    request_key: UUID
    description: str = Field(min_length=10, max_length=2000)

    _trim = field_validator("description", mode="before")(classmethod(lambda c, v: _trim(v)))


class Versioned(BaseModel):
    expected_version: int = Field(ge=1)


class Decide(Versioned):
    decision: Literal["approve", "decline"]
    reason: str = Field(min_length=10, max_length=1000)
    conditions: list[str] = Field(default_factory=list, max_length=len(CONDITIONS))

    _trim = field_validator("reason", mode="before")(classmethod(lambda c, v: _trim(v)))


def labelled(codes):
    return [{"code": c, "label": CONDITIONS[c]} for c in codes if c in CONDITIONS]


def student_view(r):
    return {
        "id": r.id, "status": r.status, "version": r.version, "description": r.description,
        "conditions": labelled(r.conditions), "decision_reason": r.decision_reason,
        "decided_at": r.decided_at, "confirmed": r.confirmed_at is not None,
        "created_at": r.created_at,
    }


@router.get("/catalogue")
async def catalogue():
    return {"conditions": [{"code": c, "label": l} for c, l in CONDITIONS.items()]}


@router.post("/me", status_code=201)
async def request_support(
    body: Request,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    existing = (
        await session.execute(
            sa.select(AccommodationRequest).where(
                AccommodationRequest.student_id == user.id,
                AccommodationRequest.request_key == str(body.request_key),
            )
        )
    ).scalar_one_or_none()
    if existing:
        response.status_code = 200
        return student_view(existing)
    row = AccommodationRequest(
        student_id=user.id, request_key=str(body.request_key), description=body.description
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return student_view(row)


@router.get("/me")
async def mine(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await session.execute(
            sa.select(AccommodationRequest)
            .where(AccommodationRequest.student_id == user.id)
            .order_by(AccommodationRequest.created_at.desc())
        )
    ).scalars().all()
    return {"items": [student_view(r) for r in rows]}


async def own(session, user, request_id):
    row = (
        await session.execute(
            sa.select(AccommodationRequest)
            .where(AccommodationRequest.id == request_id, AccommodationRequest.student_id == user.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Not found.")
    return row


@router.post("/me/{request_id}/confirm")
async def confirm(
    request_id: int,
    body: Versioned,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    row = await own(session, user, request_id)
    if row.status != "approved" or row.version != body.expected_version:
        raise fastapi.HTTPException(409, "Only the current approved conditions can be confirmed.")
    row.confirmed_at = dt.datetime.now(dt.timezone.utc)
    await session.commit()
    return student_view(row)


@router.post("/me/{request_id}/withdraw")
async def withdraw(
    request_id: int,
    body: Versioned,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    row = await own(session, user, request_id)
    if row.status in ("withdrawn", "declined") or row.version != body.expected_version:
        raise fastapi.HTTPException(409, "This request cannot be withdrawn.")
    row.status, row.confirmed_at, row.version = "withdrawn", None, row.version + 1
    await session.commit()
    return student_view(row)


# ----------------------------------------------------------------------- owners


def require_owner(user, operation="read"):
    if not owner(user, operation):
        raise fastapi.HTTPException(
            403, "Accommodation requests are limited to people granted that restricted permission."
        )


@router.get("")
async def queue(
    response: fastapi.Response,
    status: Literal["requested", "approved", "declined", "withdrawn"] = "requested",
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    require_owner(user)
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await session.execute(
            sa.select(AccommodationRequest, User.name)
            .join(User, User.id == AccommodationRequest.student_id)
            .where(AccommodationRequest.status == status)
            .order_by(AccommodationRequest.created_at)
            .limit(100)
        )
    ).all()
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action="accommodation_queue_viewed", purpose="accommodations",
            target_type="accommodation_queue", target_id=status,
            reason="Opened the accommodation requests.", event_metadata={"returned": len(rows)},
        )
    )
    await session.commit()
    return {
        "items": [student_view(r) | {"student": name, "student_id": r.student_id} for r, name in rows],
        "conditions": [{"code": c, "label": l} for c, l in CONDITIONS.items()],
        "can_decide": owner(user, "update"),
    }


@router.post("/{request_id}/decide")
async def decide(
    request_id: int,
    body: Decide,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    require_owner(user, "update")
    row = (
        await session.execute(
            sa.select(AccommodationRequest).where(AccommodationRequest.id == request_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Request not found.")
    if row.status != "requested" or row.version != body.expected_version:
        raise fastapi.HTTPException(409, "This request changed. Refresh before deciding.")
    if body.decision == "approve":
        if not body.conditions or any(c not in CONDITIONS for c in body.conditions):
            raise fastapi.HTTPException(422, "Choose at least one approved condition from the list.")
        row.status, row.conditions = "approved", sorted(set(body.conditions))
    else:
        row.status, row.conditions = "declined", []
    row.decision_reason, row.decided_by = body.reason, user.id
    row.decided_at, row.confirmed_at, row.version = dt.datetime.now(dt.timezone.utc), None, row.version + 1
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action=f"accommodation_{row.status}", purpose="accommodations",
            target_type="accommodation_request", target_id=str(row.id), reason=body.reason,
            event_metadata={"conditions": row.conditions},
        )
    )
    notifications.notify(
        session, row.student_id, "accommodation_decided", "Your support request has an answer",
        None, "/profile", ("accommodation_request", row.id),
    )
    await session.commit()
    return student_view(row)
