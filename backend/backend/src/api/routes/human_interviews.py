"""Human appointments isolated from voice AI assessment state and grading."""

import datetime as dt
import hashlib
import json
from typing import Literal
from uuid import UUID
import fastapi
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from pydantic import BaseModel, Field, ConfigDict, field_validator
from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.human_interview import HumanInterview
from src.models.db.user import User
from src.services import human_interviews as service

router = fastapi.APIRouter(prefix="/v2/human-interviews", tags=["human-interviews"])


class Create(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    student_id: int = Field(ge=1)
    title: str = Field(min_length=3, max_length=160)
    meet_url: str
    starts_at: dt.datetime
    ends_at: dt.datetime

    @field_validator("title", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("meet_url", mode="before")
    @classmethod
    def validate_link(cls, value):
        return service.meet_url(value)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("An explicit timezone is required.")
        return value.astimezone(service.UTC)


class Close(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    status: Literal["completed", "cancelled", "no_show", "technical_issue"]
    reason: str = Field(min_length=10, max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


def no_store(response):
    response.headers["Cache-Control"] = "no-store"


async def staff_row(
    session, user, course_id, appointment_id, operation, lock=False, historical=False
):
    predicate = await service.authorize(
        user, course_id, session, operation, historical=historical
    )
    statement = (
        sa.select(HumanInterview)
        .join(User, User.id == HumanInterview.student_id)
        .where(
            HumanInterview.id == appointment_id,
            HumanInterview.course_id == course_id,
            predicate,
            service.student_only(),
        )
    )
    if not is_admin_user(user):
        statement = statement.where(HumanInterview.host_id == user.id)
    if lock:
        statement = statement.with_for_update(of=HumanInterview)
    row = (await session.execute(statement)).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(
            404, "Human interview not found within your current access."
        )
    return row


@router.get("/courses/{course_id}")
async def staff_list(
    course_id: str,
    response: fastapi.Response,
    page: int = fastapi.Query(1, ge=1),
    limit: int = fastapi.Query(20, ge=1, le=50),
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    predicate = await service.authorize(
        user, course_id, session, "read", historical=True
    )
    conditions = [
        HumanInterview.course_id == course_id,
        predicate,
        service.student_only(),
    ]
    if not is_admin_user(user):
        conditions.append(HumanInterview.host_id == user.id)
    from src.services.sampark_enrollment_sync import freshness_predicate

    current_enrollment = sa.and_(
        User.enrolled_course_ids.contains([course_id]), freshness_predicate()
    )
    base = (
        sa.select(
            HumanInterview,
            User.name.label("student_name"),
            current_enrollment.label("current_enrollment"),
        )
        .join(User, User.id == HumanInterview.student_id)
        .where(*conditions)
    )
    total = (
        await session.execute(sa.select(sa.func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            base.order_by(HumanInterview.starts_at.desc(), HumanInterview.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()
    now = dt.datetime.now(service.UTC)
    no_store(response)
    return {
        "items": [
            service.metadata(row, now, can_manage=service.permitted(user, "update"))
            | {
                "student_name": name,
                "can_join": bool(current) and service.joinable(row, now),
                "can_report_result": bool(current),
                "enrollment_current": bool(current),
            }
            for row, name, current in rows
        ],
        "total": total,
        "can_create": service.permitted(user, "create"),
        "notice": service.NOTICE,
    }


@router.get("/courses/{course_id}/students")
async def eligible_students(
    course_id: str,
    response: fastapi.Response,
    q: str = fastapi.Query("", max_length=100),
    page: int = fastapi.Query(1, ge=1),
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    predicate = await service.authorize(user, course_id, session, "create")
    conditions = [predicate, service.student_only()]
    if q.strip():
        conditions.append(
            User.name.ilike(
                "%"
                + q.strip()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
                + "%",
                escape="\\",
            )
        )
    rows = (
        await session.execute(
            sa.select(User.id, User.name)
            .where(*conditions)
            .order_by(User.id)
            .offset((page - 1) * 25)
            .limit(26)
        )
    ).all()
    no_store(response)
    return {
        "items": [{"id": row.id, "name": row.name} for row in rows[:25]],
        "has_more": len(rows) > 25,
    }


@router.post("/courses/{course_id}", status_code=201)
async def create(
    course_id: str,
    body: Create,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    predicate = await service.authorize(user, course_id, session, "create")
    student = (
        await session.execute(
            sa.select(User.id).where(
                User.id == body.student_id, predicate, service.student_only()
            )
        )
    ).scalar_one_or_none()
    if student is None or student == user.id:
        raise fastapi.HTTPException(404, "Eligible student not found.")
    canonical = {
        "course_id": course_id,
        **body.model_dump(mode="json", exclude={"request_key"}),
    }
    fingerprint = hashlib.sha256(
        json.dumps(canonical, sort_keys=True).encode()
    ).hexdigest()
    request = sa.select(HumanInterview).where(
        HumanInterview.host_id == user.id,
        HumanInterview.request_key == str(body.request_key),
    )
    existing = (await session.execute(request)).scalar_one_or_none()
    now = dt.datetime.now(service.UTC)
    no_store(response)
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise fastapi.HTTPException(
                409, "This request was already used for a different appointment."
            )
        response.status_code = 200
        return service.metadata(
            existing, now, can_manage=service.permitted(user, "update")
        )
    if not now < body.starts_at <= now + dt.timedelta(days=180):
        raise fastapi.HTTPException(422, "Choose a future start within 180 days.")
    if (
        not dt.timedelta(minutes=5)
        <= body.ends_at - body.starts_at
        <= dt.timedelta(hours=3)
    ):
        raise fastapi.HTTPException(
            422, "Choose a duration between 5 minutes and 3 hours."
        )
    # Serialize all scheduling on participant rows, ordered to avoid deadlocks.
    # This prevents simultaneous requests from double-booking either participant.
    await session.execute(
        sa.select(User.id)
        .where(User.id.in_([user.id, student]))
        .order_by(User.id)
        .with_for_update()
    )
    overlap = (
        await session.execute(
            sa.select(HumanInterview.id)
            .where(
                HumanInterview.status == "scheduled",
                HumanInterview.starts_at < body.ends_at,
                HumanInterview.ends_at > body.starts_at,
                sa.or_(
                    HumanInterview.student_id.in_([user.id, student]),
                    HumanInterview.host_id.in_([user.id, student]),
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    # A concurrent retry may have been inserted while waiting for participant locks.
    existing = (await session.execute(request)).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise fastapi.HTTPException(
                409, "This request was already used for a different appointment."
            )
        response.status_code = 200
        return service.metadata(
            existing, now, can_manage=service.permitted(user, "update")
        )
    if overlap is not None:
        raise fastapi.HTTPException(
            409,
            "The student or host already has a scheduled interview in this time window.",
        )
    row_id = (
        await session.execute(
            insert(HumanInterview)
            .values(
                **body.model_dump(exclude={"request_key"}),
                course_id=course_id,
                host_id=user.id,
                request_key=str(body.request_key),
                request_hash=fingerprint,
            )
            .returning(HumanInterview.id)
        )
    ).scalar_one()
    row = await session.get(HumanInterview, row_id)
    service.audit(
        session,
        user,
        row,
        "human_interview_scheduled",
        "Host scheduled an external Google Meet interview; no attendance inferred.",
    )
    await session.commit()
    return service.metadata(row, now, can_manage=service.permitted(user, "update"))


@router.post("/courses/{course_id}/{appointment_id}/close")
async def close(
    course_id: str,
    appointment_id: int,
    body: Close,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    row = await staff_row(
        session,
        user,
        course_id,
        appointment_id,
        "update",
        lock=True,
        historical=body.status == "cancelled",
    )
    if row.version != body.expected_version or row.status != "scheduled":
        raise fastapi.HTTPException(
            409, "This appointment changed or is closed. Refresh before acting."
        )
    now = dt.datetime.now(service.UTC)
    if body.status == "completed" and now < row.starts_at:
        raise fastapi.HTTPException(
            409, "A future interview cannot be reported completed."
        )
    if body.status == "no_show" and now < row.ends_at:
        raise fastapi.HTTPException(
            409, "Wait until the scheduled end before reporting a no-show."
        )
    row.status, row.close_reason, row.closed_by, row.closed_at = (
        body.status,
        body.reason,
        user.id,
        now,
    )
    row.version += 1
    service.audit(session, user, row, "human_interview_host_reported", body.reason)
    await session.commit()
    no_store(response)
    return service.metadata(row, now)


@router.get("/courses/{course_id}/{appointment_id}/join")
async def staff_join(
    course_id: str,
    appointment_id: int,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    row = await staff_row(session, user, course_id, appointment_id, "read", lock=True)
    return await release_link(row, user, response, session)


async def release_link(row, user, response, session):
    if not service.joinable(row, dt.datetime.now(service.UTC)):
        raise fastapi.HTTPException(
            409,
            "Joining opens 15 minutes before the start and closes at the scheduled end. Closed appointments cannot be joined.",
        )
    service.audit(
        session,
        user,
        row,
        "human_interview_link_requested",
        "Released the external meeting link; this is not proof of attendance.",
    )
    await session.commit()
    no_store(response)
    # Revalidate stored URLs too: direct database edits never become redirects.
    try:
        url = service.meet_url(row.meet_url)
    except ValueError:
        raise fastapi.HTTPException(
            409, "This meeting link needs to be replaced by your host."
        )
    return {"url": url, "notice": service.NOTICE}


@router.get("/me")
async def my_interviews(
    response: fastapi.Response,
    page: int = fastapi.Query(1, ge=1),
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    # Correlated fresh enrollment verification applies to reads as well as joins.
    # JSONB contains([column]) cannot bind a SQL column as JSON. Use the JSONB
    # existence operator for each row's scalar course ID instead.
    from src.services.sampark_enrollment_sync import freshness_predicate

    predicate = sa.and_(
        User.enrolled_course_ids.op("?")(HumanInterview.course_id),
        freshness_predicate(),
    )
    base = (
        sa.select(HumanInterview, User.name)
        .join(User, User.id == HumanInterview.student_id)
        .where(HumanInterview.student_id == user.id, predicate, service.student_only())
    )
    total = (
        await session.execute(sa.select(sa.func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            base.order_by(HumanInterview.starts_at.desc(), HumanInterview.id.desc())
            .offset((page - 1) * 10)
            .limit(10)
        )
    ).all()
    no_store(response)
    return {
        "items": [
            service.metadata(row, dt.datetime.now(service.UTC)) for row, _ in rows
        ],
        "total": total,
        "notice": service.NOTICE,
    }


@router.get("/me/{appointment_id}/join")
async def student_join(
    appointment_id: int,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    from src.services.sampark_enrollment_sync import freshness_predicate

    row = (
        await session.execute(
            sa.select(HumanInterview)
            .join(User, User.id == HumanInterview.student_id)
            .where(
                HumanInterview.id == appointment_id,
                HumanInterview.student_id == user.id,
                User.enrolled_course_ids.op("?")(HumanInterview.course_id),
                freshness_predicate(),
                service.student_only(),
            )
            .with_for_update(of=HumanInterview)
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(
            404,
            "Interview not found. Ask your program team to verify current enrollment.",
        )
    host = await session.get(User, row.host_id)
    try:
        if host is None:
            raise fastapi.HTTPException(403)
        await staff_row(session, host, row.course_id, row.id, "read")
    except fastapi.HTTPException:
        raise fastapi.HTTPException(
            409,
            "Your host's current interview access needs verification. Contact your program team.",
        )
    return await release_link(row, user, response, session)
