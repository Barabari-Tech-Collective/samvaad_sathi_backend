"""Placement-only course grants and consent-aware, score-free evidence."""

import datetime
import fastapi
import sqlalchemy as sa
from src.api.dependencies.admin import get_user_active_memberships, is_admin_user
from src.api.routes.practice_dashboard import resolve_dashboard_role
from src.models.db.user import User
from src.models.db.team_member import TeamMember
from src.models.db.interview import Interview
from src.models.db.practice_governance import (
    PracticeConsent,
    PracticeDashboardAuditEvent,
)
from src.services.course_access import membership_grants, student_predicate
from src.services.feature_access import permits

UTC = datetime.timezone.utc


def permission(user, operation):
    return is_admin_user(user) or (
        isinstance(user.feature_permissions, dict)
        and permits(user.feature_permissions, "placement_shortlists", operation)
    )


async def placement_grants(user, course_id, session):
    if is_admin_user(user):
        return ()
    memberships = await get_user_active_memberships(user.id, session)
    grants = []
    for item in memberships:
        if resolve_dashboard_role(user, [item]) != "placements":
            continue
        grants.extend(
            grant
            for grant in membership_grants([item])
            if course_id in grant.course_ids
        )
    return tuple(grants)


async def can_open(user, course_id, session):
    return bool(
        permission(user, "read")
        and (is_admin_user(user) or await placement_grants(user, course_id, session))
    )


async def authorize(user, course_id, session, operation):
    if not permission(user, operation):
        raise fastapi.HTTPException(
            403,
            "An administrator must enable this shortlist operation for your account.",
        )
    grants = await placement_grants(user, course_id, session)
    if not is_admin_user(user) and not grants:
        raise fastapi.HTTPException(
            403, "A placement responsibility assigned to this course is required."
        )
    now = datetime.datetime.now(UTC)
    consent = sa.exists(
        sa.select(PracticeConsent.id).where(
            PracticeConsent.user_id == User.id,
            PracticeConsent.purpose == "opportunity_readiness",
            PracticeConsent.granted.is_(True),
            PracticeConsent.withdrawn_at.is_(None),
            sa.or_(
                PracticeConsent.expires_at.is_(None), PracticeConsent.expires_at > now
            ),
        )
    )
    staff = sa.exists(
        sa.select(TeamMember.id).where(
            TeamMember.user_id == User.id, TeamMember.status == "active"
        )
    )
    return sa.and_(
        student_predicate(course_id, grants, global_access=is_admin_user(user)),
        consent,
        ~staff,
        User.role.is_(None),
        User.is_admin.is_(False),
        User.is_super_admin.is_(False),
    )


def metadata(row):
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "course_id",
            "name",
            "opportunity",
            "status",
            "version",
            "expires_at",
            "created_at",
        )
    } | {"rules": row.evidence_rules}


def audit(session, user, row, action, reason, extra=None):
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id,
            action=action,
            purpose="opportunity_readiness",
            target_type="course_shortlist",
            target_id=str(row.id),
            reason=reason,
            event_metadata={"course_id": row.course_id, **(extra or {})},
        )
    )


def recent_evidence(course_id, days):
    conditions = (
        Interview.user_id == User.id,
        Interview.course_id == course_id,
        Interview.interview_mode == "practice",
        Interview.status == "completed",
        Interview.completed_at
        >= datetime.datetime.now(UTC) - datetime.timedelta(days=days),
    )
    count = (
        sa.select(sa.func.count(Interview.id))
        .where(*conditions)
        .correlate(User)
        .scalar_subquery()
    )
    latest = (
        sa.select(sa.func.max(Interview.completed_at))
        .where(*conditions)
        .correlate(User)
        .scalar_subquery()
    )
    return count, latest


def evidence(count, latest, minimum):
    return {
        "completed_practice": count,
        "latest_completed_at": latest.isoformat() if latest else None,
        "state": (
            "available" if count >= minimum else "insufficient" if count else "missing"
        ),
    }


def require_draft(row):
    if row.status != "draft":
        raise fastapi.HTTPException(409, "Only draft shortlists can be changed.")
    if row.expires_at <= datetime.datetime.now(UTC):
        raise fastapi.HTTPException(
            409, "This shortlist expired. Archive the draft and create a replacement."
        )
