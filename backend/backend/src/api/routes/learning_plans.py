"""Learning plans for staff and learners (architecture pack sec 4).

Staff (practice coaching permission: read to view, create to author and assign, update to
edit, publish, revise, archive and cancel) build plans and assign a published version to a
learner in a course under the usual coaching rules (course scope plus the learner's coaching
consent). Learners see their plans under /me, may self-start a plan marked self_start in a
course they are enrolled in, and write reflection steps. Drill and mock steps complete from
evidence (see services.learning_plans).
"""

import datetime as dt
import hashlib
import json
from typing import Literal
from uuid import UUID

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator, model_validator

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.job_profile import JobProfile
from src.models.db.learning_plan import (
    DRILLS,
    LearningPlan,
    LearningPlanAssignment,
    LearningPlanStep,
    LearningPlanStepProgress,
)
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.practice_drill_completion import PracticeDrillCompletion
from src.models.db.pronunciation_practice import PronunciationPractice
from src.models.db.structure_practice import StructurePractice, StructurePracticeAnswer
from src.models.db.pacing_practice import PacingPracticeSession
from src.services import learning_plans as service
from src.services import notifications
from src.services.coaching_assignments import authorize_staff, lock_student, require_owned_enrollment
from src.services.feature_access import permits

router = fastapi.APIRouter(prefix="/v2/learning-plans", tags=["learning-plans"])

UTC = dt.timezone.utc


def _trim(value):
    return value.strip() if isinstance(value, str) else value


class PlanFields(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    goal: str = Field(min_length=10, max_length=1000)
    description: str | None = Field(None, max_length=4000)
    self_start: bool = False

    _trim = field_validator("title", "goal", "description", mode="before")(classmethod(lambda c, v: _trim(v)))


class CreatePlan(PlanFields):
    request_key: UUID


class EditPlan(PlanFields):
    expected_row_version: int = Field(ge=1)


class Step(BaseModel):
    kind: Literal["drill", "mock", "reflection"]
    title: str = Field(min_length=3, max_length=160)
    instructions: str | None = Field(None, max_length=2000)
    module: Literal[DRILLS] | None = None  # type: ignore[valid-type]
    job_profile_id: int | None = Field(None, ge=1)

    _trim = field_validator("title", "instructions", mode="before")(classmethod(lambda c, v: _trim(v)))

    @model_validator(mode="after")
    def target(self):
        if (self.kind == "drill") != (self.module is not None):
            raise ValueError("A drill step needs a drill, and only a drill step has one.")
        if (self.kind == "mock") != (self.job_profile_id is not None):
            raise ValueError("A mock step needs a mock template, and only a mock step has one.")
        return self


class DrillReceipt(BaseModel):
    completion_id: int = Field(ge=1)


class DrillSession(BaseModel):
    session_id: int = Field(ge=1)


class Steps(BaseModel):
    expected_row_version: int = Field(ge=1)
    steps: list[Step] = Field(min_length=1, max_length=20)


class Versioned(BaseModel):
    expected_row_version: int = Field(ge=1)


class Reasoned(BaseModel):
    reason: str = Field(min_length=10, max_length=1000)

    _trim = field_validator("reason", mode="before")(classmethod(lambda c, v: _trim(v)))


class Assign(Reasoned):
    request_key: UUID
    plan_id: int = Field(ge=1)
    due_date: dt.date | None = None


class Cancel(Reasoned):
    expected_version: int = Field(ge=1)


class Start(BaseModel):
    request_key: UUID
    plan_id: int = Field(ge=1)
    course_id: str = Field(min_length=1, max_length=128)


class Reflection(BaseModel):
    text: str = Field(min_length=20, max_length=4000)

    _trim = field_validator("text", mode="before")(classmethod(lambda c, v: _trim(v)))


def can(user, operation):
    return is_admin_user(user) or (
        isinstance(user.feature_permissions, dict)
        and permits(user.feature_permissions, "practice_coaching", operation)
    )


def require(user, operation):
    if not can(user, operation):
        raise fastapi.HTTPException(403, "Learning plans need the practice coaching permission.")


def audit(session, user_id, target_type, target_id, action, reason, details=None):
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user_id,
            action=action,
            purpose="facilitator_coaching",
            target_type=target_type,
            target_id=str(target_id),
            reason=reason,
            event_metadata=details or {},
        )
    )


async def load_plan(session, plan_id, lock=False):
    stmt = sa.select(LearningPlan).where(LearningPlan.id == plan_id)
    if lock:
        stmt = stmt.with_for_update()
    plan = (await session.execute(stmt)).scalar_one_or_none()
    if plan is None:
        raise fastapi.HTTPException(404, "Learning plan not found.")
    return plan


def editable(plan, expected):
    if plan.status != "draft":
        raise fastapi.HTTPException(409, "Only a draft can be edited. Start a new version instead.")
    if plan.row_version != expected:
        raise fastapi.HTTPException(409, "This plan changed. Refresh before saving.")


async def plan_detail(session, plan):
    steps = (await service.steps_for(session, [plan.id]))[plan.id]
    return service.plan_view(plan, steps)


# ---------------------------------------------------------------- staff: plans


@router.get("")
async def list_plans(
    response: fastapi.Response,
    status: Literal["draft", "published", "archived"] | None = None,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    require(user, "read")
    response.headers["Cache-Control"] = "no-store"
    stmt = sa.select(LearningPlan)
    if status:
        stmt = stmt.where(LearningPlan.status == status)
    else:
        stmt = stmt.where(LearningPlan.status != "archived")
    plans = (await session.execute(stmt.order_by(LearningPlan.title, LearningPlan.version.desc()))).scalars().all()
    steps = await service.steps_for(session, [p.id for p in plans])
    return {
        "items": [service.plan_view(p, steps[p.id]) for p in plans],
        "can_create": can(user, "create"),
        "can_edit": can(user, "update"),
    }


@router.post("", status_code=201)
async def create_plan(
    body: CreatePlan,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    require(user, "create")
    existing = (
        await session.execute(
            sa.select(LearningPlan).where(
                LearningPlan.created_by == user.id, LearningPlan.request_key == str(body.request_key)
            )
        )
    ).scalar_one_or_none()
    if existing:
        return await plan_detail(session, existing)
    plan = LearningPlan(
        request_key=str(body.request_key),
        created_by=user.id,
        **body.model_dump(exclude={"request_key"}),
    )
    session.add(plan)
    await session.flush()
    audit(session, user.id, "learning_plan", plan.id, "learning_plan_created", f"Created plan {plan.title}.")
    await session.commit()
    await session.refresh(plan)
    return await plan_detail(session, plan)


@router.get("/{plan_id}")
async def get_plan(
    plan_id: int,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    require(user, "read")
    return await plan_detail(session, await load_plan(session, plan_id))


@router.patch("/{plan_id}")
async def edit_plan(
    plan_id: int,
    body: EditPlan,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    require(user, "update")
    plan = await load_plan(session, plan_id, lock=True)
    editable(plan, body.expected_row_version)
    for key, value in body.model_dump(exclude={"expected_row_version"}).items():
        setattr(plan, key, value)
    plan.row_version += 1
    await session.commit()
    await session.refresh(plan)
    return await plan_detail(session, plan)


@router.put("/{plan_id}/steps")
async def set_steps(
    plan_id: int,
    body: Steps,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    require(user, "update")
    plan = await load_plan(session, plan_id, lock=True)
    editable(plan, body.expected_row_version)
    mocks = {s.job_profile_id for s in body.steps if s.job_profile_id}
    if mocks:
        ok = set(
            (
                await session.execute(
                    sa.select(JobProfile.id).where(
                        JobProfile.id.in_(mocks),
                        JobProfile.interview_mode == "practice",
                        JobProfile.status == "published",
                    )
                )
            ).scalars()
        )
        if mocks - ok:
            raise fastapi.HTTPException(422, "Mock steps must use published mock interview templates.")
    await session.execute(sa.delete(LearningPlanStep).where(LearningPlanStep.plan_id == plan.id))
    for position, step in enumerate(body.steps, start=1):
        session.add(LearningPlanStep(plan_id=plan.id, position=position, **step.model_dump()))
    plan.row_version += 1
    await session.commit()
    await session.refresh(plan)
    return await plan_detail(session, plan)


@router.post("/{plan_id}/publish")
async def publish_plan(
    plan_id: int,
    body: Versioned,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    require(user, "update")
    plan = await load_plan(session, plan_id, lock=True)
    editable(plan, body.expected_row_version)
    if not (await service.steps_for(session, [plan.id]))[plan.id]:
        raise fastapi.HTTPException(409, "Add at least one step before publishing.")
    plan.status, plan.published_at = "published", dt.datetime.now(UTC)
    plan.row_version += 1
    # A newer version replaces the one it revised for new assignments; started ones keep it.
    if plan.parent_id:
        parent = await load_plan(session, plan.parent_id, lock=True)
        parent.status = "archived"
        parent.row_version += 1
    audit(session, user.id, "learning_plan", plan.id, "learning_plan_published", f"Published version {plan.version}.")
    await session.commit()
    await session.refresh(plan)
    return await plan_detail(session, plan)


@router.post("/{plan_id}/revise", status_code=201)
async def revise_plan(
    plan_id: int,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Start the next version of a published plan as a draft copy, or return the open one."""
    require(user, "update")
    plan = await load_plan(session, plan_id, lock=True)
    if plan.status != "published":
        raise fastapi.HTTPException(409, "Only a published plan can be revised.")
    draft = (
        await session.execute(
            sa.select(LearningPlan).where(LearningPlan.parent_id == plan.id, LearningPlan.status == "draft")
        )
    ).scalar_one_or_none()
    if draft:
        return await plan_detail(session, draft)
    draft = LearningPlan(
        title=plan.title, goal=plan.goal, description=plan.description, self_start=plan.self_start,
        version=plan.version + 1, parent_id=plan.id, request_key=f"revise-{plan.id}", created_by=user.id,
    )
    session.add(draft)
    await session.flush()
    for step, _ in (await service.steps_for(session, [plan.id]))[plan.id]:
        session.add(
            LearningPlanStep(
                plan_id=draft.id, position=step.position, kind=step.kind, module=step.module,
                job_profile_id=step.job_profile_id, title=step.title, instructions=step.instructions,
            )
        )
    await session.commit()
    await session.refresh(draft)
    return await plan_detail(session, draft)


@router.post("/{plan_id}/archive")
async def archive_plan(
    plan_id: int,
    body: Reasoned,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Stop new assignments. Learners already on it keep going."""
    require(user, "update")
    plan = await load_plan(session, plan_id, lock=True)
    plan.status = "archived"
    plan.row_version += 1
    audit(session, user.id, "learning_plan", plan.id, "learning_plan_archived", body.reason)
    await session.commit()
    return {"id": plan.id, "status": plan.status}


# ------------------------------------------------------ staff: learner progress


@router.get("/courses/{course_id}/students/{student_id}")
async def staff_assignments(
    course_id: str,
    student_id: int,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    require(user, "read")
    await authorize_staff(session, user, course_id, student_id, "read")
    rows = await service.assignments_where(
        session,
        sa.and_(
            LearningPlanAssignment.course_id == course_id,
            LearningPlanAssignment.student_id == student_id,
        ),
    )
    return {
        "items": await service.assignment_views(session, rows),
        "can_assign": can(user, "create"),
        "can_cancel": can(user, "update"),
    }


async def create_assignment(session, user, course_id, student_id, body, *, request_key=None):
    """Shared by the learner page and cohort assignment. Caller has authorised the learner."""
    plan = await load_plan(session, body.plan_id)
    if plan.status != "published":
        raise fastapi.HTTPException(422, "Choose a published learning plan.")
    if body.due_date and body.due_date < dt.datetime.now(UTC).date():
        raise fastapi.HTTPException(422, "Choose today or a future due date (UTC).")
    key = request_key or str(body.request_key)
    fingerprint = hashlib.sha256(
        json.dumps(
            {"course_id": course_id, "student_id": student_id, "plan_id": body.plan_id,
             "reason": body.reason, "due_date": str(body.due_date)},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    existing = (
        await session.execute(
            sa.select(LearningPlanAssignment).where(
                LearningPlanAssignment.student_id == student_id,
                LearningPlanAssignment.request_key == key,
            )
        )
    ).scalar_one_or_none()
    if existing:
        if existing.request_hash != fingerprint:
            raise fastapi.HTTPException(409, "This request key belongs to a different assignment.")
        return existing, False
    row = LearningPlanAssignment(
        plan_id=plan.id, student_id=student_id, course_id=course_id, source="assigned",
        assigned_by=user.id, reason=body.reason, due_date=body.due_date,
        request_key=key, request_hash=fingerprint,
    )
    session.add(row)
    await session.flush()
    audit(session, user.id, "learning_plan_assignment", row.id, "learning_plan_assigned", body.reason,
          {"course_id": course_id, "student_id": student_id, "plan_id": plan.id, "plan_version": plan.version})
    notifications.notify(
        session, student_id, "learning_plan_assigned", "A learning plan was given to you",
        plan.title, "/coaching", ("learning_plan_assignment", row.id),
    )
    return row, True


@router.post("/courses/{course_id}/students/{student_id}", status_code=201)
async def assign_plan(
    course_id: str,
    student_id: int,
    body: Assign,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    require(user, "create")
    await authorize_staff(session, user, course_id, student_id, "create")
    await lock_student(session, student_id)
    await authorize_staff(session, user, course_id, student_id, "create")
    row, created = await create_assignment(session, user, course_id, student_id, body)
    await session.commit()
    if not created:
        response.status_code = 200
    return (await service.assignment_views(session, [row]))[0]


@router.post("/courses/{course_id}/students/{student_id}/{assignment_id}/cancel")
async def cancel_plan_assignment(
    course_id: str,
    student_id: int,
    assignment_id: int,
    body: Cancel,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    require(user, "update")
    await authorize_staff(session, user, course_id, student_id, "update")
    row = (
        await session.execute(
            sa.select(LearningPlanAssignment)
            .where(
                LearningPlanAssignment.id == assignment_id,
                LearningPlanAssignment.student_id == student_id,
                LearningPlanAssignment.course_id == course_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Learning plan assignment not found.")
    if row.version != body.expected_version:
        raise fastapi.HTTPException(409, "This plan changed. Refresh before cancelling.")
    if row.status != "active":
        raise fastapi.HTTPException(409, "Only a plan in progress can be cancelled.")
    row.status, row.closed_at, row.version = "cancelled", dt.datetime.now(UTC), row.version + 1
    audit(session, user.id, "learning_plan_assignment", row.id, "learning_plan_cancelled", body.reason)
    await session.commit()
    return (await service.assignment_views(session, [row]))[0]


# ----------------------------------------------------------------- learners


@router.get("/me/plans")
async def my_plans(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    """The learner's own plans with every step and where they are."""
    response.headers["Cache-Control"] = "no-store"
    rows = await service.assignments_where(session, LearningPlanAssignment.student_id == user.id)
    return {"items": await service.assignment_views(session, rows)}


@router.get("/me/available")
async def available_plans(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    """Published plans this learner may start on their own."""
    response.headers["Cache-Control"] = "no-store"
    plans = (
        await session.execute(
            sa.select(LearningPlan)
            .where(LearningPlan.status == "published", LearningPlan.self_start.is_(True))
            .order_by(LearningPlan.title)
        )
    ).scalars().all()
    steps = await service.steps_for(session, [p.id for p in plans])
    return {"items": [service.plan_view(p, steps[p.id]) for p in plans]}


@router.post("/me/start", status_code=201)
async def start_plan(
    body: Start,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    plan = await load_plan(session, body.plan_id)
    if plan.status != "published" or not plan.self_start:
        raise fastapi.HTTPException(422, "This plan cannot be started on your own.")
    await require_owned_enrollment(session, user.id, body.course_id)
    existing = (
        await session.execute(
            sa.select(LearningPlanAssignment).where(
                LearningPlanAssignment.student_id == user.id,
                LearningPlanAssignment.request_key == str(body.request_key),
            )
        )
    ).scalar_one_or_none()
    if existing:
        response.status_code = 200
        return (await service.assignment_views(session, [existing]))[0]
    running = (
        await session.execute(
            sa.select(LearningPlanAssignment).where(
                LearningPlanAssignment.student_id == user.id,
                LearningPlanAssignment.plan_id == plan.id,
                LearningPlanAssignment.course_id == body.course_id,
                LearningPlanAssignment.status == "active",
            )
        )
    ).scalars().first()
    if running:
        response.status_code = 200
        return (await service.assignment_views(session, [running]))[0]
    row = LearningPlanAssignment(
        plan_id=plan.id, student_id=user.id, course_id=body.course_id, source="self",
        request_key=str(body.request_key), request_hash="self",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return (await service.assignment_views(session, [row]))[0]


@router.post("/me/{assignment_id}/steps/{step_id}/reflection")
async def write_reflection(
    assignment_id: int,
    step_id: int,
    body: Reflection,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    """Complete the current reflection step. Steps before it must be done first."""
    row = (
        await session.execute(
            sa.select(LearningPlanAssignment)
            .where(LearningPlanAssignment.id == assignment_id, LearningPlanAssignment.student_id == user.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Learning plan not found.")
    if row.status != "active":
        raise fastapi.HTTPException(409, "This plan is no longer in progress.")
    view = (await service.assignment_views(session, [row]))[0]
    current = next((s for s in view["steps"] if s["status"] == "current"), None)
    if current is None or current["id"] != step_id or current["kind"] != "reflection":
        raise fastapi.HTTPException(409, "This is not the step you are on.")
    session.add(
        LearningPlanStepProgress(
            assignment_id=row.id, step_id=step_id, evidence_kind="reflection",
            reflection=body.text, completed_at=dt.datetime.now(UTC),
        )
    )
    try:
        await session.commit()
    except sa.exc.IntegrityError:
        await session.rollback()
        raise fastapi.HTTPException(409, "This reflection was already submitted. Refresh the plan.")
    return (await service.assignment_views(session, [row]))[0]


@router.post("/me/{assignment_id}/steps/{step_id}/drill-completion")
async def bind_drill_completion(assignment_id: int, step_id: int, body: DrillReceipt, user=fastapi.Depends(get_current_user), session=fastapi.Depends(get_async_session)):
    """Bind one owned, verified drill receipt to exactly the current plan step."""
    row = (await session.execute(sa.select(LearningPlanAssignment).where(LearningPlanAssignment.id == assignment_id, LearningPlanAssignment.student_id == user.id).with_for_update())).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Learning plan not found.")
    await require_owned_enrollment(session, user.id, row.course_id)
    if row.status != "active":
        raise fastapi.HTTPException(409, "This plan is no longer in progress.")
    view = (await service.assignment_views(session, [row]))[0]
    current = next((s for s in view["steps"] if s["status"] == "current"), None)
    if current is None or current["id"] != step_id or current["kind"] != "drill":
        raise fastapi.HTTPException(409, "This is not the drill step you are on.")
    receipt = (
        await session.execute(
            sa.select(PracticeDrillCompletion)
            .where(PracticeDrillCompletion.id == body.completion_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if receipt is None or receipt.user_id != user.id or receipt.module != current["module"] or receipt.completed_at < row.created_at:
        raise fastapi.HTTPException(409, "This drill receipt cannot complete this plan step.")
    used = (await session.execute(sa.select(LearningPlanStepProgress.id).where(LearningPlanStepProgress.evidence_kind == "drill_completion", LearningPlanStepProgress.evidence_id == receipt.id))).scalar_one_or_none()
    if used is not None:
        raise fastapi.HTTPException(409, "This drill receipt was already used by a learning plan.")
    session.add(
        LearningPlanStepProgress(
            assignment_id=row.id,
            step_id=step_id,
            evidence_kind="drill_completion",
            evidence_id=receipt.id,
            completed_at=receipt.completed_at,
        )
    )
    try:
        await session.commit()
    except sa.exc.IntegrityError:
        await session.rollback()
        raise fastapi.HTTPException(409, "This drill receipt was already used by a learning plan.")
    return (await service.assignment_views(session, [row]))[0]


@router.post("/me/{assignment_id}/steps/{step_id}/drill-session")
async def bind_drill_session(
    assignment_id: int,
    step_id: int,
    body: DrillSession,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    """Bind one completed, server-owned specialist drill session to its exact plan step."""
    row = (
        await session.execute(
            sa.select(LearningPlanAssignment)
            .where(LearningPlanAssignment.id == assignment_id, LearningPlanAssignment.student_id == user.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Learning plan not found.")
    await require_owned_enrollment(session, user.id, row.course_id)
    if row.status != "active":
        raise fastapi.HTTPException(409, "This plan is no longer in progress.")
    view = (await service.assignment_views(session, [row]))[0]
    current = next((step for step in view["steps"] if step["status"] == "current"), None)
    if current is None or current["id"] != step_id or current["kind"] != "drill":
        raise fastapi.HTTPException(409, "This is not the drill step you are on.")
    module = current["module"]
    evidence_kind, completed_at = None, None
    if module == "pronunciation":
        record = (
            await session.execute(
                sa.select(PronunciationPractice)
                .where(
                    PronunciationPractice.id == body.session_id,
                    PronunciationPractice.user_id == user.id,
                    PronunciationPractice.status == "completed",
                    PronunciationPractice.created_at >= row.created_at,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if record:
            evidence_kind, completed_at = "pronunciation_session", record.created_at
    elif module == "structure":
        record = (
            await session.execute(
                sa.select(StructurePractice)
                .where(
                    StructurePractice.id == body.session_id,
                    StructurePractice.user_id == user.id,
                    StructurePractice.created_at >= row.created_at,
                    sa.exists(
                        sa.select(StructurePracticeAnswer.id).where(
                            StructurePracticeAnswer.practice_id == StructurePractice.id,
                            StructurePracticeAnswer.analyzed_at.is_not(None),
                        )
                    ),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if record:
            evidence_kind, completed_at = "structure_practice", record.updated_at
    elif module == "pace":
        record = (
            await session.execute(
                sa.select(PacingPracticeSession)
                .where(
                    PacingPracticeSession.id == body.session_id,
                    PacingPracticeSession.user_id == user.id,
                    PacingPracticeSession.status == "completed",
                    PacingPracticeSession.updated_at >= row.created_at,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if record:
            evidence_kind, completed_at = "pacing_session", record.updated_at
    if evidence_kind is None or completed_at is None:
        raise fastapi.HTTPException(409, "This completed session cannot finish this plan step.")
    used = (
        await session.execute(
            sa.select(LearningPlanStepProgress.id).where(
                LearningPlanStepProgress.evidence_kind == evidence_kind,
                LearningPlanStepProgress.evidence_id == body.session_id,
            )
        )
    ).scalar_one_or_none()
    if used is not None:
        raise fastapi.HTTPException(409, "This practice session was already used by a learning plan.")
    session.add(
        LearningPlanStepProgress(
            assignment_id=row.id, step_id=step_id, evidence_kind=evidence_kind,
            evidence_id=body.session_id, completed_at=completed_at,
        )
    )
    try:
        await session.commit()
    except sa.exc.IntegrityError:
        await session.rollback()
        raise fastapi.HTTPException(409, "This practice session was already used by a learning plan.")
    return (await service.assignment_views(session, [row]))[0]
