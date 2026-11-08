"""Explicit academic scopes, separate from Program/Placement team domains.

Course IDs are stable IDs from the central course catalogue, never inferred
from a student's target job, an email domain or a client-side filter. The
per-membership clauses remain paired so two grants cannot form an accidental
course x college cross product.
"""

from dataclasses import dataclass

import fastapi
import sqlalchemy as sa

from src.api.dependencies.admin import (
    calculate_user_permissions,
    get_current_admin_user,
    get_user_active_memberships,
    is_admin_user,
    normalize_role,
)
from src.api.dependencies.session import get_async_session
from src.models.db.user import User
from src.services.feature_access import permits


@dataclass(frozen=True)
class CourseGrant:
    course_ids: frozenset[str]
    colleges: frozenset[str] | None

    def allows(self, course_id: str, college: str | None) -> bool:
        return course_id in self.course_ids and (
            self.colleges is None or (college or "").strip().casefold() in self.colleges
        )


def membership_grants(memberships: list) -> tuple[CourseGrant, ...]:
    grants = []
    for item in memberships:
        member = item[0] if isinstance(item, (tuple, list)) else item
        ids = getattr(member, "course_ids", None)
        # Legacy nulls, malformed values and empty assignments grant nothing.
        if not isinstance(ids, list) or not ids:
            continue
        courses = frozenset(x.strip() for x in ids if isinstance(x, str) and x.strip())
        raw_colleges = getattr(member, "colleges", None)
        if raw_colleges is not None and not isinstance(raw_colleges, list):
            continue
        role = normalize_role(getattr(member, "access_role", ""))
        if role in {"junior facilitator", "jr facilitator"} and not raw_colleges:
            continue
        colleges = (
            frozenset(
                x.strip().casefold()
                for x in raw_colleges
                if isinstance(x, str) and x.strip()
            )
            if isinstance(raw_colleges, list)
            else None
        )
        if courses:
            grants.append(CourseGrant(courses, colleges))
    return tuple(grants)


async def grants_for(user: User, session) -> tuple[CourseGrant, ...]:
    memberships = await get_user_active_memberships(user.id, session)
    return membership_grants(memberships)


def student_predicate(
    course_id: str, grants: tuple[CourseGrant, ...], *, global_access: bool
):
    from src.services.sampark_enrollment_sync import freshness_predicate
    enrolled = sa.and_(User.enrolled_course_ids.contains([course_id]), freshness_predicate())
    if global_access:
        return enrolled
    clauses = []
    for grant in grants:
        if course_id not in grant.course_ids:
            continue
        if grant.colleges is None:
            clauses.append(enrolled)
        elif grant.colleges:
            clauses.append(
                sa.and_(
                    enrolled,
                    sa.func.lower(sa.func.trim(User.university)).in_(grant.colleges),
                )
            )
    return sa.or_(*clauses) if clauses else sa.false()


def require_course(course_id: str, grants: tuple[CourseGrant, ...], user: User) -> None:
    if not is_admin_user(user) and not any(course_id in g.course_ids for g in grants):
        raise fastapi.HTTPException(403, "This course is outside your assigned scope.")


async def require_global_legacy_analytics(current_user: User, session=None) -> None:
    """Unadapted aggregations must not bypass an explicit course grant.

    Administrators keep operational access. Staff whose memberships carry no course
    assignment keep the role-based dashboards, scoped by their college limits in the
    handlers. A person with any explicit course grant is limited to /v2/course-workspace,
    because these legacy handlers cannot enforce course boundaries. Without a database
    session the check fails closed to administrators only.
    """
    if is_admin_user(current_user):
        return
    if session is not None:
        grants = await grants_for(current_user, session)
        if not grants:
            return
    raise fastapi.HTTPException(
        403,
        "Use the course-scoped workspace. This legacy endpoint cannot enforce course boundaries.",
    )


# Which page permission each analytics area belongs to. Having one of them, or being a team
# lead, is the same rule the staff menu uses, so hiding a page now also closes its data.
ANALYTICS_AREAS = {
    "dashboard": ("can_access_dashboard",),
    "dropoffs": ("can_access_dashboard",),
    "insights": ("can_access_dashboard",),
    "difficulty": ("can_access_dashboard", "can_access_interviews"),
    "questions": ("can_access_dashboard", "can_access_interviews"),
    "students": ("can_access_students",),
    "student": ("can_access_students",),
    "search": ("can_access_students",),
    "rankings": ("can_access_students",),
    "colleges": ("can_access_colleges", "can_access_students"),
    "interviews": ("can_access_interviews",),
    "interview": ("can_access_interviews",),
    "roles": ("can_access_interviews", "can_create_roles"),
}
ANALYTICS_DEFAULT = ("can_access_dashboard",)


async def require_analytics_area(path: str, user: User, session) -> None:
    """Refuse analytics areas whose page this person does not have."""
    if is_admin_user(user) or session is None:
        return
    policy = getattr(user, "feature_permissions", None)
    if isinstance(policy, dict) and permits(policy, "analytics", "read"):
        return  # an administrator granted reports to this person by name
    memberships = await get_user_active_memberships(user.id, session)
    if not memberships:
        return  # get_current_admin_user has already refused anyone who is not staff
    perms = calculate_user_permissions(user, memberships)
    parts = path.split("/analytics/", 1)
    area = parts[1].split("/", 1)[0] if len(parts) == 2 else ""
    if perms.get("is_team_lead") or any(
        perms.get(flag) for flag in ANALYTICS_AREAS.get(area, ANALYTICS_DEFAULT)
    ):
        return
    raise fastapi.HTTPException(403, "This report is not part of your role.")


async def legacy_analytics_guard(
    request: fastapi.Request,
    current_user: User = fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
) -> None:
    await require_global_legacy_analytics(current_user, session)
    await require_analytics_area(request.url.path, current_user, session)
