"""Course-scoped coaching reads and administrator-managed scope assignments.

No raw audio, transcript or cross-course report data is serialized. Academic
enrollment is a trusted central-course snapshot, not student-editable profile
data. Legacy untagged interviews remain invisible to scoped staff.
"""

from __future__ import annotations

import datetime
from typing import Annotated, Literal

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
from src.services.course_access import grants_for, require_course, student_predicate, membership_grants
from src.api.dependencies.admin import get_user_active_memberships
from src.api.routes.practice_dashboard import resolve_dashboard_role

from src.services.course_reporting import (
    Activity, RosterFilters, RosterExport, EXPORT_LIMIT,
    completed_practice_count, roster_predicate, roster_rows, roster_csv,
)
from src.services.feature_access import permits, require_permission

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


async def _shared_student_predicate(course_id, current_user, session, *, coaching_only=False):
    memberships = await get_user_active_memberships(current_user.id, session)
    grants = membership_grants(memberships)
    require_course(course_id, grants, current_user)
    now = datetime.datetime.now(datetime.timezone.utc)

    def consent(purpose):
        return sa.exists(sa.select(PracticeConsent.id).where(
            PracticeConsent.user_id == User.id, PracticeConsent.purpose == purpose,
            PracticeConsent.granted.is_(True), PracticeConsent.withdrawn_at.is_(None),
            sa.or_(PracticeConsent.expires_at.is_(None), PracticeConsent.expires_at > now)))

    if is_admin_user(current_user):
        predicate = sa.and_(student_predicate(course_id, grants, global_access=True), consent("facilitator_coaching"))
    else:
        clauses = []
        for item in memberships:
            paired = membership_grants([item])
            if not paired or not any(course_id in grant.course_ids for grant in paired):
                continue
            role = resolve_dashboard_role(current_user, [item])
            if role not in ({"facilitator"} if coaching_only else {"facilitator", "placements"}):
                continue
            purpose = "opportunity_readiness" if role == "placements" else "facilitator_coaching"
            clauses.append(sa.and_(student_predicate(course_id, paired, global_access=False), consent(purpose)))
        if not clauses:
            raise fastapi.HTTPException(403, "A scoped coaching or placement responsibility is required.")
        predicate = sa.or_(*clauses)
    is_staff = sa.exists(
        sa.select(TeamMember.id).where(
            TeamMember.user_id == User.id, TeamMember.status == "active"
        )
    )
    predicate = sa.and_(
        predicate,
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
    q: Annotated[str, fastapi.Query(max_length=100)] = "",
    activity: Activity = "all",
    response: fastapi.Response = None,
):
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    from src.services.course_shortlists import can_open
    from src.services.human_interviews import permitted as human_permitted
    from src.services.course_support import can_open as support_can_open
    filters = RosterFilters(q=q, activity=activity)
    authorized = await _shared_student_predicate(course_id, current_user, session)
    completed = completed_practice_count(course_id)
    predicate = roster_predicate(authorized, completed, filters)
    summary = (
        await session.execute(
            sa.select(
                sa.func.count(User.id).label("students"),
                sa.func.count(User.id).filter(completed > 0).label("practised"),
                sa.func.coalesce(sa.func.sum(completed), 0).label("rounds"),
            ).where(predicate)
        )
    ).one()
    rows = (
        await session.execute(
            roster_rows(predicate, completed).offset((page - 1) * limit).limit(limit)
        )
    ).all()
    return {
        "course_id": course_id,
        "page": page,
        "limit": limit,
        "can_open_human_interviews": human_permitted(current_user, "read"),
        "can_open_support": await support_can_open(current_user, course_id, session),
        "can_open_shortlists": await can_open(current_user, course_id, session),
        "filters": filters.model_dump(),
        "total": summary.students,
        "summary": {
            "practised_students": summary.practised,
            "not_started_students": summary.students - summary.practised,
            "completed_practice_rounds": summary.rounds,
        },
        "can_export": is_admin_user(current_user)
        or (
            isinstance(current_user.feature_permissions, dict)
            and permits(current_user.feature_permissions, "course_workspace", "export")
        ),
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "college": row.university,
                "completed_practice": row.completed_practice,
            }
            for row in rows
        ],
        "notice": "Counts and results cover matching shared students in this course only. Sharing, enrollment freshness and course/college access apply. Untagged legacy work is excluded.",
    }


@router.post("/courses/{course_id}/students/export")
async def export_course_students(
    course_id: str,
    body: RosterExport,
    current_user: User = fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    # Keep this check in the handler as well as the router feature gate so an
    # alternate router mount cannot accidentally make export equivalent to read.
    if not is_admin_user(current_user):
        if not isinstance(current_user.feature_permissions, dict):
            raise fastapi.HTTPException(
                403,
                "An administrator must explicitly enable course export for your account.",
            )
        require_permission(
            current_user.feature_permissions, "course_workspace", "export"
        )
    authorized = await _shared_student_predicate(course_id, current_user, session)
    completed = completed_practice_count(course_id)
    predicate = roster_predicate(authorized, completed, body)
    rows = (
        await session.execute(roster_rows(predicate, completed).limit(EXPORT_LIMIT + 1))
    ).all()
    if len(rows) > EXPORT_LIMIT:
        raise fastapi.HTTPException(
            413,
            "Export is limited to 1,000 matching students. Narrow your search or activity filter.",
        )
    content = roster_csv(rows)
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=current_user.id,
            action="course_roster_exported",
            purpose="course_reporting",
            target_type="course",
            target_id=course_id,
            reason=body.reason,
            event_metadata={
                "rows": len(rows),
                "activity": body.activity,
                "search_applied": bool(body.q),
            },
        )
    )
    # Do not disclose the file if the audit commit fails.
    await session.commit()
    return fastapi.Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'attachment; filename="course-practice.csv"',
        },
    )


@router.get("/courses/{course_id}/support")
async def course_support(
    course_id: str,
    response: fastapi.Response,
    page: int = fastapi.Query(1, ge=1),
    limit: int = fastapi.Query(25, ge=1, le=100),
    issue: Literal[
        "all",
        "session_blocked",
        "report_delayed",
        "open_over_two_hours",
        "answer_processing_failed",
    ] = "all",
    current_user: User = fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    from src.services.course_support import allowed, support_query

    if not allowed(current_user):
        raise fastapi.HTTPException(
            403,
            "An administrator must enable the practice support queue for your account.",
        )
    authorized = await _shared_student_predicate(
        course_id, current_user, session, coaching_only=True
    )
    signals = support_query(course_id, authorized)
    predicate = signals.c.issue.is_not(None)
    if issue != "all":
        predicate = sa.and_(predicate, signals.c.issue == issue)
    summary = (
        await session.execute(
            sa.select(
                sa.func.count().label("sessions"),
                sa.func.coalesce(sa.func.sum(signals.c.failed_answers), 0).label(
                    "failed_answers"
                ),
            )
            .select_from(signals)
            .where(predicate)
        )
    ).one()
    rows = (
        (
            await session.execute(
                sa.select(signals)
                .where(predicate)
                .order_by(signals.c.created_at.asc(), signals.c.interview_id.asc())
                .offset((page - 1) * limit)
                .limit(limit)
            )
        )
        .mappings()
        .all()
    )
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=current_user.id,
            action="course_support_viewed",
            purpose="facilitator_coaching",
            target_type="course",
            target_id=course_id,
            reason="Opened the current shared practice support queue.",
            event_metadata={"issue": issue, "page": page, "returned": len(rows)},
        )
    )
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [dict(row) for row in rows],
        "total": summary.sessions,
        "failed_answers": summary.failed_answers,
        "page": page,
        "limit": limit,
        "notice": "Signals for investigation, not a diagnosis or cheating judgment. Long-open means age since creation, not proven inactivity. One primary signal per session is shown. No automatic reset or regeneration is performed.",
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


@router.get("/courses/{course_id}/students/{student_id}/practice/{interview_id}/review")
async def review_course_practice(
    course_id: str,
    student_id: int,
    interview_id: int,
    response: fastapi.Response,
    current_user: User = fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    """Release only derived feedback after current scope and consent checks."""
    from src.models.db.summary_report import SummaryReport
    from src.services.coaching_review import review_fields

    response.headers["Cache-Control"] = "no-store"
    predicate = await _shared_student_predicate(course_id, current_user, session)
    student = (await session.execute(
        sa.select(User.id).where(predicate, User.id == student_id)
    )).scalar_one_or_none()
    if student is None:
        raise fastapi.HTTPException(404, "Shared practice review not found.")
    row = (await session.execute(
        sa.select(Interview.id, SummaryReport.report_json)
        .outerjoin(SummaryReport, SummaryReport.interview_id == Interview.id)
        .where(Interview.id == interview_id, Interview.user_id == student_id,
               Interview.course_id == course_id, Interview.interview_mode == "practice",
               Interview.status == "completed")
    )).one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Shared practice review not found.")
    fields = review_fields(row.report_json)
    session.add(PracticeDashboardAuditEvent(
        actor_id=current_user.id, action="course_practice_reviewed",
        purpose="course_review", target_type="interview", target_id=str(interview_id),
        reason="Staff opened shared course practice feedback.",
        event_metadata={"course_id": course_id, "student_id": student_id,
                        "feedback_state": fields["state"]},
    ))
    # A failed audit write prevents disclosure of the feedback.
    await session.commit()
    return {"course_id": course_id, "student_id": student_id,
            "interview_id": interview_id, **fields}


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
