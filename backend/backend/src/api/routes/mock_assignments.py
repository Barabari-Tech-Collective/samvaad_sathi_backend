"""Mock interview assignments: a teacher asks a learner, in a course, to take one mock.

Same rules as speaking-drill follow-ups (course workspace read plus practice coaching for the
operation, the learner's coaching consent and current enrollment), kept in their own table
and routes. It completes itself when the learner finishes a mock of that template in that
course after it was assigned. Mocks stay rehearsal: assigning one never makes it count.
"""

import datetime
import hashlib
import json
from uuid import UUID

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.interview import Interview
from src.models.db.job_profile import JobProfile
from src.models.db.mock_assignment import MockInterviewAssignment
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.services import notifications
from src.services.coaching_assignments import authorize_staff, lock_student
from src.services.feature_access import permits

router = fastapi.APIRouter(prefix="/v2/mock-assignments", tags=["mock-assignments"])

UTC = datetime.timezone.utc
NOTICE = "Mock interviews are rehearsal. Taking one never counts toward selection."


class Create(BaseModel):
    request_key: UUID
    job_profile_id: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=1000)
    note: str | None = Field(default=None, max_length=2000)
    due_date: datetime.date | None = None

    @field_validator("reason", "note", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class Cancel(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


def audit(session, actor_id, row, action, reason):
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=actor_id,
            action=action,
            purpose="facilitator_coaching",
            target_type="mock_assignment",
            target_id=str(row.id),
            reason=reason,
            event_metadata={
                "course_id": row.course_id,
                "student_id": row.student_id,
                "job_profile_id": row.job_profile_id,
                "status": row.status,
                "version": row.version,
            },
        )
    )


async def settle(session, rows):
    """Close assignments the learner has since completed with a finished mock."""
    changed = False
    for row in rows:
        if row.status != "assigned":
            continue
        done = (
            await session.execute(
                sa.select(Interview.id, Interview.completed_at)
                .where(
                    Interview.user_id == row.student_id,
                    Interview.course_id == row.course_id,
                    Interview.job_profile_id == row.job_profile_id,
                    Interview.interview_mode == "practice",
                    Interview.status == "completed",
                    Interview.completed_at >= row.created_at,
                )
                .order_by(Interview.completed_at)
                .limit(1)
            )
        ).first()
        if done:
            row.status, row.interview_id = "completed", done.id
            row.closed_at = done.completed_at
            row.version += 1
            changed = True
    if changed:
        await session.commit()


async def page_of(session, predicate, page, limit):
    total = (
        await session.execute(
            sa.select(sa.func.count()).select_from(MockInterviewAssignment).where(predicate)
        )
    ).scalar_one()
    rows = (
        await session.execute(
            sa.select(MockInterviewAssignment, JobProfile.job_name)
            .join(JobProfile, JobProfile.id == MockInterviewAssignment.job_profile_id)
            .where(predicate)
            .order_by(MockInterviewAssignment.created_at.desc(), MockInterviewAssignment.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()
    await settle(session, [r for r, _ in rows])
    return {
        "items": [serialize(r, name) for r, name in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "notice": NOTICE,
    }


def serialize(row, job_name):
    return {
        "id": row.id,
        "course_id": row.course_id,
        "job_profile_id": row.job_profile_id,
        "job_name": job_name,
        "reason": row.reason,
        "note": row.note,
        "due_date": row.due_date,
        "status": row.status,
        "version": row.version,
        "interview_id": row.interview_id,
        "created_at": row.created_at,
        "closed_at": row.closed_at,
    }


@router.get("/me")
async def own_mock_assignments(
    response: fastapi.Response,
    page: int = fastapi.Query(1, ge=1),
    limit: int = fastapi.Query(25, ge=1, le=100),
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    """The learner's own: which mock to take (job_profile_id), why, by when, and status."""
    response.headers["Cache-Control"] = "no-store"
    return await page_of(session, MockInterviewAssignment.student_id == user.id, page, limit)


@router.get("/courses/{course_id}/students/{student_id}")
async def staff_mock_assignments(
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
    result = await page_of(
        session,
        sa.and_(
            MockInterviewAssignment.course_id == course_id,
            MockInterviewAssignment.student_id == student_id,
        ),
        page,
        limit,
    )
    may = lambda op: is_admin_user(user) or permits(user.feature_permissions, "practice_coaching", op)  # noqa: E731
    return result | {"can_create": may("create"), "can_cancel": may("update")}


@router.post("/courses/{course_id}/students/{student_id}", status_code=201)
async def create_mock_assignment(
    course_id: str,
    student_id: int,
    body: Create,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    await authorize_staff(session, user, course_id, student_id, "create")
    await lock_student(session, student_id)
    await authorize_staff(session, user, course_id, student_id, "create")
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
    existing = (
        await session.execute(
            sa.select(MockInterviewAssignment).where(
                MockInterviewAssignment.assigned_by == user.id,
                MockInterviewAssignment.request_key == str(body.request_key),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise fastapi.HTTPException(
                409, "This request key belongs to a different assignment. Reload before creating another."
            )
        response.status_code = 200
        return serialize(existing, (await session.get(JobProfile, existing.job_profile_id)).job_name)
    template = await session.get(JobProfile, body.job_profile_id)
    if template is None or template.interview_mode != "practice" or template.status != "published":
        raise fastapi.HTTPException(422, "Choose a published mock interview template.")
    if body.due_date and body.due_date < datetime.datetime.now(UTC).date():
        raise fastapi.HTTPException(422, "Choose today or a future due date (UTC).")
    row = MockInterviewAssignment(
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
        audit(session, user.id, row, "mock_assignment_created", body.reason)
        notifications.notify(
            session, student_id, "mock_assigned", "A mock interview was assigned to you",
            "Your teacher asked you to take a mock interview.", "/mock-interviews",
            ("mock_assignment", row.id),
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise fastapi.HTTPException(
            409, "An assignment request changed concurrently. Retry with the same request key."
        )
    await session.refresh(row)
    response.headers["Cache-Control"] = "no-store"
    return serialize(row, template.job_name)


@router.post("/courses/{course_id}/students/{student_id}/{assignment_id}/cancel")
async def cancel_mock_assignment(
    course_id: str,
    student_id: int,
    assignment_id: int,
    body: Cancel,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    await authorize_staff(session, user, course_id, student_id, "update")
    await lock_student(session, student_id)
    await authorize_staff(session, user, course_id, student_id, "update")
    row = (
        await session.execute(
            sa.select(MockInterviewAssignment)
            .where(
                MockInterviewAssignment.id == assignment_id,
                MockInterviewAssignment.student_id == student_id,
                MockInterviewAssignment.course_id == course_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Assignment not found.")
    if row.version != body.expected_version:
        raise fastapi.HTTPException(409, "Assignment changed. Reload its current status.")
    if row.status != "assigned":
        raise fastapi.HTTPException(409, "Only an open assignment can be cancelled.")
    row.status, row.version = "cancelled", row.version + 1
    row.closed_at = datetime.datetime.now(UTC)
    audit(session, user.id, row, "mock_assignment_cancelled", body.reason)
    await session.commit()
    return serialize(row, (await session.get(JobProfile, row.job_profile_id)).job_name)
