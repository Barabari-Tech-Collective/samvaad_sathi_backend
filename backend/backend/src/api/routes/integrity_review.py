"""Scoped staff case work and ownership-only learner explanation/appeal."""

import datetime as dt
from typing import Literal
from uuid import UUID
import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.integrity_case import IntegrityCase
from src.models.db.interview import Interview
from src.models.db.user import User
from src.services import integrity_review as service
from src.services.course_access import grants_for, student_predicate

router = fastapi.APIRouter(prefix="/v2/integrity", tags=["assessment-integrity"])
Source = Literal["technical_context", "interviewer_observation", "verified_artifact"]
Outcome = Literal["clear", "inconclusive", "concern"]


class Reason(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    reason: str = Field(min_length=10, max_length=2000)

    @field_validator("reason", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class Open(Reason):
    interview_id: int = Field(strict=True, ge=1)
    source: Source
    shared_evidence_confirmed: Literal[True]


class StaffAction(Reason):
    expected_version: int = Field(strict=True, ge=1)
    action: Literal["add_evidence", "decide", "resolve_appeal", "reopen", "redact"]
    target_index: int | None = Field(default=None, strict=True, ge=0)
    source: Source | None = None
    outcome: Outcome | None = None
    shared_evidence_confirmed: Literal[True]

    @model_validator(mode="after")
    def exact_fields(self):
        if (self.action == "redact") != (self.target_index is not None):
            raise ValueError("Only redaction actions require a target entry index")
        if (self.action == "add_evidence") != (self.source is not None):
            raise ValueError("Only evidence actions require an evidence source")
        if (self.action in {"decide", "resolve_appeal"}) != (self.outcome is not None):
            raise ValueError("Only decision actions require an outcome")
        return self


class LearnerAction(Reason):
    expected_version: int = Field(strict=True, ge=1)
    action: Literal["explain", "appeal"]


def no_store(response):
    response.headers["Cache-Control"] = "no-store"


async def staff_case(session, user, course, case_id, operation, *, lock=False):
    predicate = await service.scope(user, course, session, operation)
    stmt = sa.select(IntegrityCase).where(
        IntegrityCase.course_id == course, IntegrityCase.id == case_id, predicate
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Review case not found in your current scope.")
    if row.student_id == user.id:
        raise fastapi.HTTPException(403, "Use the learner view for your own case.")
    return row


@router.get("/courses/{course_id}/cases")
async def list_cases(
    course_id: str,
    response: fastapi.Response,
    page: int = fastapi.Query(1, ge=1),
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    no_store(response)
    predicate = await service.scope(user, course_id, session, "read")
    where = (
        IntegrityCase.course_id == course_id,
        IntegrityCase.student_id != user.id,
        predicate,
    )
    total = (
        await session.scalar(
            sa.select(sa.func.count()).select_from(IntegrityCase).where(*where)
        )
    ) or 0
    rows = (
        await session.scalars(
            sa.select(IntegrityCase)
            .where(*where)
            .order_by(IntegrityCase.id.desc())
            .offset((page - 1) * 20)
            .limit(20)
        )
    ).all()
    now = dt.datetime.now(service.UTC)
    for row in rows:
        service.audit(session, user, row, "read")
    await session.commit()
    return {
        "items": [service.serialize(row, user, now, staff=True) for row in rows],
        "total": total,
        "can_create": service.permitted(user, "create"),
        "notice": service.NOTICE,
    }


@router.post("/courses/{course_id}/cases")
async def open_case(
    course_id: str,
    body: Open,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    no_store(response)
    await service.scope(user, course_id, session, "create")
    # Lock sitting first to serialize simultaneous open requests, including
    # different staff/request keys. Only freshly enrolled, in-scope assessments.
    grants = await grants_for(user, session)
    predicate = student_predicate(course_id, grants, global_access=is_admin_user(user))
    sitting = (
        await session.execute(
            sa.select(Interview, User)
            .join(User, User.id == Interview.user_id)
            .where(
                Interview.id == body.interview_id,
                Interview.course_id == course_id,
                Interview.interview_mode == "assessment",
                Interview.status.in_(["completed", "terminated"]),
                Interview.user_id != user.id,
                predicate,
            )
            .with_for_update(of=Interview)
        )
    ).one_or_none()
    if sitting is None:
        raise fastapi.HTTPException(
            404, "Finished assessment not found in your current course scope."
        )
    interview, student = sitting
    existing = await session.scalar(
        sa.select(IntegrityCase).where(IntegrityCase.interview_id == interview.id)
    )
    digest = service.fingerprint(body)
    if existing:
        await staff_case(session, user, course_id, existing.id, "read")
        if (
            existing.opened_by != user.id
            or existing.request_key != str(body.request_key)
            or existing.request_hash != digest
        ):
            raise fastapi.HTTPException(
                409,
                "This assessment already has a review case. Open the existing case.",
            )
        return service.serialize(
            existing, user, dt.datetime.now(service.UTC), staff=True
        )
    now = dt.datetime.now(service.UTC)
    history = [
        {
            "kind": "opened",
            "source": body.source,
            "reason": body.reason,
            "actor_id": user.id,
            "at": now.isoformat(),
        }
    ]
    signals = [
        {k: event.get(k) for k in ("eventId", "reason", "recordedAt")}
        for event in (interview.integrity_events or [])[:100]
        if isinstance(event, dict)
    ]
    if signals:
        history.append(
            {
                "kind": "browser_observations",
                "source": "browser_signal",
                "reason": "Unverified browser observations. These do not establish cheating.",
                "signals": signals,
                "at": now.isoformat(),
            }
        )
    row = IntegrityCase(
        interview_id=interview.id,
        student_id=student.id,
        course_id=course_id,
        college=student.university,
        opened_by=user.id,
        request_key=str(body.request_key),
        request_hash=digest,
        state="open",
        version=1,
        history=history,
    )
    session.add(row)
    await session.flush()
    service.audit(session, user, row, "opened")
    await session.commit()
    return service.serialize(row, user, now, staff=True)


@router.post("/courses/{course_id}/cases/{case_id}/actions")
async def staff_action(
    course_id: str,
    case_id: int,
    body: StaffAction,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    no_store(response)
    operation = {
        "add_evidence": "update",
        "decide": "publish",
        "resolve_appeal": "resolve_appeal",
        "reopen": "update",
        "redact": "update",
    }[body.action]
    row = await staff_case(session, user, course_id, case_id, operation, lock=True)
    now = dt.datetime.now(service.UTC)
    if service.transition(row, user, body, now, staff=True):
        service.audit(session, user, row, body.action)
    await session.commit()
    return service.serialize(row, user, now, staff=True)


@router.get("/me/notice-count")
async def notice_count(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    no_store(response)
    count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(IntegrityCase)
        .where(IntegrityCase.student_id == user.id, IntegrityCase.state != "resolved")
    )
    return {"count": count or 0}


@router.get("/me/cases")
async def learner_cases(
    response: fastapi.Response,
    page: int = fastapi.Query(1, ge=1),
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    no_store(response)
    # Historical ownership is sufficient. Enrollment withdrawal must not remove
    # the learner's access to their evidence, explanation or appeal rights.
    predicate = IntegrityCase.student_id == user.id
    rows = (
        await session.scalars(
            sa.select(IntegrityCase)
            .where(predicate)
            .order_by(IntegrityCase.id.desc())
            .offset((page - 1) * 20)
            .limit(20)
        )
    ).all()
    total = await session.scalar(
        sa.select(sa.func.count()).select_from(IntegrityCase).where(predicate)
    )
    now = dt.datetime.now(service.UTC)
    return {
        "items": [service.serialize(row, user, now) for row in rows],
        "total": total or 0,
        "notice": service.NOTICE,
    }


@router.post("/me/cases/{case_id}/actions")
async def learner_action(
    case_id: int,
    body: LearnerAction,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    no_store(response)
    row = await session.scalar(
        sa.select(IntegrityCase)
        .where(IntegrityCase.id == case_id, IntegrityCase.student_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise fastapi.HTTPException(404, "Review case not found.")
    now = dt.datetime.now(service.UTC)
    if service.transition(row, user, body, now, staff=False):
        service.audit(session, user, row, body.action)
    await session.commit()
    return service.serialize(row, user, now)
