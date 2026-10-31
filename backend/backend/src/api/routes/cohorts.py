"""Cohorts: staff-managed groups of learners inside one course (architecture pack sec 4, F31).

A cohort is grouping, never access. Each person sees only the members their own course and
college grant covers; anyone else is reported as a count, never named. Viewing needs the
course workspace "read" permission and managing needs "update" (administrators pass). Every
change is versioned and audited with a reason.
"""

import datetime as dt
import hashlib
import json
from typing import Literal
from uuid import UUID, uuid5

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.session import get_async_session
from src.models.db.coaching_assignment import CoachingAssignment
from src.models.db.cohort import Cohort, CohortMember
from src.models.db.job_profile import JobProfile
from src.models.db.mock_assignment import MockInterviewAssignment
from src.models.db.opportunity import OpportunityCandidate
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.user import User
from src.services.course_access import grants_for, require_course, student_predicate
from src.services.feature_access import permits, require_permission
from src.services.people import student_only

router = fastapi.APIRouter(prefix="/v2/cohorts", tags=["cohorts"])

NOT_FOUND = "Cohort not found in this course."


def _trim(value):
    return value.strip() if isinstance(value, str) else value


class Create(BaseModel):
    request_key: UUID
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(None, max_length=1000)

    _trim = field_validator("name", "description", mode="before")(classmethod(lambda c, v: _trim(v)))


class Members(BaseModel):
    expected_version: int = Field(ge=1)
    add: list[int] = Field(default_factory=list, max_length=500)
    remove: list[int] = Field(default_factory=list, max_length=500)
    reason: str = Field(min_length=10, max_length=1000)

    _trim = field_validator("reason", mode="before")(classmethod(lambda c, v: _trim(v)))


class Change(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(None, min_length=2, max_length=120)
    description: str | None = Field(None, max_length=1000)
    archived: bool | None = None
    reason: str = Field(min_length=10, max_length=1000)

    _trim = field_validator("name", "description", "reason", mode="before")(
        classmethod(lambda c, v: _trim(v))
    )


def can(user, operation):
    return is_admin_user(user) or (
        isinstance(user.feature_permissions, dict)
        and permits(user.feature_permissions, "course_workspace", operation)
    )


async def scope(user, course_id, session, operation="read"):
    """Refuse unless this person may do `operation` here; return who they may see."""
    if not can(user, operation):
        raise fastapi.HTTPException(
            403, "Cohorts need the course workspace permission for this action."
        )
    admin = is_admin_user(user)
    grants = () if admin else await grants_for(user, session)
    require_course(course_id, grants, user)
    return sa.and_(student_predicate(course_id, grants, global_access=admin), student_only())


async def load(session, course_id, cohort_id, lock=False):
    stmt = sa.select(Cohort).where(Cohort.id == cohort_id, Cohort.course_id == course_id)
    if lock:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, NOT_FOUND)
    return row


def audit(session, user, row, action, reason, details=None):
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id,
            action=action,
            purpose="cohort",
            target_type="cohort",
            target_id=str(row.id),
            reason=reason,
            event_metadata={"course_id": row.course_id, "version": row.version, **(details or {})},
        )
    )


def summary(row, visible, hidden):
    return {
        "id": row.id,
        "course_id": row.course_id,
        "name": row.name,
        "description": row.description,
        "version": row.version,
        "archived": row.archived_at is not None,
        "created_at": row.created_at,
        "members": visible,
        # Members outside this person's colleges are counted, never named.
        "members_outside_your_scope": hidden,
    }


async def counts(session, cohort_ids, predicate):
    if not cohort_ids:
        return {}
    rows = (
        await session.execute(
            sa.select(
                CohortMember.cohort_id,
                sa.func.count(CohortMember.id),
                sa.func.count(CohortMember.id).filter(predicate),
            )
            .join(User, User.id == CohortMember.student_id)
            .where(CohortMember.cohort_id.in_(cohort_ids))
            .group_by(CohortMember.cohort_id)
        )
    ).all()
    return {cid: (int(seen), int(total) - int(seen)) for cid, total, seen in rows}


@router.get("/courses/{course_id}")
async def list_cohorts(
    course_id: str,
    response: fastapi.Response,
    archived: bool = False,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    predicate = await scope(user, course_id, session)
    stmt = sa.select(Cohort).where(Cohort.course_id == course_id)
    stmt = stmt.where(Cohort.archived_at.is_not(None) if archived else Cohort.archived_at.is_(None))
    rows = (await session.execute(stmt.order_by(Cohort.name, Cohort.id))).scalars().all()
    seen = await counts(session, [r.id for r in rows], predicate)
    return {
        "items": [summary(r, *seen.get(r.id, (0, 0))) for r in rows],
        "can_manage": can(user, "update"),
    }


@router.post("/courses/{course_id}", status_code=201)
async def create_cohort(
    course_id: str,
    body: Create,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    await scope(user, course_id, session, "update")
    existing = (
        await session.execute(
            sa.select(Cohort).where(
                Cohort.course_id == course_id, Cohort.request_key == str(body.request_key)
            )
        )
    ).scalar_one_or_none()
    if existing:
        return summary(existing, 0, 0)
    row = Cohort(
        course_id=course_id,
        name=body.name,
        description=body.description or None,
        request_key=str(body.request_key),
        created_by=user.id,
    )
    session.add(row)
    await session.flush()
    audit(session, user, row, "cohort_created", f"Created cohort {body.name}.")
    await session.commit()
    await session.refresh(row)
    return summary(row, 0, 0)


@router.get("/courses/{course_id}/learners")
async def find_learners(
    course_id: str,
    response: fastapi.Response,
    q: str = fastapi.Query("", max_length=100),
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Learners enrolled in this course within this person's scope, to add to a cohort.
    Names and colleges only; no evidence."""
    response.headers["Cache-Control"] = "no-store"
    predicate = await scope(user, course_id, session, "update")
    stmt = sa.select(User.id, User.name, User.university).where(predicate)
    term = q.strip()
    if term:
        literal = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(
            sa.or_(
                User.name.ilike(f"%{literal}%", escape="\\"),
                User.university.ilike(f"%{literal}%", escape="\\"),
            )
        )
    rows = (await session.execute(stmt.order_by(User.name, User.id).limit(25))).all()
    return {"items": [{"id": r.id, "name": r.name, "college": r.university} for r in rows]}


@router.get("/courses/{course_id}/{cohort_id}")
async def cohort_detail(
    course_id: str,
    cohort_id: int,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    predicate = await scope(user, course_id, session)
    row = await load(session, course_id, cohort_id)
    members = (
        await session.execute(
            sa.select(User.id, User.name, User.university, CohortMember.added_at)
            .join(CohortMember, CohortMember.student_id == User.id)
            .where(CohortMember.cohort_id == row.id, predicate)
            .order_by(User.name, User.id)
        )
    ).all()
    total = (
        await session.execute(
            sa.select(sa.func.count(CohortMember.id)).where(CohortMember.cohort_id == row.id)
        )
    ).scalar_one()
    return {
        **summary(row, len(members), total - len(members)),
        "items": [
            {"id": m.id, "name": m.name, "college": m.university, "added_at": m.added_at}
            for m in members
        ],
        "can_manage": can(user, "update") and row.archived_at is None,
    }


@router.put("/courses/{course_id}/{cohort_id}/members")
async def change_members(
    course_id: str,
    cohort_id: int,
    body: Members,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    predicate = await scope(user, course_id, session, "update")
    row = await load(session, course_id, cohort_id, lock=True)
    if row.archived_at is not None:
        raise fastapi.HTTPException(409, "Restore this cohort before changing its members.")
    if row.version != body.expected_version:
        raise fastapi.HTTPException(409, "This cohort changed. Refresh before saving.")
    add, remove = set(body.add), set(body.remove)
    if not add and not remove:
        raise fastapi.HTTPException(422, "Choose someone to add or remove.")
    if add & remove:
        raise fastapi.HTTPException(422, "A learner cannot be added and removed at once.")
    # Only learners this person may see in this course can be added or removed.
    reachable = set(
        (
            await session.execute(
                sa.select(User.id).where(User.id.in_(add | remove), predicate)
            )
        ).scalars()
    )
    if (add | remove) - reachable:
        raise fastapi.HTTPException(
            422, "Some learners are not enrolled in this course within your access."
        )
    already = set(
        (
            await session.execute(
                sa.select(CohortMember.student_id).where(
                    CohortMember.cohort_id == row.id,
                    CohortMember.student_id.in_(add | remove),
                )
            )
        ).scalars()
    )
    for student_id in sorted(add - already):
        session.add(CohortMember(cohort_id=row.id, student_id=student_id, added_by=user.id))
    if remove & already:
        await session.execute(
            sa.delete(CohortMember).where(
                CohortMember.cohort_id == row.id,
                CohortMember.student_id.in_(remove & already),
            )
        )
    row.version += 1
    audit(
        session, user, row, "cohort_members_changed", body.reason,
        {"added": sorted(add - already), "removed": sorted(remove & already)},
    )
    await session.commit()
    return {"id": row.id, "version": row.version}


@router.patch("/courses/{course_id}/{cohort_id}")
async def change_cohort(
    course_id: str,
    cohort_id: int,
    body: Change,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    await scope(user, course_id, session, "update")
    row = await load(session, course_id, cohort_id, lock=True)
    if row.version != body.expected_version:
        raise fastapi.HTTPException(409, "This cohort changed. Refresh before saving.")
    before = {"name": row.name, "description": row.description, "archived": row.archived_at is not None}
    if body.name is not None:
        row.name = body.name
    if body.description is not None:
        row.description = body.description or None
    if body.archived is not None:
        # Archiving keeps members and history; it only stops changes and hides it from lists.
        row.archived_at = dt.datetime.now(dt.timezone.utc) if body.archived else None
    row.version += 1
    audit(session, user, row, "cohort_changed", body.reason, {"before": before})
    await session.commit()
    return {"id": row.id, "version": row.version}


async def learner_cohorts(session, student_id, courses):
    """The active cohorts a learner is in, within courses the viewer may act in."""
    if not courses:
        return []
    rows = (
        await session.execute(
            sa.select(Cohort.id, Cohort.name, Cohort.course_id)
            .join(CohortMember, CohortMember.cohort_id == Cohort.id)
            .where(
                CohortMember.student_id == student_id,
                Cohort.course_id.in_(courses),
                Cohort.archived_at.is_(None),
            )
            .order_by(Cohort.name)
        )
    ).all()
    return [{"id": r.id, "name": r.name, "course_id": r.course_id} for r in rows]


class Assign(BaseModel):
    """One action for every member of a cohort that this person may act for."""

    request_key: UUID
    kind: Literal["drill", "mock", "opening"]
    module: Literal["warm_up", "think_fast"] | None = None
    job_profile_id: int | None = Field(None, ge=1)
    opportunity_id: int | None = Field(None, ge=1)
    reason: str = Field(min_length=10, max_length=1000)
    note: str | None = Field(None, max_length=2000)
    due_date: dt.date | None = None

    _trim = field_validator("reason", "note", mode="before")(classmethod(lambda c, v: _trim(v)))


def _fingerprint(course_id, student_id, body):
    return hashlib.sha256(
        json.dumps(
            {"course_id": course_id, "student_id": student_id,
             **body.model_dump(mode="json", exclude={"request_key"})},
            sort_keys=True,
        ).encode()
    ).hexdigest()


@router.post("/courses/{course_id}/{cohort_id}/assign")
async def assign_to_cohort(
    course_id: str,
    cohort_id: int,
    body: Assign,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Give every visible member the same drill or mock, or add them to an opening.

    Each member is checked under the usual rule for that action (coaching needs the
    learner's coaching consent; an opening needs a placement responsibility), so a cohort
    never widens anyone's access. Retrying with the same request_key creates nothing twice."""
    visible = await scope(user, course_id, session)
    row = await load(session, course_id, cohort_id)
    if row.archived_at is not None:
        raise fastapi.HTTPException(409, "Restore this cohort before assigning to it.")
    members = (
        await session.execute(
            sa.select(User.id, User.name)
            .join(CohortMember, CohortMember.student_id == User.id)
            .where(CohortMember.cohort_id == row.id, visible)
            .order_by(User.name, User.id)
        )
    ).all()
    total = (
        await session.execute(
            sa.select(sa.func.count(CohortMember.id)).where(CohortMember.cohort_id == row.id)
        )
    ).scalar_one()
    names = {m.id: m.name for m in members}
    done, skipped = [], []

    if body.kind in ("drill", "mock"):
        if (body.kind == "drill") != (body.module is not None) or (
            (body.kind == "mock") != (body.job_profile_id is not None)
        ):
            raise fastapi.HTTPException(422, "Choose a drill for a drill, or a mock template for a mock.")
        if body.due_date and body.due_date < dt.datetime.now(dt.timezone.utc).date():
            raise fastapi.HTTPException(422, "Choose today or a future due date (UTC).")
        if not is_admin_user(user):
            require_permission(user.feature_permissions, "practice_coaching", "create")
        if body.kind == "mock":
            template = await session.get(JobProfile, body.job_profile_id)
            if template is None or template.interview_mode != "practice" or template.status != "published":
                raise fastapi.HTTPException(422, "Choose a published mock interview template.")
        from src.api.routes.course_workspace import _shared_student_predicate

        coaching = await _shared_student_predicate(course_id, user, session, coaching_only=True)
        allowed = set(
            (await session.execute(sa.select(User.id).where(User.id.in_(names), coaching))).scalars()
        )
        Model = CoachingAssignment if body.kind == "drill" else MockInterviewAssignment
        for sid, name in names.items():
            if sid not in allowed:
                skipped.append({"id": sid, "name": name, "reason": "Not sharing practice for coaching in this course."})
                continue
            key = str(uuid5(body.request_key, str(sid)))
            if (
                await session.execute(
                    sa.select(Model.id).where(Model.assigned_by == user.id, Model.request_key == key)
                )
            ).scalar_one_or_none():
                done.append({"id": sid, "name": name})
                continue
            fields = dict(
                student_id=sid, assigned_by=user.id, course_id=course_id, reason=body.reason,
                note=body.note or None, due_date=body.due_date, request_key=key,
                request_hash=_fingerprint(course_id, sid, body), status="assigned", version=1,
            )
            item = Model(
                **fields,
                **({"module": body.module} if body.kind == "drill" else {"job_profile_id": body.job_profile_id}),
            )
            session.add(item)
            await session.flush()
            session.add(
                PracticeDashboardAuditEvent(
                    actor_id=user.id,
                    action="coaching_assignment_created" if body.kind == "drill" else "mock_assignment_created",
                    purpose="facilitator_coaching",
                    target_type="coaching_assignment" if body.kind == "drill" else "mock_assignment",
                    target_id=str(item.id),
                    reason=body.reason,
                    event_metadata={"course_id": course_id, "student_id": sid, "cohort_id": row.id},
                )
            )
            done.append({"id": sid, "name": name})
    else:
        if body.opportunity_id is None:
            raise fastapi.HTTPException(422, "Choose an opening.")
        from src.api.routes.opportunities import _audit as opening_audit, _load as load_opening, _require_open
        from sqlalchemy.dialects.postgresql import insert

        opening, eligible = await load_opening(session, user, body.opportunity_id, "update", lock=True)
        _require_open(opening)
        if opening.course_id != course_id:
            raise fastapi.HTTPException(422, "Choose an opening in this course.")
        allowed = set(
            (await session.execute(sa.select(User.id).where(User.id.in_(names), eligible))).scalars()
        )
        already = set(
            (
                await session.execute(
                    sa.select(OpportunityCandidate.student_id).where(
                        OpportunityCandidate.opportunity_id == opening.id,
                        OpportunityCandidate.student_id.in_(names),
                    )
                )
            ).scalars()
        )
        new = sorted(allowed - already)
        if new:
            await session.execute(
                insert(OpportunityCandidate)
                .values([{"opportunity_id": opening.id, "student_id": s, "added_by": user.id} for s in new])
                .on_conflict_do_nothing(constraint="uq_opportunity_candidate")
            )
            opening_audit(session, user, opening, "opportunity_candidates_added",
                          {"student_ids": new, "cohort_id": row.id})
        for sid, name in names.items():
            if sid in allowed:
                done.append({"id": sid, "name": name})
            else:
                skipped.append({"id": sid, "name": name, "reason": "Outside your placement responsibility for this course."})
    audit(session, user, row, f"cohort_assigned_{body.kind}", body.reason,
          {"done": [d["id"] for d in done], "skipped": [s["id"] for s in skipped]})
    await session.commit()
    return {
        "done": done,
        "skipped": skipped,
        "members_outside_your_scope": total - len(members),
    }
