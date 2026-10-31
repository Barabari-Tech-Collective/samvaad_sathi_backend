"""Availability rules for assigned AI assessments (architecture pack F14).

Assigning is additive: a learner with no assignment for a template keeps today's open access.
Once an assignment exists the learner can start only inside its window, on the pinned revision."""

import datetime as dt

import fastapi
import sqlalchemy as sa

from src.api.dependencies.admin import is_admin_user
from src.models.db.assessment_assignment import AssessmentAssignment
from src.services.course_access import grants_for, require_course, student_predicate
from src.services.feature_access import permits

UTC = dt.timezone.utc


def permitted(user, operation):
    return is_admin_user(user) or (
        isinstance(user.feature_permissions, dict)
        and permits(user.feature_permissions, "assessment_assignments", operation)
    )


async def authorize(user, course_id, session, operation):
    if not permitted(user, operation):
        raise fastapi.HTTPException(403, "An administrator must enable assigning AI assessments for your account.")
    grants = await grants_for(user, session)
    require_course(course_id, grants, user)
    return student_predicate(course_id, grants, global_access=is_admin_user(user))


def phase(row, now) -> str:
    """upcoming | open | closed | started | completed | cancelled"""
    if row.status != "assigned":
        return row.status
    if now < row.opens_at:
        return "upcoming"
    if row.closes_at is not None and now >= row.closes_at:
        return "closed"
    return "open"


async def start_check(session, student_id, job_profile, course_id, now=None):
    """Raise 409 with a next step if this learner holds assignments but none is startable.
    Returns the assignment to link, or None when nothing is assigned (open access)."""
    now = now or dt.datetime.now(UTC)
    rows = (
        await session.execute(
            sa.select(AssessmentAssignment)
            .where(
                AssessmentAssignment.student_id == student_id,
                AssessmentAssignment.job_profile_id == job_profile.id,
                AssessmentAssignment.status.in_(("assigned", "started")),
                *(
                    [AssessmentAssignment.course_id == course_id]
                    if course_id is not None
                    else []
                ),
            )
            .order_by(AssessmentAssignment.opens_at)
            .with_for_update()
        )
    ).scalars().all()
    if not rows:
        return None
    started = next((r for r in rows if r.status == "started"), None)
    if started:
        return started
    startable = [r for r in rows if phase(r, now) == "open"]
    if not startable:
        if all(phase(r, now) == "upcoming" for r in rows):
            raise fastapi.HTTPException(
                409, f"This assessment opens on {min(r.opens_at for r in rows).isoformat()}."
            )
        raise fastapi.HTTPException(
            409, "The time to start this assessment has ended. Ask your facilitator to reschedule it."
        )
    row = startable[0]
    if (job_profile.content_revision or 0) != row.pinned_revision:
        raise fastapi.HTTPException(
            409, "This assessment was changed after it was assigned. Ask your facilitator to assign it again."
        )
    return row


async def settle(session, rows):
    """Mark started assignments completed once their interview has finished."""
    from src.models.db.interview import Interview

    changed = False
    for row in rows:
        if row.status != "started" or row.interview_id is None:
            continue
        done = (
            await session.execute(
                sa.select(Interview.completed_at).where(
                    Interview.id == row.interview_id, Interview.status == "completed"
                )
            )
        ).first()
        if done:
            row.status, row.closed_at = "completed", done[0]
            row.version += 1
            changed = True
    if changed:
        await session.commit()


def student_only():
    from src.services.people import student_only as f

    return f()
