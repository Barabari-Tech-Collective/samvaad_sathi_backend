"""Explicit Meet link-out policy. No attendance inference or AI fallback."""

import datetime as dt
import re
import fastapi
import sqlalchemy as sa
from src.api.dependencies.admin import is_admin_user
from src.models.db.user import User
from src.models.db.team_member import TeamMember
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.services.course_access import grants_for, require_course, student_predicate
from src.services.feature_access import permits

UTC = dt.timezone.utc
NOTICE = "Human interview on Google Meet in a separate tab. Your host manages admission, camera and audio there. Samvaad does not detect attendance or record the meeting, generate an AI score, or switch to AI automatically."


def meet_url(value):
    # Exact ASCII host and standard meeting code only. No redirects, fragments,
    # query tokens, credentials, ports, encoded paths or look-alike domains.
    if not isinstance(value, str) or not re.fullmatch(
        r"https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}", value.strip()
    ):
        raise ValueError(
            "Use a Google Meet link such as https://meet.google.com/abc-defg-hij without query parameters."
        )
    return value.strip()


def permitted(user, operation):
    # New feature is opt-in; NULL legacy role defaults grant no human scheduling.
    return is_admin_user(user) or (
        isinstance(user.feature_permissions, dict)
        and permits(user.feature_permissions, "human_interviews", operation)
    )


async def authorize(user, course_id, session, operation, *, historical=False):
    if not permitted(user, operation):
        raise fastapi.HTTPException(
            403,
            "An administrator must enable this human interview operation for your account.",
        )
    grants = await grants_for(user, session)
    require_course(course_id, grants, user)
    if historical:
        if is_admin_user(user):
            return sa.true()
        clauses = []
        for grant in grants:
            if course_id not in grant.course_ids:
                continue
            if grant.colleges is None:
                return sa.true()
            if grant.colleges:
                clauses.append(
                    sa.func.lower(sa.func.trim(User.university)).in_(grant.colleges)
                )
        return sa.or_(*clauses) if clauses else sa.false()
    return student_predicate(course_id, grants, global_access=is_admin_user(user))


def student_only():
    staff = sa.exists(
        sa.select(TeamMember.id).where(
            TeamMember.user_id == User.id, TeamMember.status == "active"
        )
    )
    return sa.and_(
        ~staff,
        User.role.is_(None),
        User.is_admin.is_(False),
        User.is_super_admin.is_(False),
    )


def joinable(row, now):
    return (
        row.status == "scheduled"
        and row.starts_at - dt.timedelta(minutes=15) <= now < row.ends_at
    )


def metadata(row, now, *, can_manage=False):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "course_id",
            "student_id",
            "host_id",
            "title",
            "starts_at",
            "ends_at",
            "status",
            "version",
        )
    } | {
        "format": "human_google_meet",
        "can_join": joinable(row, now),
        "join_opens_at": row.starts_at - dt.timedelta(minutes=15),
        "can_manage": can_manage and row.status == "scheduled",
        "attendance": "not_verified",
        "outcome_source": "host_reported" if row.closed_at else None,
    }


def audit(session, actor, row, action, reason):
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=actor.id,
            action=action,
            purpose="human_interview",
            target_type="human_interview",
            target_id=str(row.id),
            reason=reason,
            event_metadata={
                "course_id": row.course_id,
                "status": row.status,
                "version": row.version,
            },
        )
    )
