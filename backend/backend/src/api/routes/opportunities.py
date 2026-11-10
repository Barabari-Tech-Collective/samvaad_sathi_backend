"""Opportunities: an opening with candidates, a round plan and linked work (pack F25, slice 2).

Every route checks the shortlist access rule on the opportunity's course. Candidate decisions,
locking and release stay on the shortlist; this record organises who is considered, against
which rules, through which rounds.
"""

import datetime
import hashlib
import json
from typing import Literal
from uuid import UUID

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.dialects.postgresql import insert

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.session import get_async_session
from src.models.db.assessment_assignment import AssessmentAssignment
from src.models.db.bulk_ats import BulkATSBatch
from src.models.db.human_interview import HumanInterview
from src.models.db.job_profile import JobProfile
from src.models.db.opportunity import Opportunity, OpportunityCandidate, OpportunityRound
from src.models.db.practice_governance import PracticeDashboardAuditEvent, PracticeShortlist
from src.models.db.user import User
from src.services import assessment_assignments as assignments
from src.services import course_shortlists as shortlists
from src.services import opportunities as service

router = fastapi.APIRouter(prefix="/v2/opportunities", tags=["opportunities"])
UTC = datetime.timezone.utc


def _trim(value):
    if isinstance(value, str):
        value = value.strip()
    return value or None


class Create(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    title: str = Field(min_length=3, max_length=160)
    organisation: str = Field(min_length=2, max_length=160)
    job_profile_id: int | None = Field(None, ge=1)
    requirements: str | None = Field(None, max_length=4000)
    max_evidence_age_days: int = Field(90, ge=1, le=365)
    min_assessments: int = Field(1, ge=0, le=20)

    _trim = field_validator("title", "organisation", "requirements", mode="before")(
        classmethod(lambda cls, v: _trim(v))
    )


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=3, max_length=160)
    organisation: str | None = Field(None, min_length=2, max_length=160)
    job_profile_id: int | None = Field(None, ge=1)
    requirements: str | None = Field(None, max_length=4000)
    max_evidence_age_days: int | None = Field(None, ge=1, le=365)
    min_assessments: int | None = Field(None, ge=0, le=20)
    status: Literal["open", "closed", "archived"] | None = None


class Candidates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_ids: list[int] = Field(min_length=1, max_length=100)


class Round(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ai", "human"]
    name: str = Field(min_length=2, max_length=160)
    job_profile_id: int | None = Field(None, ge=1)


async def _course_names(session, ids):
    from src.models.db.student_placement import StudentPlacement

    if not ids:
        return {}
    rows = await session.execute(
        sa.select(StudentPlacement.course_id, sa.func.max(StudentPlacement.course_name))
        .where(StudentPlacement.course_id.in_(ids))
        .group_by(StudentPlacement.course_id)
    )
    return {k: v for k, v in rows.all() if v}


def _audit(session, user, opportunity, action, extra=None):
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id,
            action=action,
            purpose="opportunity_readiness",
            target_type="opportunity",
            target_id=str(opportunity.id),
            reason=action.replace("_", " "),
            event_metadata={"course_id": opportunity.course_id, **(extra or {})},
        )
    )


def _summary(row, **extra):
    return {
        "id": row.id,
        "course_id": row.course_id,
        "title": row.title,
        "organisation": row.organisation,
        "job_profile_id": row.job_profile_id,
        "requirements": row.requirements,
        "max_evidence_age_days": row.max_evidence_age_days,
        "min_assessments": row.min_assessments,
        "eligibility_version": row.eligibility_version,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        **extra,
    }


async def _assessment_template(session, job_profile_id):
    """An AI round or job role must be a published assessment template."""
    row = await session.get(JobProfile, job_profile_id)
    if row is None or row.interview_mode != "assessment" or row.status != "published":
        raise fastapi.HTTPException(422, "Choose a published assessment template.")
    return row


async def _load(session, user, opportunity_id, operation, lock=False):
    stmt = sa.select(Opportunity).where(Opportunity.id == opportunity_id)
    if lock:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Opportunity not found.")
    predicate = await service.authorize(user, row.course_id, session, operation)
    return row, predicate


def _require_open(row):
    if row.status != "open":
        raise fastapi.HTTPException(409, "Only an open opportunity can be changed this way.")


@router.get("")
async def list_opportunities(
    response: fastapi.Response,
    status: Literal["open", "closed", "archived"] = "open",
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if not shortlists.permission(user, "read"):
        raise fastapi.HTTPException(403, "An administrator must enable opportunities for your account.")
    response.headers["Cache-Control"] = "no-store"
    courses = await shortlists.placement_course_ids(user, session)
    if courses is not None and not courses:
        return {"items": [], "can_create": shortlists.permission(user, "create")}
    candidates = (
        sa.select(OpportunityCandidate.opportunity_id, sa.func.count().label("n"))
        .group_by(OpportunityCandidate.opportunity_id)
        .subquery()
    )
    rounds = (
        sa.select(OpportunityRound.opportunity_id, sa.func.count().label("n"))
        .group_by(OpportunityRound.opportunity_id)
        .subquery()
    )
    stmt = (
        sa.select(Opportunity, candidates.c.n, rounds.c.n)
        .outerjoin(candidates, candidates.c.opportunity_id == Opportunity.id)
        .outerjoin(rounds, rounds.c.opportunity_id == Opportunity.id)
        .where(Opportunity.status == status)
        .order_by(Opportunity.created_at.desc(), Opportunity.id.desc())
        .limit(200)
    )
    if courses is not None:
        stmt = stmt.where(Opportunity.course_id.in_(sorted(courses)))
    rows = (await session.execute(stmt)).all()
    return {
        "items": [_summary(r, candidates=int(c or 0), rounds=int(n or 0)) for r, c, n in rows],
        "can_create": shortlists.permission(user, "create"),
    }


@router.get("/board")
async def board(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Every opening this person may read, with who is at which stage and what to do next."""
    if not shortlists.permission(user, "read"):
        # Not an error here: Program staff use the other two tabs of this page.
        return {"items": [], "can": {"create": False, "update": False, "publish": False}, "allowed": False}
    response.headers["Cache-Control"] = "no-store"
    courses = await shortlists.placement_course_ids(user, session)
    can = {"create": shortlists.permission(user, "create"), "update": shortlists.permission(user, "update"), "publish": shortlists.permission(user, "publish")}
    if courses is not None and not courses:
        return {"items": [], "can": can}
    query = sa.select(Opportunity).order_by(Opportunity.created_at.desc(), Opportunity.id.desc()).limit(300)
    if courses is not None:
        query = query.where(Opportunity.course_id.in_(sorted(courses)))
    from src.services import opportunity_selection as selection
    from src.models.db.opportunity_handoff import OpportunityHandoff

    openings = list((await session.execute(query)).scalars())
    rounds = dict(
        (await session.execute(
            sa.select(OpportunityRound.opportunity_id, sa.func.count()).group_by(OpportunityRound.opportunity_id)
        )).all()
    )
    sent = set(
        (await session.execute(sa.select(OpportunityHandoff.opportunity_id).where(OpportunityHandoff.status == "shared"))).scalars()
    )
    predicates = {}
    for course_id in {o.course_id for o in openings}:
        predicates[course_id] = await service.authorize(user, course_id, session, "read")
    counts = await selection.board_counts(session, openings, predicates)
    items = [
        _summary(
            opening,
            people=counts[opening.id]["people"],
            rounds=int(rounds.get(opening.id, 0)),
            need=counts[opening.id]["need"],
            todo=counts[opening.id]["todo"],
            included=counts[opening.id]["included"],
            excluded=counts[opening.id]["excluded"],
            selection=counts[opening.id]["selection"],
            sent=opening.id in sent,
        )
        for opening in openings
    ]
    names = await _course_names(session, {o.course_id for o in openings})
    return {"items": [i | {"course_name": names.get(i["course_id"], i["course_id"])} for i in items], "can": can}


@router.post("/courses/{course_id}", status_code=201)
async def create_opportunity(
    course_id: str,
    body: Create,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    await service.authorize(user, course_id, session, "create")
    if body.job_profile_id is not None:
        await _assessment_template(session, body.job_profile_id)
    fingerprint = hashlib.sha256(
        json.dumps(
            {"course_id": course_id, **body.model_dump(mode="json", exclude={"request_key"})},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    request = sa.select(Opportunity).where(
        Opportunity.created_by == user.id, Opportunity.request_key == str(body.request_key)
    )
    existing = (await session.execute(request)).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise fastapi.HTTPException(409, "This request was already used for a different opportunity.")
        response.status_code = 200
        return _summary(existing)
    row_id = (
        await session.execute(
            insert(Opportunity)
            .values(
                **body.model_dump(exclude={"request_key"}),
                course_id=course_id,
                created_by=user.id,
                request_key=str(body.request_key),
                request_hash=fingerprint,
            )
            .on_conflict_do_nothing(constraint="uq_opportunity_request")
            .returning(Opportunity.id)
        )
    ).scalar_one_or_none()
    row = (await session.execute(request)).scalar_one() if row_id is None else await session.get(Opportunity, row_id)
    _audit(session, user, row, "opportunity_created")
    await session.commit()
    return _summary(row)


@router.get("/{opportunity_id}")
async def opportunity_detail(
    opportunity_id: int,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    row, predicate = await _load(session, user, opportunity_id, "read")
    response.headers["Cache-Control"] = "no-store"
    now = datetime.datetime.now(UTC)
    # Only candidates who are still within this person's scope are shown.
    people = (
        await session.execute(
            sa.select(
                User.id, User.name, User.university, OpportunityCandidate.added_at,
                service.shares_mock_data(now).label("shares"),
            )
            .join(OpportunityCandidate, OpportunityCandidate.student_id == User.id)
            .where(OpportunityCandidate.opportunity_id == row.id, predicate)
            .order_by(User.name)
        )
    ).all()
    evidence = await service.evidence(session, row, [p.id for p in people], now)
    rounds = (
        await session.execute(
            sa.select(OpportunityRound, JobProfile.job_name)
            .outerjoin(JobProfile, JobProfile.id == OpportunityRound.job_profile_id)
            .where(OpportunityRound.opportunity_id == row.id)
            .order_by(OpportunityRound.position)
        )
    ).all()
    assigned = {
        rid: (int(total), int(done))
        for rid, total, done in (
            await session.execute(
                sa.select(
                    AssessmentAssignment.opportunity_round_id,
                    sa.func.count(AssessmentAssignment.id),
                    sa.func.count(AssessmentAssignment.id).filter(AssessmentAssignment.status == "completed"),
                )
                .where(
                    AssessmentAssignment.opportunity_round_id.in_([r.id for r, _ in rounds] or [-1]),
                    AssessmentAssignment.status != "cancelled",
                )
                .group_by(AssessmentAssignment.opportunity_round_id)
            )
        ).all()
    }
    waiting: dict[int, list[str]] = {}
    round_names = {r.id: r.name for r, _ in rounds}
    for student_id, round_id in (
        await session.execute(
            sa.select(AssessmentAssignment.student_id, AssessmentAssignment.opportunity_round_id).where(
                AssessmentAssignment.opportunity_round_id.in_(list(round_names) or [-1]),
                AssessmentAssignment.status.not_in(["cancelled", "completed"]),
            )
        )
    ).all():
        waiting.setdefault(student_id, []).append(round_names[round_id])
    linked_shortlists = (
        await session.execute(
            sa.select(PracticeShortlist).where(PracticeShortlist.opportunity_id == row.id)
        )
    ).scalars().all()
    batches = (
        await session.execute(
            sa.select(BulkATSBatch.id, BulkATSBatch.name).where(BulkATSBatch.opportunity_id == row.id)
        )
    ).all()
    role = await session.get(JobProfile, row.job_profile_id) if row.job_profile_id else None
    return _summary(
        row,
        job_role=role.job_name if role else None,
        candidates=[
            {
                "student_id": p.id,
                "name": p.name,
                "college": p.university,
                "added_at": p.added_at.isoformat(),
                "shares_mock_data": bool(p.shares),
                "waiting_on": waiting.get(p.id, []),
                **evidence[p.id],
            }
            for p in people
        ],
        rounds=[
            {"id": r.id, "position": r.position, "kind": r.kind, "name": r.name,
             "job_profile_id": r.job_profile_id, "template": name,
             "assigned": assigned.get(r.id, (0, 0))[0], "completed": assigned.get(r.id, (0, 0))[1]}
            for r, name in rounds
        ],
        shortlists=[shortlists.metadata(s) for s in linked_shortlists],
        screening=[{"id": b.id, "name": b.name} for b in batches],
        can_update=shortlists.permission(user, "update"),
        can_manage=shortlists.permission(user, "publish"),
    )


@router.patch("/{opportunity_id}")
async def change_opportunity(
    opportunity_id: int,
    body: Change,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    row, _ = await _load(session, user, opportunity_id, "update", lock=True)
    changes = body.model_dump(exclude_unset=True)
    if "job_profile_id" in changes and changes["job_profile_id"] is not None:
        await _assessment_template(session, changes["job_profile_id"])
    rules = {"max_evidence_age_days", "min_assessments"}
    if any(k in rules and changes[k] is not None and changes[k] != getattr(row, k) for k in changes):
        # Decisions name the rules they used, so changed rules are a new version.
        row.eligibility_version += 1
    for key, value in changes.items():
        if value is not None or key in {"job_profile_id", "requirements"}:
            setattr(row, key, value)
    _audit(session, user, row, "opportunity_changed", {"fields": sorted(changes)})
    await session.commit()
    await session.refresh(row)
    return _summary(row)


@router.get("/{opportunity_id}/eligible-students")
async def eligible_students(
    opportunity_id: int,
    q: str = fastapi.Query("", max_length=80),
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    row, predicate = await _load(session, user, opportunity_id, "update")
    already = sa.select(OpportunityCandidate.student_id).where(
        OpportunityCandidate.opportunity_id == row.id
    )
    stmt = sa.select(User.id, User.name, User.university).where(predicate, User.id.not_in(already))
    if q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(sa.or_(User.name.ilike(pattern), User.email.ilike(pattern)))
    rows = (await session.execute(stmt.order_by(User.name).limit(200 if not q.strip() else 25))).all()
    from src.services import learner_hub as hub

    context = await hub.load_context(user, session, 30, row.course_id)
    status = {p.id: p.status for p in context.learners}
    return {"items": [{"student_id": r.id, "name": r.name, "college": r.university, "status": status.get(r.id)} for r in rows]}


@router.post("/{opportunity_id}/candidates")
async def add_candidates(
    opportunity_id: int,
    body: Candidates,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    row, predicate = await _load(session, user, opportunity_id, "update", lock=True)
    _require_open(row)
    wanted = sorted(set(body.student_ids))
    allowed = set(
        (await session.execute(sa.select(User.id).where(User.id.in_(wanted), predicate))).scalars()
    )
    if allowed:
        await session.execute(
            insert(OpportunityCandidate)
            .values([{"opportunity_id": row.id, "student_id": sid, "added_by": user.id} for sid in sorted(allowed)])
            .on_conflict_do_nothing(constraint="uq_opportunity_candidate")
        )
        _audit(session, user, row, "opportunity_candidates_added", {"student_ids": sorted(allowed)})
    await session.commit()
    return {"added": sorted(allowed), "not_eligible": [s for s in wanted if s not in allowed]}


@router.delete("/{opportunity_id}/candidates/{student_id}", status_code=204)
async def remove_candidate(
    opportunity_id: int,
    student_id: int,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    row, _ = await _load(session, user, opportunity_id, "update", lock=True)
    _require_open(row)
    await session.execute(
        sa.delete(OpportunityCandidate).where(
            OpportunityCandidate.opportunity_id == row.id,
            OpportunityCandidate.student_id == student_id,
        )
    )
    _audit(session, user, row, "opportunity_candidate_removed", {"student_id": student_id})
    await session.commit()
    return fastapi.Response(status_code=204)


@router.post("/{opportunity_id}/rounds", status_code=201)
async def add_round(
    opportunity_id: int,
    body: Round,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    row, _ = await _load(session, user, opportunity_id, "update", lock=True)
    _require_open(row)
    if body.kind == "ai":
        if body.job_profile_id is None:
            raise fastapi.HTTPException(422, "An AI round needs a published assessment template.")
        await _assessment_template(session, body.job_profile_id)
    position = (
        await session.execute(
            sa.select(sa.func.coalesce(sa.func.max(OpportunityRound.position), 0)).where(
                OpportunityRound.opportunity_id == row.id
            )
        )
    ).scalar_one() + 1
    plan = OpportunityRound(
        opportunity_id=row.id,
        position=position,
        kind=body.kind,
        name=body.name.strip(),
        job_profile_id=body.job_profile_id if body.kind == "ai" else None,
        created_by=user.id,
    )
    session.add(plan)
    _audit(session, user, row, "opportunity_round_added", {"kind": body.kind})
    await session.commit()
    return {"id": plan.id, "position": position, "kind": plan.kind, "name": plan.name,
            "job_profile_id": plan.job_profile_id}


@router.delete("/{opportunity_id}/rounds/{round_id}", status_code=204)
async def remove_round(
    opportunity_id: int,
    round_id: int,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    row, _ = await _load(session, user, opportunity_id, "update", lock=True)
    _require_open(row)
    await session.execute(
        sa.delete(OpportunityRound).where(
            OpportunityRound.id == round_id, OpportunityRound.opportunity_id == row.id
        )
    )
    _audit(session, user, row, "opportunity_round_removed", {"round_id": round_id})
    await session.commit()
    return fastapi.Response(status_code=204)


@router.put("/{opportunity_id}/screening/{batch_id}")
async def link_screening(
    opportunity_id: int,
    batch_id: int,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Attach a resume-screening run. Needs both: changing this opportunity, and owning
    the run: its creator, an admin, or a lead who decides this opening."""
    row, _ = await _load(session, user, opportunity_id, "update", lock=True)
    batch = await session.get(BulkATSBatch, batch_id)
    if batch is None or not (is_admin_user(user) or batch.created_by == user.id or shortlists.permission(user, "publish")):
        raise fastapi.HTTPException(404, "Screening run not found.")
    if batch.opportunity_id not in (None, row.id):
        # Moving a run silently would take it away from another opening's reviewers.
        raise fastapi.HTTPException(
            409, "This screening run belongs to another opening. Unlink it there first."
        )
    batch.opportunity_id = row.id
    _audit(session, user, row, "opportunity_screening_linked", {"batch_id": batch_id})
    await session.commit()
    return {"id": batch.id, "name": batch.name}


@router.delete("/{opportunity_id}/screening/{batch_id}")
async def unlink_screening(
    opportunity_id: int,
    batch_id: int,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Detach a screening run from this opening. The run and its results are kept."""
    row, _ = await _load(session, user, opportunity_id, "update", lock=True)
    batch = await session.get(BulkATSBatch, batch_id)
    if (
        batch is None
        or batch.opportunity_id != row.id
        or not (is_admin_user(user) or batch.created_by == user.id or shortlists.permission(user, "publish"))
    ):
        raise fastapi.HTTPException(404, "Screening run not found on this opening.")
    batch.opportunity_id = None
    _audit(session, user, row, "opportunity_screening_unlinked", {"batch_id": batch_id})
    await session.commit()
    return {"id": batch.id}


class AssignRound(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    difficulty: Literal["easy", "medium", "hard", "expert"] = "medium"
    opens_at: datetime.datetime
    closes_at: datetime.datetime | None = None
    instructions: str | None = Field(default=None, max_length=2000)
    # Only these people, when given; otherwise everyone in the opening.
    student_ids: list[int] | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("opens_at", "closes_at")
    @classmethod
    def zoned(cls, v):
        if v is not None and v.tzinfo is None:
            raise ValueError("Give times with a timezone.")
        return v


@router.post("/{opportunity_id}/rounds/{round_id}/assign")
async def assign_round(
    opportunity_id: int,
    round_id: int,
    body: AssignRound,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Give every current candidate this AI round as a timed assignment."""
    from src.services.interview_eligibility import unavailable_reason

    row, predicate = await _load(session, user, opportunity_id, "update", lock=True)
    _require_open(row)
    plan = (
        await session.execute(
            sa.select(OpportunityRound).where(
                OpportunityRound.id == round_id, OpportunityRound.opportunity_id == row.id
            )
        )
    ).scalar_one_or_none()
    if plan is None or plan.kind != "ai":
        raise fastapi.HTTPException(404, "AI round not found.")
    template = await _assessment_template(session, plan.job_profile_id) if plan.job_profile_id else None
    if template is None:
        raise fastapi.HTTPException(422, "This round has no assessment template.")
    reason = unavailable_reason(template, body.difficulty)
    if reason:
        raise fastapi.HTTPException(422, reason)
    if body.closes_at is not None and body.closes_at <= body.opens_at:
        raise fastapi.HTTPException(422, "The window must close after it opens.")
    if body.closes_at is not None and body.closes_at <= datetime.datetime.now(UTC):
        raise fastapi.HTTPException(422, "The window must end in the future.")
    students = (
        await session.execute(
            sa.select(User.id)
            .join(OpportunityCandidate, OpportunityCandidate.student_id == User.id)
            .where(
                OpportunityCandidate.opportunity_id == row.id,
                predicate,
                *([User.id.in_(body.student_ids)] if body.student_ids else []),
            )
        )
    ).scalars().all()
    if not students:
        raise fastapi.HTTPException(422, "There are no candidates in your scope to assign this to.")
    made, replayed = await assignments.create_for(
        session, user, course_id=row.course_id, template=template, difficulty=body.difficulty,
        student_ids=list(students), opens_at=body.opens_at, closes_at=body.closes_at,
        instructions=body.instructions, request_key=body.request_key, round_id=plan.id,
    )
    _audit(session, user, row, "opportunity_round_assigned", {"round_id": plan.id, "created": len(made)})
    await session.commit()
    return {"created": len(made), "already_assigned": replayed}
