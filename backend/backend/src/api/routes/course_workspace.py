"""Course-scoped coaching reads and administrator-managed scope assignments.

No raw audio, transcript or cross-course report data is serialized. Academic
enrollment is a trusted central-course snapshot, not student-editable profile
data. Legacy untagged interviews remain invisible to scoped staff.
"""

from __future__ import annotations

import datetime

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.interview import Interview
from src.models.db.practice_governance import (
    PracticeConsent,
    PracticeDashboardAuditEvent,
)
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.services.course_access import grants_for, require_course, student_predicate
from src.api.dependencies.admin import get_user_active_memberships
from src.api.routes.practice_dashboard import resolve_dashboard_role

router = fastapi.APIRouter(prefix="/v2/course-workspace", tags=["course-workspace"])


class CourseAssignment(BaseModel):
    expected_version: str | None = Field(default=None, min_length=64, max_length=64)
    course_ids: list[str] = Field(max_length=100)
    reason: str = Field(min_length=10, max_length=1000)

    @field_validator("course_ids")
    @classmethod
    def valid_ids(cls, value: list[str]) -> list[str]:
        if any(
            not x.strip() or len(x.strip()) > 128 or x.strip() == "*" for x in value
        ):
            raise ValueError(
                "Use explicit central course IDs, without blank or wildcard IDs."
            )
        return sorted(set(x.strip() for x in value))

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


def assignment_version(member) -> str:
    import hashlib
    import json

    payload = {
        "course_ids": member.course_ids,
        "colleges": member.colleges,
        "access_role": member.access_role,
        "status": member.status,
        "team_id": member.team_id,
        "domain": member.domain,
        "purposes": member.purposes,
        "updated_at": member.updated_at.isoformat() if member.updated_at else None,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _audit(
    session, user: User, target: str, target_id: int, body: CourseAssignment
) -> None:
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id,
            action="course_scope_updated",
            purpose="course_access",
            target_type=target,
            target_id=str(target_id),
            reason=body.reason,
            event_metadata={"course_ids": body.course_ids},
        )
    )


@router.get("/me")
async def my_course_scope(
    current_user: User = fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    grants = await grants_for(current_user, session)
    return {
        "global_access": is_admin_user(current_user),
        "course_ids": sorted({c for g in grants for c in g.course_ids}),
        "grants": [
            {
                "course_ids": sorted(g.course_ids),
                "colleges": None if g.colleges is None else sorted(g.colleges),
            }
            for g in grants
        ],
        "catalogue_source": "central_course_ids",
        "unassigned_access": "denied",
    }


async def _shared_student_predicate(course_id, current_user, session):
    grants = await grants_for(current_user, session)
    require_course(course_id, grants, current_user)
    global_access = is_admin_user(current_user)
    predicate = student_predicate(course_id, grants, global_access=global_access)
    memberships = await get_user_active_memberships(current_user.id, session)
    role = resolve_dashboard_role(current_user, memberships)
    if role not in {"facilitator", "placements", "admin", "super_admin"}:
        raise fastapi.HTTPException(
            403, "A coaching or placement responsibility is required."
        )
    purpose = (
        "opportunity_readiness" if role == "placements" else "facilitator_coaching"
    )
    # A course grant never substitutes for the student's purpose-specific choice.
    now = datetime.datetime.now(datetime.timezone.utc)
    consenting = sa.exists(
        sa.select(PracticeConsent.id).where(
            PracticeConsent.user_id == User.id,
            PracticeConsent.purpose == purpose,
            PracticeConsent.granted.is_(True),
            PracticeConsent.withdrawn_at.is_(None),
            sa.or_(
                PracticeConsent.expires_at.is_(None), PracticeConsent.expires_at > now
            ),
        )
    )
    is_staff = sa.exists(
        sa.select(TeamMember.id).where(
            TeamMember.user_id == User.id, TeamMember.status == "active"
        )
    )
    predicate = sa.and_(
        predicate,
        consenting,
        ~is_staff,
        User.role.is_(None),
        User.is_admin.is_(False),
        User.is_super_admin.is_(False),
    )
    return predicate


@router.get("/courses/{course_id}/students")
async def course_students(
    course_id: str,
    page: int = fastapi.Query(1, ge=1),
    limit: int = fastapi.Query(25, ge=1, le=100),
    current_user: User = fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    predicate = await _shared_student_predicate(course_id, current_user, session)
    total = (
        await session.execute(
            sa.select(sa.func.count()).select_from(User).where(predicate)
        )
    ).scalar_one()
    # Only interviews explicitly bound to this course count, even when a student
    # is enrolled in several courses or changed their target role.
    completed = (
        sa.select(sa.func.count(Interview.id))
        .where(
            Interview.user_id == User.id,
            Interview.course_id == course_id,
            Interview.interview_mode == "practice",
            Interview.status == "completed",
        )
        .correlate(User)
        .scalar_subquery()
    )
    result = await session.execute(
        sa.select(
            User.id, User.name, User.university, completed.label("completed_practice")
        )
        .where(predicate)
        .order_by(User.id)
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return {
        "course_id": course_id,
        "page": page,
        "limit": limit,
        "total": total,
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "college": row.university,
                "completed_practice": row.completed_practice,
            }
            for row in result.all()
        ],
        "notice": "Current coaching consent and course scope required. Untagged legacy work is excluded.",
    }


@router.get("/courses/{course_id}/students/{student_id}/practice")
async def student_course_practice(
    course_id: str,
    student_id: int,
    page: int = fastapi.Query(1, ge=1),
    limit: int = fastapi.Query(25, ge=1, le=100),
    current_user: User = fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    predicate = await _shared_student_predicate(course_id, current_user, session)
    student = (await session.execute(
        sa.select(User.id, User.name, User.university).where(predicate, User.id == student_id)
    )).one_or_none()
    if student is None:
        raise fastapi.HTTPException(404, "Shared student history not found.")
    conditions = (
        Interview.user_id == student_id,
        Interview.course_id == course_id,
        Interview.interview_mode == "practice",
        Interview.status == "completed",
    )
    total = (await session.execute(
        sa.select(sa.func.count()).select_from(Interview).where(*conditions)
    )).scalar_one()
    rows = (await session.execute(
        sa.select(Interview.id, Interview.track, Interview.difficulty,
                  Interview.completed_at, Interview.duration_seconds)
        .where(*conditions)
        .order_by(Interview.completed_at.desc().nullslast(), Interview.id.desc())
        .offset((page - 1) * limit).limit(limit)
    )).all()
    return {
        "course_id": course_id,
        "student": {"id": student.id, "name": student.name, "college": student.university},
        "page": page, "limit": limit, "total": total,
        "items": [{"id": row.id, "track": row.track, "difficulty": row.difficulty,
                   "completed_at": row.completed_at, "duration_seconds": row.duration_seconds}
                  for row in rows],
        "notice": "Completed practice in this course only. Detailed feedback and recordings are not included.",
    }


@router.put("/members/{member_id}/courses")
async def assign_member_courses(
    member_id: int,
    body: CourseAssignment,
    current_user: User = fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if not is_admin_user(current_user):
        raise fastapi.HTTPException(403, "An administrator must approve course access.")
    member = (
        await session.execute(
            sa.select(TeamMember)
            .join(Team, TeamMember.team_id == Team.id)
            .where(
                TeamMember.id == member_id,
                TeamMember.status == "active",
                Team.status == "active",
            )
            .with_for_update(of=TeamMember)
        )
    ).scalar_one_or_none()
    if member is None:
        raise fastapi.HTTPException(404, "Active membership not found.")
    if body.expected_version is None:
        raise fastapi.HTTPException(
            428, "Reload this membership before changing course access."
        )
    if body.expected_version != assignment_version(member):
        raise fastapi.HTTPException(
            409,
            "This membership changed. Reload it and review the current course and college access.",
        )
    member.course_ids = body.course_ids
    _audit(session, current_user, "team_member", member_id, body)
    await session.commit()
    return {
        "member_id": member_id,
        "course_ids": body.course_ids,
        "version": assignment_version(member),
    }


@router.get("/members")
async def course_members(
    current_user: User = fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if not is_admin_user(current_user):
        raise fastapi.HTTPException(403, "Administrator access required.")
    result = await session.execute(
        sa.select(TeamMember, User.name, Team.name.label("team_name"))
        .join(User, User.id == TeamMember.user_id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.status == "active", Team.status == "active")
        .order_by(TeamMember.id)
        .limit(200)
    )
    return [
        {
            "version": assignment_version(row[0]),
            "id": row[0].id,
            "name": row.name,
            "team": row.team_name,
            "course_ids": row[0].course_ids or [],
            "colleges": row[0].colleges,
        }
        for row in result.all()
    ]


@router.put("/students/{student_id}/enrollments")
async def sync_student_enrollments(
    student_id: int,
    body: CourseAssignment,
    current_user: User = fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if not is_admin_user(current_user):
        raise fastapi.HTTPException(
            403, "Only administrators may synchronize central enrollments."
        )
    from src.services.sampark_enrollment_sync import enabled

    if enabled():
        raise fastapi.HTTPException(
            409,
            "Sampark is the authoritative enrollment source. Update enrollment there and synchronize.",
        )
    student = (
        await session.execute(
            sa.select(User).where(User.id == student_id).with_for_update()
        )
    ).scalar_one_or_none()
    if student is None or student.role is not None or is_admin_user(student):
        raise fastapi.HTTPException(404, "Student not found.")
    student.enrolled_course_ids = body.course_ids
    _audit(session, current_user, "student_enrollment", student_id, body)
    await session.commit()
    return {
        "student_id": student_id,
        "course_ids": body.course_ids,
        "source": "administrator_verified_central_snapshot",
    }


@router.post("/enrollments/sync-from-sampark")
async def pull_sampark_enrollments(
    current_user: User = fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if not is_admin_user(current_user):
        raise fastapi.HTTPException(403, "Administrator access required")
    from src.services.sampark_enrollment_sync import synchronize

    try:
        return await synchronize(session)
    except Exception:
        await session.rollback()
        raise fastapi.HTTPException(
            503, "Sampark enrollment sync failed. No partial snapshot was committed."
        )


@router.get("/enrollments/status")
async def enrollment_sync_status(
    current_user: User = fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if not is_admin_user(current_user):
        raise fastapi.HTTPException(403, "Administrator access required")
    from src.services.sampark_enrollment_sync import (
        enabled,
        max_age,
        freshness_predicate,
    )

    counts = (
        await session.execute(
            sa.select(
                sa.func.count(User.id).label("students"),
                sa.func.count(User.id)
                .filter(User.enrollment_source == "sampark")
                .label("synced"),
                sa.func.count(User.id).filter(freshness_predicate()).label("fresh"),
                sa.func.max(User.enrollment_observed_at).label("latest_observation"),
            ).where(
                User.role.is_(None),
                User.is_admin.is_(False),
                User.is_super_admin.is_(False),
            )
        )
    ).one()
    return {
        "enabled": enabled(),
        "source": "sampark",
        "max_age_seconds": max_age(),
        "students": counts.students,
        "synced_students": counts.synced,
        "fresh_students": counts.fresh if enabled() else 0,
        "latest_observation": counts.latest_observation,
        "notice": "Freshness is enforced for new course-bound attempts and scoped roster reads when sync is enabled. Already saved evidence is retained.",
    }
