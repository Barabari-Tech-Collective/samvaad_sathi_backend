"""Assign an AI assessment to learners or a cohort (architecture pack F14).

Staff with the assessment_assignments permission pick a published assessment template, a
window and optional instructions. The template revision is pinned and the learner's confirmed
accommodation labels are copied at that moment. Starting is gated in interviews_v2."""

import datetime as dt
from uuid import UUID

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator, model_validator

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.assessment_assignment import AssessmentAssignment
from src.models.db.cohort import Cohort, CohortMember
from src.models.db.job_profile import JobProfile
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.user import User
from src.services import assessment_assignments as service
from src.services import notifications
from src.services.accommodations import confirmed_conditions
from src.services.interview_eligibility import unavailable_reason

router = fastapi.APIRouter(prefix="/v2/assessment-assignments", tags=["assessment-assignments"])
UTC = dt.timezone.utc
DIFFICULTIES = ("easy", "medium", "hard", "expert")


class Window(BaseModel):
    opens_at: dt.datetime
    closes_at: dt.datetime | None = None

    @model_validator(mode="after")
    def ordered(self):
        for name in ("opens_at", "closes_at"):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                raise ValueError("Give times with a timezone.")
        if self.closes_at is not None and self.closes_at <= self.opens_at:
            raise ValueError("The window must close after it opens.")
        return self


class Create(Window):
    request_key: UUID
    job_profile_id: int = Field(ge=1)
    difficulty: str = "medium"
    instructions: str | None = Field(default=None, max_length=2000)
    student_ids: list[int] = Field(default_factory=list, max_length=200)
    cohort_id: int | None = Field(default=None, ge=1)

    @field_validator("instructions", mode="before")
    @classmethod
    def trim(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v

    @field_validator("difficulty")
    @classmethod
    def level(cls, v):
        if v not in DIFFICULTIES:
            raise ValueError("Choose easy, medium, hard or expert.")
        return v

    @model_validator(mode="after")
    def who(self):
        if bool(self.student_ids) == (self.cohort_id is not None):
            raise ValueError("Choose learners or a cohort, not both.")
        return self


class Change(Window):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=1000)


class Cancel(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=1000)


def serialize(row, job_name, now, student=None):
    return {
        "id": row.id, "course_id": row.course_id, "job_profile_id": row.job_profile_id,
        "job_name": job_name, "difficulty": row.difficulty, "opens_at": row.opens_at,
        "closes_at": row.closes_at, "instructions": row.instructions,
        "accommodations": row.accommodations or [], "status": row.status,
        "phase": service.phase(row, now), "version": row.version, "interview_id": row.interview_id,
        "created_at": row.created_at,
    } | ({"student": student, "student_id": row.student_id} if student is not None else {})


def audit(session, actor_id, row, action, reason):
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=actor_id, action=action, purpose="assessment_assignment",
            target_type="assessment_assignment", target_id=str(row.id), reason=reason,
            event_metadata={
                "course_id": row.course_id, "student_id": row.student_id,
                "job_profile_id": row.job_profile_id, "status": row.status, "version": row.version,
            },
        )
    )


async def listing(session, predicate, now):
    rows = (
        await session.execute(
            sa.select(AssessmentAssignment, JobProfile.job_name, User.name)
            .join(JobProfile, JobProfile.id == AssessmentAssignment.job_profile_id)
            .join(User, User.id == AssessmentAssignment.student_id)
            .where(predicate)
            .order_by(AssessmentAssignment.opens_at.desc(), AssessmentAssignment.id.desc())
            .limit(200)
        )
    ).all()
    await service.settle(session, [r for r, _, _ in rows])
    return rows


@router.get("/me")
async def mine(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    """The learner's own assigned AI assessments: what, when it opens and closes, and state."""
    response.headers["Cache-Control"] = "no-store"
    now = dt.datetime.now(UTC)
    rows = await listing(session, AssessmentAssignment.student_id == user.id, now)
    return {"items": [serialize(r, name, now) for r, name, _ in rows if r.status != "cancelled"]}


@router.get("/courses/{course_id}")
async def staff_list(
    course_id: str,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    predicate = await service.authorize(user, course_id, session, "read")
    response.headers["Cache-Control"] = "no-store"
    now = dt.datetime.now(UTC)
    rows = await listing(
        session,
        sa.and_(
            AssessmentAssignment.course_id == course_id,
            AssessmentAssignment.student_id.in_(sa.select(User.id).where(predicate)),
        ),
        now,
    )
    return {
        "items": [serialize(r, name, now, student) for r, name, student in rows],
        "can_create": service.permitted(user, "create"),
        "can_update": service.permitted(user, "update"),
    }


@router.get("/courses/{course_id}/options")
async def options(
    course_id: str,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    predicate = await service.authorize(user, course_id, session, "create")
    templates = (
        await session.execute(
            sa.select(JobProfile.id, JobProfile.job_name)
            .where(JobProfile.interview_mode == "assessment", JobProfile.status == "published")
            .order_by(JobProfile.job_name).limit(200)
        )
    ).all()
    cohorts = (
        await session.execute(
            sa.select(Cohort.id, Cohort.name)
            .where(Cohort.course_id == course_id, Cohort.archived_at.is_(None)).order_by(Cohort.name)
        )
    ).all()
    students = (
        await session.execute(
            sa.select(User.id, User.name).where(predicate, service.student_only()).order_by(User.name).limit(500)
        )
    ).all()
    return {
        "templates": [{"id": i, "name": n} for i, n in templates],
        "cohorts": [{"id": i, "name": n} for i, n in cohorts],
        "students": [{"id": i, "name": n} for i, n in students],
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
    template = await session.get(JobProfile, body.job_profile_id)
    if template is None or template.interview_mode != "assessment" or template.status != "published":
        raise fastapi.HTTPException(422, "Choose a published assessment template.")
    reason = unavailable_reason(template, body.difficulty)
    if reason is not None:
        raise fastapi.HTTPException(422, reason)
    if body.closes_at is not None and body.closes_at <= dt.datetime.now(UTC):
        raise fastapi.HTTPException(422, "The window must end in the future.")
    if body.cohort_id is not None:
        cohort = await session.get(Cohort, body.cohort_id)
        if cohort is None or cohort.course_id != course_id or cohort.archived_at is not None:
            raise fastapi.HTTPException(404, "Cohort not found.")
        wanted = (
            await session.execute(
                sa.select(CohortMember.student_id).where(CohortMember.cohort_id == cohort.id)
            )
        ).scalars().all()
    else:
        wanted = list(dict.fromkeys(body.student_ids))
    if not wanted:
        raise fastapi.HTTPException(422, "There is nobody to assign this to.")
    allowed = set(
        (
            await session.execute(
                sa.select(User.id).where(predicate, service.student_only(), User.id.in_(wanted))
            )
        ).scalars().all()
    )
    missing = [s for s in wanted if s not in allowed]
    if missing and body.cohort_id is None:
        raise fastapi.HTTPException(404, "Some learners are not in your scope.")
    now = dt.datetime.now(UTC)
    made, replayed, skipped = [], 0, len(missing)
    for student_id in sorted(allowed):
        key = str(body.request_key)
        existing = (
            await session.execute(
                sa.select(AssessmentAssignment).where(
                    AssessmentAssignment.student_id == student_id, AssessmentAssignment.request_key == key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            replayed += 1
            continue
        row = AssessmentAssignment(
            student_id=student_id, assigned_by=user.id, course_id=course_id, cohort_id=body.cohort_id,
            job_profile_id=template.id, pinned_revision=template.content_revision or 0,
            difficulty=body.difficulty, opens_at=body.opens_at, closes_at=body.closes_at,
            instructions=body.instructions, request_key=key,
            accommodations=await confirmed_conditions(session, student_id),
        )
        session.add(row)
        await session.flush()
        audit(session, user.id, row, "assessment_assignment_created", "Assigned an AI assessment.")
        notifications.notify(
            session, student_id, "assessment_assigned", "An AI interview was assigned to you",
            f"{template.job_name} opens on {body.opens_at.date().isoformat()}.",
            "/assessments", ("assessment_assignment", row.id),
        )
        made.append(row)
    await session.commit()
    if not made and replayed:
        response.status_code = 200
    return {
        "created": len(made), "already_assigned": replayed, "skipped_out_of_scope": skipped,
        "items": [serialize(r, template.job_name, now) | {"student_id": r.student_id} for r in made],
    }


async def locked(session, course_id, assignment_id, user, operation):
    predicate = await service.authorize(user, course_id, session, operation)
    row = (
        await session.execute(
            sa.select(AssessmentAssignment)
            .where(
                AssessmentAssignment.id == assignment_id,
                AssessmentAssignment.course_id == course_id,
                AssessmentAssignment.student_id.in_(sa.select(User.id).where(predicate)),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Assignment not found.")
    return row


@router.post("/courses/{course_id}/{assignment_id}/reschedule")
async def reschedule(
    course_id: str, assignment_id: int, body: Change,
    user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session),
):
    """Move the window, e.g. after it closed. A changed template still needs a new assignment."""
    row = await locked(session, course_id, assignment_id, user, "update")
    if row.version != body.expected_version:
        raise fastapi.HTTPException(409, "Assignment changed. Reload it.")
    if row.status != "assigned":
        raise fastapi.HTTPException(409, "Only an assignment that has not started can be rescheduled.")
    if body.closes_at is not None and body.closes_at <= dt.datetime.now(UTC):
        raise fastapi.HTTPException(422, "The window must end in the future.")
    row.opens_at, row.closes_at, row.version = body.opens_at, body.closes_at, row.version + 1
    audit(session, user.id, row, "assessment_assignment_rescheduled", body.reason)
    notifications.notify(
        session, row.student_id, "assessment_rescheduled", "Your AI interview time changed",
        "Open it to see the new window.", "/assessments", ("assessment_assignment", row.id),
    )
    await session.commit()
    name = (await session.get(JobProfile, row.job_profile_id)).job_name
    return serialize(row, name, dt.datetime.now(UTC))


@router.post("/courses/{course_id}/{assignment_id}/cancel")
async def cancel(
    course_id: str, assignment_id: int, body: Cancel,
    user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session),
):
    row = await locked(session, course_id, assignment_id, user, "update")
    if row.version != body.expected_version:
        raise fastapi.HTTPException(409, "Assignment changed. Reload it.")
    if row.status != "assigned":
        raise fastapi.HTTPException(409, "Only an assignment that has not started can be cancelled.")
    row.status, row.version, row.closed_at = "cancelled", row.version + 1, dt.datetime.now(UTC)
    audit(session, user.id, row, "assessment_assignment_cancelled", body.reason)
    await session.commit()
    name = (await session.get(JobProfile, row.job_profile_id)).job_name
    return serialize(row, name, dt.datetime.now(UTC))
