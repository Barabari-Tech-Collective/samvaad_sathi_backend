"""Course-bound assignment authorization, lifecycle and atomic completion."""

import datetime
import fastapi
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from src.api.dependencies.admin import is_admin_user
from src.api.routes.course_workspace import _shared_student_predicate
from src.models.db.coaching_assignment import CoachingAssignment
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.practice_drill_completion import PracticeDrillCompletion
from src.models.db.user import User
from src.services.course_access import student_predicate
from src.services.feature_access import require_permission


def serialize(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "course_id",
            "module",
            "reason",
            "note",
            "due_date",
            "status",
            "version",
            "created_at",
            "closed_at",
        )
    }


def audit(session, actor_id, row, action, reason):
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=actor_id,
            action=action,
            purpose="facilitator_coaching",
            target_type="coaching_assignment",
            target_id=str(row.id),
            reason=reason,
            event_metadata={"course_id": row.course_id, "student_id": row.student_id},
        )
    )


async def authorize_staff(session, user, course_id, student_id, operation):
    if not is_admin_user(user):
        require_permission(user.feature_permissions, "course_workspace", "read")
        require_permission(user.feature_permissions, "practice_coaching", operation)
    # Coaching responsibility and consent must be paired to this exact course/college.
    predicate = await _shared_student_predicate(
        course_id, user, session, coaching_only=True
    )
    student = (
        await session.execute(
            sa.select(User.id).where(predicate, User.id == student_id)
        )
    ).scalar_one_or_none()
    if student is None:
        raise fastapi.HTTPException(404, "Shared coaching student not found.")


async def require_owned_enrollment(session, user_id, course_id):
    active = (
        await session.execute(
            sa.select(User.id).where(
                User.id == user_id, student_predicate(course_id, (), global_access=True)
            )
        )
    ).scalar_one_or_none()
    if active is None:
        raise fastapi.HTTPException(
            403, "Current course enrollment is required to finish this assignment."
        )


async def complete_assignment(session, *, user_id, payload):
    row = (
        await session.execute(
            sa.select(CoachingAssignment)
            .where(
                CoachingAssignment.id == payload.assignment_id,
                CoachingAssignment.student_id == user_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Assignment not found.")
    if row.module != payload.module or payload.evidence_kind != "audio":
        raise fastapi.HTTPException(
            422, "Complete the assigned speaking drill with voice."
        )
    if row.status == "completed":
        evidence = await session.get(PracticeDrillCompletion, row.completion_id)
        if (
            evidence.client_completion_id != payload.client_completion_id
            or evidence.level != payload.level
            or evidence.evidence_units != payload.evidence_units
        ):
            raise fastapi.HTTPException(409, "This assignment is already completed.")
        return evidence, False
    if row.status != "assigned":
        raise fastapi.HTTPException(409, "This assignment has been cancelled.")
    await require_owned_enrollment(session, user_id, row.course_id)
    # Insert and assignment transition commit together. Existing general-practice
    # receipts cannot be reused or attributed retroactively to an assignment.
    evidence_id = (
        await session.execute(
            insert(PracticeDrillCompletion)
            .values(
                user_id=user_id,
                module=payload.module,
                level=payload.level,
                client_completion_id=payload.client_completion_id,
                evidence_kind=payload.evidence_kind,
                evidence_units=payload.evidence_units,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "client_completion_id"])
            .returning(PracticeDrillCompletion.id)
        )
    ).scalar_one_or_none()
    if evidence_id is None:
        raise fastapi.HTTPException(
            409, "This completion receipt was already used. Start a new drill."
        )
    row.status = "completed"
    row.completion_id = evidence_id
    row.version += 1
    row.closed_at = datetime.datetime.now(datetime.timezone.utc)
    audit(
        session,
        user_id,
        row,
        "coaching_assignment_completed",
        "Student finished the linked speaking drill; client-reported activity, not a verified skill score.",
    )
    await session.commit()
    return await session.get(PracticeDrillCompletion, evidence_id), True
