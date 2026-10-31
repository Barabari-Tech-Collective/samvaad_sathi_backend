"""Availability rules for assigned AI assessments (architecture pack F14).

Assigning is additive: a learner with no assignment for a template keeps today's open access.
Once an assignment exists the learner can start only inside its window, on the pinned revision."""

import datetime as dt

import fastapi
import sqlalchemy as sa

from src.services import notifications
from src.services.accommodations import confirmed_conditions

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


async def create_for(
    session, user, *, course_id, template, difficulty, student_ids, opens_at, closes_at,
    instructions, request_key, cohort_id=None, round_id=None, audit=None,
):
    """Create one assignment per learner (idempotent per learner and request key).
    Returns (new rows, replayed count). The caller commits."""
    made, replayed = [], 0
    for student_id in sorted(set(student_ids)):
        key = str(request_key)
        existing = (
            await session.execute(
                sa.select(AssessmentAssignment.id).where(
                    AssessmentAssignment.student_id == student_id,
                    AssessmentAssignment.request_key == key,
                )
            )
        ).first()
        if existing is not None:
            replayed += 1
            continue
        row = AssessmentAssignment(
            student_id=student_id, assigned_by=user.id, course_id=course_id, cohort_id=cohort_id,
            opportunity_round_id=round_id, job_profile_id=template.id,
            pinned_revision=template.content_revision or 0, difficulty=difficulty,
            opens_at=opens_at, closes_at=closes_at, instructions=instructions, request_key=key,
            accommodations=await confirmed_conditions(session, student_id),
        )
        session.add(row)
        await session.flush()
        if audit:
            audit(session, user.id, row, "assessment_assignment_created", "Assigned an AI assessment.")
        notifications.notify(
            session, student_id, "assessment_assigned", "An AI interview was assigned to you",
            f"{template.job_name} opens on {opens_at.date().isoformat()}.",
            "/assessments", ("assessment_assignment", row.id),
        )
        made.append(row)
    return made, replayed
