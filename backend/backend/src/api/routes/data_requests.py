"""Export and deletion requests (architecture pack F32).

Student: ask, read their own, withdraw before verification, download an export.
Owners (restricted "data_requests" permission): queue, verify identity, complete or reject."""

import datetime as dt
from typing import Literal
from uuid import UUID

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.data_request import DataRequest
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.user import User
from src.services import data_requests as service
from src.services import notifications
from src.services.feature_access import permits

router = fastapi.APIRouter(prefix="/v2/data-requests", tags=["data-requests"])
UTC = dt.timezone.utc


def owner(user, operation="read") -> bool:
    return isinstance(user.feature_permissions, dict) and permits(user.feature_permissions, "data_requests", operation)


def require_owner(user, operation="read"):
    if not owner(user, operation):
        raise fastapi.HTTPException(403, "Data requests are limited to people granted that restricted permission.")


class Create(BaseModel):
    request_key: UUID
    kind: Literal["export", "deletion"]
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note", mode="before")
    @classmethod
    def trim(cls, v):
        return v.strip() or None if isinstance(v, str) else v


class Decide(BaseModel):
    expected_version: int = Field(ge=1)
    note: str = Field(min_length=10, max_length=1000)

    @field_validator("note", mode="before")
    @classmethod
    def trim(cls, v):
        return v.strip() if isinstance(v, str) else v


def view(r):
    return {
        "id": r.id, "kind": r.kind, "status": r.status, "version": r.version, "note": r.note,
        "outcome": r.outcome, "closure_note": r.closure_note, "created_at": r.created_at,
        "completed_at": r.completed_at,
    }


def audit(session, user, r, action, reason):
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action=action, purpose="data_requests", target_type="data_request",
            target_id=str(r.id), reason=reason, event_metadata={"kind": r.kind, "status": r.status},
        )
    )


@router.post("", status_code=201)
async def create(
    body: Create, response: fastapi.Response,
    user=fastapi.Depends(get_current_user), session=fastapi.Depends(get_async_session),
):
    existing = (
        await session.execute(
            sa.select(DataRequest).where(DataRequest.student_id == user.id, DataRequest.request_key == str(body.request_key))
        )
    ).scalar_one_or_none()
    if existing:
        response.status_code = 200
        return view(existing)
    open_one = (
        await session.execute(
            sa.select(DataRequest.id).where(
                DataRequest.student_id == user.id, DataRequest.kind == body.kind,
                DataRequest.status.in_(("submitted", "verified")),
            )
        )
    ).first()
    if open_one:
        raise fastapi.HTTPException(409, "You already have an open request of this kind.")
    row = DataRequest(student_id=user.id, kind=body.kind, note=body.note, request_key=str(body.request_key))
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise fastapi.HTTPException(409, "Retry with the same request key.")
    await session.refresh(row)
    return view(row)


@router.get("/me")
async def mine(
    response: fastapi.Response, user=fastapi.Depends(get_current_user), session=fastapi.Depends(get_async_session)
):
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await session.execute(
            sa.select(DataRequest).where(DataRequest.student_id == user.id).order_by(DataRequest.id.desc()).limit(50)
        )
    ).scalars().all()
    return {"items": [view(r) for r in rows]}


async def own(session, user, request_id):
    row = (
        await session.execute(
            sa.select(DataRequest).where(DataRequest.id == request_id, DataRequest.student_id == user.id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Request not found.")
    return row


@router.post("/{request_id}/withdraw")
async def withdraw(
    request_id: int, user=fastapi.Depends(get_current_user), session=fastapi.Depends(get_async_session)
):
    row = await own(session, user, request_id)
    if row.status != "submitted":
        raise fastapi.HTTPException(409, "Only a request that is not yet verified can be withdrawn.")
    row.status, row.version = "withdrawn", row.version + 1
    await session.commit()
    return view(row)


@router.get("/{request_id}/export")
async def download(
    request_id: int, response: fastapi.Response,
    user=fastapi.Depends(get_current_user), session=fastapi.Depends(get_async_session),
):
    row = await own(session, user, request_id)
    if row.kind != "export" or row.status != "completed":
        raise fastapi.HTTPException(409, "This export is not ready.")
    if row.export_available_until and row.export_available_until < dt.datetime.now(UTC):
        raise fastapi.HTTPException(410, "This export has expired. Ask for a new one.")
    response.headers["Cache-Control"] = "no-store"
    return await service.build_export(session, user.id)


# ----------------------------------------------------------------------- owners


@router.get("")
async def queue(
    status: Literal["submitted", "verified", "completed", "rejected", "withdrawn"] = "submitted",
    user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session),
):
    require_owner(user)
    rows = (
        await session.execute(
            sa.select(DataRequest, User.name, User.email)
            .join(User, User.id == DataRequest.student_id)
            .where(DataRequest.status == status).order_by(DataRequest.created_at).limit(100)
        )
    ).all()
    return {
        "items": [view(r) | {"student": n, "email": e, "student_id": r.student_id} for r, n, e in rows],
        "can_decide": owner(user, "update"),
    }


async def locked(session, request_id):
    row = (
        await session.execute(sa.select(DataRequest).where(DataRequest.id == request_id).with_for_update())
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Request not found.")
    return row


def _check(row, body, allowed):
    if row.version != body.expected_version or row.status not in allowed:
        raise fastapi.HTTPException(409, "This request changed. Reload it.")


@router.post("/{request_id}/verify")
async def verify(
    request_id: int, body: Decide,
    user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session),
):
    require_owner(user, "update")
    row = await locked(session, request_id)
    _check(row, body, ("submitted",))
    row.status, row.version = "verified", row.version + 1
    row.verification_note, row.handled_by = body.note, user.id
    audit(session, user, row, "data_request_verified", body.note)
    await session.commit()
    return view(row)


@router.post("/{request_id}/reject")
async def reject(
    request_id: int, body: Decide,
    user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session),
):
    require_owner(user, "update")
    row = await locked(session, request_id)
    _check(row, body, ("submitted", "verified"))
    row.status, row.version = "rejected", row.version + 1
    row.closure_note, row.handled_by, row.completed_at = body.note, user.id, dt.datetime.now(UTC)
    audit(session, user, row, "data_request_rejected", body.note)
    notifications.notify(
        session, row.student_id, "data_request_closed", "Your data request was closed",
        "Open the request to read the reason.", "/data-requests", ("data_request", row.id),
    )
    await session.commit()
    return view(row)


@router.post("/{request_id}/complete")
async def complete(
    request_id: int, body: Decide,
    user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session),
):
    """Only a verified request is carried out. Deletion reports what was removed and kept."""
    require_owner(user, "update")
    row = await locked(session, request_id)
    _check(row, body, ("verified",))
    now = dt.datetime.now(UTC)
    if row.kind == "deletion":
        row.outcome = await service.execute_deletion(session, row.student_id)
    else:
        row.export_available_until = now + dt.timedelta(days=7)
    row.status, row.version = "completed", row.version + 1
    row.closure_note, row.handled_by, row.completed_at = body.note, user.id, now
    audit(session, user, row, "data_request_completed", body.note)
    if row.kind == "export":
        notifications.notify(
            session, row.student_id, "data_request_closed", "Your data export is ready",
            "You can download it for 7 days.", "/data-requests", ("data_request", row.id),
        )
    await session.commit()
    return view(row)
