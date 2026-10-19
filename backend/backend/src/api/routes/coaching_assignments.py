"""Explicit course follow-ups, separate from unscoped legacy assignments."""

import datetime
import hashlib
import json
from typing import Literal
from uuid import UUID

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.services.feature_access import permits
from src.api.dependencies.session import get_async_session
from src.models.db.coaching_assignment import CoachingAssignment
from src.models.db.user import User
from src.services.coaching_assignments import authorize_staff, serialize, audit

router = fastapi.APIRouter(
    prefix="/v2/coaching-assignments", tags=["coaching-assignments"]
)


class AssignmentCreate(BaseModel):
    request_key: UUID
    module: Literal["warm_up", "think_fast"]
    reason: str = Field(min_length=10, max_length=1000)
    note: str | None = Field(default=None, max_length=2000)
    due_date: datetime.date | None = None

    @field_validator("reason", "note", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class AssignmentCancel(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


@router.get("/me")
async def own_assignments(
    response: fastapi.Response,
    page: int = fastapi.Query(1, ge=1),
    limit: int = fastapi.Query(25, ge=1, le=100),
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    return await assignment_page(
        session, CoachingAssignment.student_id == user.id, page, limit
    )


@router.get("/me/{assignment_id}")
async def own_assignment(
    assignment_id: int,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    from src.services.coaching_assignments import require_owned_enrollment

    response.headers["Cache-Control"] = "no-store"
    row = (
        await session.execute(
            sa.select(CoachingAssignment).where(
                CoachingAssignment.id == assignment_id,
                CoachingAssignment.student_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Assignment not found.")
    if row.status == "assigned":
        await require_owned_enrollment(session, user.id, row.course_id)
    return serialize(row)


async def assignment_page(session, predicate, page, limit):
    total = (
        await session.execute(
            sa.select(sa.func.count()).select_from(CoachingAssignment).where(predicate)
        )
    ).scalar_one()
    rows = (
        (
            await session.execute(
                sa.select(CoachingAssignment)
                .where(predicate)
                .order_by(
                    CoachingAssignment.created_at.desc(), CoachingAssignment.id.desc()
                )
                .offset((page - 1) * limit)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [serialize(row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "notice": "Completion records student-reported speaking practice, not a verified skill score. Due dates are reminders; late practice is accepted.",
    }


@router.get("/courses/{course_id}/students/{student_id}")
async def staff_assignments(
    course_id: str,
    student_id: int,
    response: fastapi.Response,
    page: int = fastapi.Query(1, ge=1),
    limit: int = fastapi.Query(25, ge=1, le=100),
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    await authorize_staff(session, user, course_id, student_id, "read")
    result = await assignment_page(
        session,
        sa.and_(
            CoachingAssignment.course_id == course_id,
            CoachingAssignment.student_id == student_id,
        ),
        page,
        limit,
    )
    result["can_create"] = is_admin_user(user) or permits(
        user.feature_permissions, "practice_coaching", "create"
    )
    result["can_cancel"] = is_admin_user(user) or permits(
        user.feature_permissions, "practice_coaching", "update"
    )
    return result


@router.post("/courses/{course_id}/students/{student_id}", status_code=201)
async def create_assignment(
    course_id: str,
    student_id: int,
    body: AssignmentCreate,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    await authorize_staff(session, user, course_id, student_id, "create")
    # Serialize writes for this student, including retries and date validation.
    await session.execute(
        sa.select(User.id).where(User.id == student_id).with_for_update()
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "course_id": course_id,
                "student_id": student_id,
                **body.model_dump(mode="json", exclude={"request_key"}),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    row = (
        await session.execute(
            sa.select(CoachingAssignment).where(
                CoachingAssignment.assigned_by == user.id,
                CoachingAssignment.request_key == str(body.request_key),
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        if row.request_hash != fingerprint:
            raise fastapi.HTTPException(
                409,
                "This request key belongs to a different assignment. Reload before creating another.",
            )
        response.status_code = 200
        return serialize(row)
    if (
        body.due_date
        and body.due_date < datetime.datetime.now(datetime.timezone.utc).date()
    ):
        raise fastapi.HTTPException(422, "Choose today or a future due date (UTC).")
    row = CoachingAssignment(
        student_id=student_id,
        assigned_by=user.id,
        course_id=course_id,
        request_key=str(body.request_key),
        request_hash=fingerprint,
        **body.model_dump(exclude={"request_key"}),
        status="assigned",
        version=1,
    )
    session.add(row)
    try:
        await session.flush()
        audit(session, user.id, row, "coaching_assignment_created", body.reason)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise fastapi.HTTPException(
            409,
            "An assignment request changed concurrently. Retry with the same request key.",
        )
    await session.refresh(row)
    response.headers["Cache-Control"] = "no-store"
    return serialize(row)


@router.post("/courses/{course_id}/students/{student_id}/{assignment_id}/cancel")
async def cancel_assignment(
    course_id: str,
    student_id: int,
    assignment_id: int,
    body: AssignmentCancel,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    await authorize_staff(session, user, course_id, student_id, "update")
    row = (
        await session.execute(
            sa.select(CoachingAssignment)
            .where(
                CoachingAssignment.id == assignment_id,
                CoachingAssignment.student_id == student_id,
                CoachingAssignment.course_id == course_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Assignment not found.")
    if row.version != body.expected_version:
        raise fastapi.HTTPException(
            409, "Assignment changed. Reload its current status."
        )
    if row.status != "assigned":
        raise fastapi.HTTPException(409, "Only an open assignment can be cancelled.")
    row.status = "cancelled"
    row.version += 1
    row.closed_at = datetime.datetime.now(datetime.timezone.utc)
    audit(session, user.id, row, "coaching_assignment_cancelled", body.reason)
    await session.commit()
    return serialize(row)
