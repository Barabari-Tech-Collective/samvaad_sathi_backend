"""Course-scoped human placement review, independent of broad legacy routes."""

import datetime
import hashlib
import json
from typing import Literal
from uuid import UUID

import fastapi
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from pydantic import BaseModel, Field, field_validator

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.session import get_async_session
from src.models.db.user import User
from src.models.db.practice_governance import PracticeShortlist, PracticeShortlistEntry
from src.services import course_shortlists as service
from src.services.course_reporting import RosterFilters, roster_predicate

router = fastapi.APIRouter(prefix="/v2/course-shortlists", tags=["course-shortlists"])


class Create(BaseModel):
    request_key: UUID
    name: str = Field(min_length=3, max_length=160)
    opportunity: str = Field(min_length=3, max_length=200)
    role: str = Field(min_length=2, max_length=100)
    maximum_evidence_age_days: int = Field(default=90, ge=1, le=365)
    minimum_completed_interviews: int = Field(default=1, ge=1, le=20)
    expires_at: datetime.datetime

    @field_validator("name", "opportunity", "role", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("expires_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Use an explicit timezone for expiry.")
        return value.astimezone(service.UTC)


class Change(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class Decision(Change):
    student_id: int = Field(ge=1)
    decision: Literal["include", "exclude"]


async def shortlist(session, course_id, shortlist_id, lock=False):
    stmt = sa.select(PracticeShortlist).where(
        PracticeShortlist.id == shortlist_id, PracticeShortlist.course_id == course_id
    )
    if lock:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Course shortlist not found.")
    return row


def version(row, body):
    if row.version != body.expected_version:
        raise fastapi.HTTPException(
            409,
            "This shortlist changed. Refresh before reviewing or saving another decision.",
        )


def capabilities(user, row=None):
    return {
        "can_create": service.permission(user, "create"),
        "can_update": service.permission(user, "update"),
        "can_finalize": service.permission(user, "publish"),
        "can_archive": service.permission(user, "delete")
        and (row is None or row.created_by == user.id or is_admin_user(user)),
    }


@router.get("/courses/{course_id}")
async def lists(
    course_id: str,
    response: fastapi.Response,
    page: int = fastapi.Query(1, ge=1),
    limit: int = fastapi.Query(25, ge=1, le=100),
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    await service.authorize(user, course_id, session, "read")
    response.headers["Cache-Control"] = "no-store"
    predicate = PracticeShortlist.course_id == course_id
    total = (
        await session.execute(
            sa.select(sa.func.count()).select_from(PracticeShortlist).where(predicate)
        )
    ).scalar_one()
    rows = (
        (
            await session.execute(
                sa.select(PracticeShortlist)
                .where(predicate)
                .order_by(
                    PracticeShortlist.created_at.desc(), PracticeShortlist.id.desc()
                )
                .offset((page - 1) * limit)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [service.metadata(row) for row in rows],
        "total": total,
        "page": page,
        **capabilities(user),
    }


@router.post("/courses/{course_id}", status_code=201)
async def create(
    course_id: str,
    body: Create,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    await service.authorize(user, course_id, session, "create")
    canonical = {
        "course_id": course_id,
        **body.model_dump(mode="json", exclude={"request_key"}),
    }
    fingerprint = hashlib.sha256(
        json.dumps(canonical, sort_keys=True).encode()
    ).hexdigest()
    query = sa.select(PracticeShortlist).where(
        PracticeShortlist.created_by == user.id,
        PracticeShortlist.request_key == str(body.request_key),
    )
    existing = (await session.execute(query)).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise fastapi.HTTPException(
                409, "This creation request belongs to a different shortlist."
            )
        response.status_code = 200
        return service.metadata(existing)
    now = datetime.datetime.now(service.UTC)
    if not now < body.expires_at <= now + datetime.timedelta(days=365):
        raise fastapi.HTTPException(422, "Choose an expiry in the next 365 days.")
    rules = {
        key: getattr(body, key)
        for key in ("role", "maximum_evidence_age_days", "minimum_completed_interviews")
    }
    rules["decision_mode"] = "human_only"
    row_id = (
        await session.execute(
            insert(PracticeShortlist)
            .values(
                course_id=course_id,
                name=body.name,
                opportunity=body.opportunity,
                evidence_rules=rules,
                created_by=user.id,
                expires_at=body.expires_at,
                request_key=str(body.request_key),
                request_hash=fingerprint,
                status="draft",
                version=1,
            )
            .on_conflict_do_nothing(index_elements=["created_by", "request_key"])
            .returning(PracticeShortlist.id)
        )
    ).scalar_one_or_none()
    if row_id is None:
        existing = (await session.execute(query)).scalar_one()
        if existing.request_hash != fingerprint:
            raise fastapi.HTTPException(
                409, "This creation request belongs to a different shortlist."
            )
        response.status_code = 200
        return service.metadata(existing)
    row = await session.get(PracticeShortlist, row_id)
    service.audit(
        session,
        user,
        row,
        "course_shortlist_created",
        f"Created for {body.opportunity}",
    )
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return service.metadata(row)


@router.get("/courses/{course_id}/{shortlist_id}")
async def detail(
    course_id: str,
    shortlist_id: int,
    response: fastapi.Response,
    page: int = fastapi.Query(1, ge=1),
    limit: int = fastapi.Query(25, ge=1, le=100),
    q: str = fastapi.Query("", max_length=100),
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    authorized = await service.authorize(user, course_id, session, "read")
    row = await shortlist(session, course_id, shortlist_id)
    count, latest = service.recent_evidence(
        course_id, row.evidence_rules["maximum_evidence_age_days"]
    )
    predicate = roster_predicate(authorized, count, RosterFilters(q=q))
    if row.status != "draft":
        predicate = sa.and_(
            predicate,
            sa.exists(
                sa.select(PracticeShortlistEntry.id)
                .where(
                    PracticeShortlistEntry.shortlist_id == row.id,
                    PracticeShortlistEntry.student_id == User.id,
                )
                .correlate(User)
            ),
        )
    total = (
        await session.execute(
            sa.select(sa.func.count()).select_from(User).where(predicate)
        )
    ).scalar_one()
    entries = (
        await session.execute(
            sa.select(
                User.id,
                User.name,
                User.university,
                count.label("completed"),
                latest.label("latest"),
                PracticeShortlistEntry.decision,
                PracticeShortlistEntry.reason,
                PracticeShortlistEntry.evidence_snapshot,
            )
            .outerjoin(
                PracticeShortlistEntry,
                sa.and_(
                    PracticeShortlistEntry.shortlist_id == row.id,
                    PracticeShortlistEntry.student_id == User.id,
                ),
            )
            .where(predicate)
            .order_by(User.id)
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()
    service.audit(
        session,
        user,
        row,
        "course_shortlist_viewed",
        "Opened current consented placement review.",
    )
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {
        "shortlist": service.metadata(row),
        "items": [
            {
                "student": {
                    "id": item.id,
                    "name": item.name,
                    "college": item.university,
                },
                "evidence": service.evidence(
                    item.completed,
                    item.latest,
                    row.evidence_rules["minimum_completed_interviews"],
                ),
                "decision": item.decision,
                "reason": item.reason,
                "decision_evidence": item.evidence_snapshot,
            }
            for item in entries
        ],
        "total": total,
        "page": page,
        **capabilities(user, row),
        "notice": "Human review only. Missing or insufficient practice is not a low score or an automatic rejection. Only explicit decisions are finalized; undecided students stay undecided. Current sharing and enrollment checks apply even after finalization.",
    }


@router.put("/courses/{course_id}/{shortlist_id}/decision")
async def decide(
    course_id: str,
    shortlist_id: int,
    body: Decision,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    authorized = await service.authorize(user, course_id, session, "update")
    row = await shortlist(session, course_id, shortlist_id, True)
    service.require_draft(row)
    version(row, body)
    count, latest = service.recent_evidence(
        course_id, row.evidence_rules["maximum_evidence_age_days"]
    )
    student = (
        await session.execute(
            sa.select(User.id, count.label("completed"), latest.label("latest")).where(
                authorized, User.id == body.student_id
            )
        )
    ).one_or_none()
    if student is None:
        raise fastapi.HTTPException(404, "Shared placement student not found.")
    entry = (
        await session.execute(
            sa.select(PracticeShortlistEntry).where(
                PracticeShortlistEntry.shortlist_id == row.id,
                PracticeShortlistEntry.student_id == body.student_id,
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        entry = PracticeShortlistEntry(shortlist_id=row.id, student_id=body.student_id)
        session.add(entry)
    entry.decision = body.decision
    entry.reason = body.reason
    entry.evidence_snapshot = service.evidence(
        student.completed,
        student.latest,
        row.evidence_rules["minimum_completed_interviews"],
    )
    entry.decided_by = user.id
    entry.decided_at = datetime.datetime.now(service.UTC)
    row.version += 1
    service.audit(
        session,
        user,
        row,
        "course_shortlist_decided",
        body.reason,
        {"student_id": body.student_id, "decision": body.decision},
    )
    await session.commit()
    return {
        "student_id": body.student_id,
        "decision": body.decision,
        "version": row.version,
    }


@router.post("/courses/{course_id}/{shortlist_id}/finalize")
async def finalize(
    course_id: str,
    shortlist_id: int,
    body: Change,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    authorized = await service.authorize(user, course_id, session, "publish")
    row = await shortlist(session, course_id, shortlist_id, True)
    service.require_draft(row)
    version(row, body)
    total = (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(PracticeShortlistEntry)
            .where(PracticeShortlistEntry.shortlist_id == row.id)
        )
    ).scalar_one()
    visible = (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(PracticeShortlistEntry)
            .join(User, User.id == PracticeShortlistEntry.student_id)
            .where(PracticeShortlistEntry.shortlist_id == row.id, authorized)
        )
    ).scalar_one()
    if not total:
        raise fastapi.HTTPException(
            409, "Record at least one human decision before finalizing."
        )
    if visible != total:
        raise fastapi.HTTPException(
            409,
            "Some recorded decisions are no longer reviewable within your current access and sharing scope. Refresh; the draft owner may archive it and create a replacement. No decisions were silently removed.",
        )
    row.status = "locked"
    row.version += 1
    row.locked_by = user.id
    row.locked_at = datetime.datetime.now(service.UTC)
    service.audit(session, user, row, "course_shortlist_finalized", body.reason)
    await session.commit()
    return service.metadata(row)


@router.post("/courses/{course_id}/{shortlist_id}/archive")
async def archive(
    course_id: str,
    shortlist_id: int,
    body: Change,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    await service.authorize(user, course_id, session, "delete")
    row = await shortlist(session, course_id, shortlist_id, True)
    version(row, body)
    if row.status != "draft" or not (row.created_by == user.id or is_admin_user(user)):
        raise fastapi.HTTPException(
            409, "Only the draft owner or an administrator can archive a draft."
        )
    row.status = "archived"
    row.version += 1
    service.audit(session, user, row, "course_shortlist_archived", body.reason)
    await session.commit()
    return service.metadata(row)
