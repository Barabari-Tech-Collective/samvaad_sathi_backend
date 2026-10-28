"""Authorization gate for cross-student (analytics/dashboard) endpoints.

Kept separate from api/dependencies/auth.py, which answers "who is this?".
This module answers "are they allowed to read other people's data?" - a
distinction the analytics endpoints previously did not make at all: they
depended on get_current_user purely to require *a* login, then discarded
it (`del current_user`) and queried across every user in the system. Any
logged-in student could therefore enumerate every other student's name,
college, scores and activity.

Access is granted two ways, and either is sufficient:

  1. user.is_admin in the database - the intended long-term mechanism.
  2. the user's email appearing in the ADMIN_EMAILS setting.

(2) exists because (1) alone is unreachable in some environments: the
column defaults to false for every pre-existing row, so granting it
requires an UPDATE, and not everyone who can deploy this service can also
run SQL against its database (Render's free tier has no shell, and DB
console access is not always shared with whoever owns the deploy). An
env-var allow-list is settable by anyone who can configure the service,
which keeps the dashboard operable without weakening the gate for
students - they appear in neither list.
"""

import fastapi

from src.api.dependencies.auth import get_current_user
from src.config.manager import settings
from src.models.db.user import User


def _allow_listed_emails() -> set[str]:
    """Parse ADMIN_EMAILS into a normalised set. Comma-separated; blanks and
    surrounding whitespace ignored so a trailing comma isn't a footgun."""
    raw = getattr(settings, "ADMIN_EMAILS", "") or ""
    return {entry.strip().lower() for entry in raw.split(",") if entry.strip()}


def _super_admin_allow_listed_emails() -> set[str]:
    """Parse SUPER_ADMIN_EMAILS into a normalised set. Comma-separated; blanks and
    surrounding whitespace ignored."""
    raw = getattr(settings, "SUPER_ADMIN_EMAILS", "") or ""
    return {entry.strip().lower() for entry in raw.split(",") if entry.strip()}


def normalize_role(role: str | None) -> str:
    """Normalize a role string by trimming, lowercasing, and replacing underscores with spaces."""
    if not role:
        return ""
    return str(role).strip().replace("_", " ").lower()


def is_super_admin_user(user: User) -> bool:
    """True if user has full system access, including team creation and deletion.

    Authority hierarchy:
      1. Explicit SUPER_ADMIN_EMAILS env allowlist (fail-safe / break-glass).
      2. Persistent database flag: user.is_super_admin == True.
      3. Negative guard: If the user is explicitly a standard admin (user.is_admin is True
         or listed in ADMIN_EMAILS), they MUST NOT be elevated to Super Admin via token claims.
      4. Fallback for non-persisted / SSO mock identities: only allow token claim if not
         already designated as a standard admin.
    """
    email = (getattr(user, "email", "") or "").strip().lower()

    # 1. Explicit super admin allowlist (break-glass / env override)
    if bool(email) and email in _super_admin_allow_listed_emails():
        return True

    # 2. Database persistent flag is authoritative for Super Admins
    if getattr(user, "is_super_admin", False):
        return True

    # 3. Block token elevation for confirmed standard admins:
    # If the user is explicitly a regular admin in DB or in ADMIN_EMAILS,
    # incoming token claims must NEVER elevate them to Super Admin.
    is_standard_admin = getattr(user, "is_admin", False) or (
        bool(email) and email in _allow_listed_emails()
    )
    if is_standard_admin:
        return False

    # 4. Token-derived attribute or claim (only for identities without DB records / test mocks)
    if getattr(user, "_token_is_super_admin", False):
        return True

    token_role = normalize_role(
        getattr(user, "_token_role", "") or getattr(user, "role", "")
    )
    return token_role in {"super admin", "super_admin"}


def is_admin_user(user: User) -> bool:
    """True if user may read cross-student data. Super Admins inherit this privilege."""
    if (
        is_super_admin_user(user)
        or getattr(user, "is_admin", False)
        or getattr(user, "_token_is_admin", False)
    ):
        return True
    email = (getattr(user, "email", "") or "").strip().lower()
    if bool(email) and email in _allow_listed_emails():
        return True
    token_role = normalize_role(
        getattr(user, "_token_role", "") or getattr(user, "role", "")
    )
    return token_role in {"admin", "super admin", "super_admin"}


from src.api.dependencies.session import get_async_session

STAFF_ROLES = {
    "admin",
    "super admin",
    "lead",
    "team lead",
    "manager",
    "creator",
    "facilitator",
    "staff",
    "sr facilitator",
    "senior facilitator",
    "jr facilitator",
    "junior facilitator",
    "program manager",
    "placement com",
    "recruiter",
    "placement staff",
    "team member",
    "member",
}


CREATOR_ROLES = {
    "lead",
    "team lead",
    "creator",
    "admin",
    "super admin",
    "sr facilitator",
    "senior facilitator",
}
JUNIOR_ROLES = {"jr facilitator", "junior facilitator"}
TEAM_MEMBER_ROLES = {"team member", "member", "staff"}
UNRESTRICTED_COLLEGE_ROLES = CREATOR_ROLES | TEAM_MEMBER_ROLES
LEAD_ROLES = {"lead", "team lead", "manager", "admin", "super admin", "creator"}


def extract_memberships_from_result(res) -> list:
    """Extract list of membership records or (member, team) tuples from SQLAlchemy Result or Mock.

    Prioritizes res.all() to preserve (TeamMember, Team) tuples without triggering
    unawaited lazy loads on member.team. If res.all() returns a MagicMock (in unconfigured mocks),
    it safely falls back to res.scalars().all().
    """
    if res is None:
        return []
    try:
        from sqlalchemy.engine.row import Row
    except ImportError:
        Row = ()
    try:
        if hasattr(res, "all") and callable(res.all):
            a_res = res.all()
            if isinstance(a_res, list):
                return [
                    (
                        tuple(item)
                        if (
                            isinstance(item, Row)
                            or (
                                not isinstance(item, (tuple, list))
                                and hasattr(item, "__getitem__")
                                and not hasattr(item, "access_role")
                            )
                        )
                        else item
                    )
                    for item in a_res
                ]
    except Exception:
        pass
    try:
        if hasattr(res, "scalars") and callable(res.scalars):
            s_obj = res.scalars()
            if hasattr(s_obj, "all") and callable(s_obj.all):
                s_res = s_obj.all()
                if isinstance(s_res, list):
                    return s_res
    except Exception:
        pass
    return []


def calculate_user_permissions(
    user: User,
    memberships: list,
) -> dict:
    """Calculate explicit role-based permissions for a user across teams.

    Enforces granular page-level and action-level permissions based on team type,
    access role (Creator, Lead, Facilitator, Team Member), and purpose tags:
    - Tag 'Create interview': Interview Creation/Roles page, interview analytics, practice interview mode.
    - Tag 'Check batch progress': Dashboard page (batch metrics), Students page, and Interview page.
    - Tag 'Check student progress': Students page only (view & edit candidate details); Colleges page remains hidden.
    - Tag 'Check college progress': Colleges page only (cross-institution analytics).
    - ATS feature: Dedicated tag/role grant.
    """
    if is_admin_user(user):
        return {
            "can_create_roles": True,
            "can_access_ats": True,
            "can_edit": True,
            "is_view_only": False,
            "colleges": None,
            "is_team_lead": True,
            "can_access_dashboard": True,
            "can_access_students": True,
            "can_access_colleges": True,
            "can_access_interviews": True,
            "can_access_practice_interview": True,
        }

    is_placement_lead = False
    is_program_lead = False
    is_placement_creator = False
    is_program_creator = False
    is_sr_facilitator = False
    is_junior_facilitator = False
    is_team_member = False
    junior_colleges: list[str] = []
    sr_colleges: list[str] = []

    all_purposes: set[str] = set()
    has_explicit_purposes = False

    for item in memberships:
        if isinstance(item, (tuple, list)) or hasattr(item, "__getitem__"):
            try:
                member = item[0]
                team = item[1] if len(item) > 1 else None
            except Exception:
                member = item
                team = getattr(member, "team", None)
        else:
            member = item
            team = getattr(member, "team", None)

        raw_team_name = getattr(team, "name", "")
        t_name = raw_team_name.strip().lower() if isinstance(raw_team_name, str) else ""
        m_role = normalize_role(getattr(member, "access_role", ""))
        m_purposes = getattr(member, "purposes", None)

        if m_purposes and isinstance(m_purposes, list):
            for p in m_purposes:
                if p and isinstance(p, str):
                    cleaned = p.strip().lower().replace("_", " ")
                    if cleaned:
                        all_purposes.add(cleaned)
                        has_explicit_purposes = True

        is_placement = any(kw in t_name for kw in ["placement", "partnership"])

        if m_role in {"lead", "team lead", "manager"}:
            if is_placement:
                is_placement_lead = True
            else:
                is_program_lead = True
        elif m_role in {"sr facilitator", "senior facilitator"}:
            is_sr_facilitator = True
            if (
                member
                and getattr(member, "colleges", None)
                and isinstance(member.colleges, list)
            ):
                sr_colleges.extend(
                    [c for c in member.colleges if isinstance(c, str) and c.strip()]
                )
        elif m_role in JUNIOR_ROLES:
            is_junior_facilitator = True
            if (
                member
                and getattr(member, "colleges", None)
                and isinstance(member.colleges, list)
            ):
                junior_colleges.extend(
                    [c for c in member.colleges if isinstance(c, str) and c.strip()]
                )
        elif m_role in {"creator"}:
            if is_placement:
                is_placement_creator = True
            else:
                is_program_creator = True
        elif m_role in TEAM_MEMBER_ROLES:
            is_team_member = True

    # Fallback to token claims / user.role
    token_role = normalize_role(
        getattr(user, "_token_role", "") or getattr(user, "role", "")
    )
    can_access_ats_token = token_role in _BULK_ATS_ROLES

    if not memberships:
        if token_role in {"lead", "team lead", "manager"}:
            is_program_lead = True
        elif token_role in {"sr facilitator", "senior facilitator"}:
            is_sr_facilitator = True
        elif token_role in JUNIOR_ROLES:
            is_junior_facilitator = True
        elif token_role in {"creator"}:
            is_program_creator = True
        elif token_role in TEAM_MEMBER_ROLES:
            is_team_member = True

    # Check purpose tags
    has_create_interview = any("create interview" in p or "interview" in p for p in all_purposes)
    has_batch_progress = any("batch progress" in p or "batch" in p for p in all_purposes)
    has_student_progress = any("student progress" in p or "student" in p for p in all_purposes)
    has_college_progress = any("college progress" in p or "college" in p for p in all_purposes)
    has_ats_purpose = any("ats" in p for p in all_purposes)

    # 1. Edit & View-only capabilities:
    # Any Lead, Senior Facilitator, or Creator possesses edit capabilities.
    can_edit = bool(
        is_placement_lead
        or is_program_lead
        or is_sr_facilitator
        or is_placement_creator
        or is_program_creator
    )
    is_view_only = not can_edit

    # 2. Page access & Role creation:
    # Role Creation: Leads, Senior Facilitators, and Creators (by default or with interview/batch tags)
    can_create_roles = bool(
        is_placement_lead
        or is_program_lead
        or is_sr_facilitator
        or (is_placement_creator and (not has_explicit_purposes or has_create_interview or has_batch_progress))
        or (is_program_creator and (not has_explicit_purposes or has_create_interview or has_batch_progress))
    )

    # Bulk ATS: Placement Leads, legacy/unrestricted Placement Creators, or users with explicit ATS tag/token claim
    can_access_ats = bool(
        is_placement_lead
        or (is_placement_creator and (not has_explicit_purposes or has_ats_purpose or can_access_ats_token))
        or has_ats_purpose
        or can_access_ats_token
    )

    # Interview Portal: All Leads, Senior/Junior Facilitators, Creators, or Team Members with interview/batch tag
    can_access_interviews = bool(
        is_placement_lead
        or is_program_lead
        or is_sr_facilitator
        or is_junior_facilitator
        or (is_placement_creator and (not has_explicit_purposes or has_create_interview or has_batch_progress))
        or (is_program_creator and (not has_explicit_purposes or has_create_interview or has_batch_progress))
        or (is_team_member and (has_create_interview or has_batch_progress))
    )

    # Practice Interview: Leads, Senior Facilitators, and Creators (by default or with interview/batch tag)
    can_access_practice_interview = bool(
        is_placement_lead
        or is_program_lead
        or is_sr_facilitator
        or (is_placement_creator and (not has_explicit_purposes or has_create_interview or has_batch_progress))
        or (is_program_creator and (not has_explicit_purposes or has_create_interview or has_batch_progress))
    )

    # Dashboard: Leads, Facilitators, or members/creators with batch progress tag (or legacy placement creators)
    can_access_dashboard = bool(
        is_placement_lead
        or is_program_lead
        or is_sr_facilitator
        or is_junior_facilitator
        or (is_placement_creator and (not has_explicit_purposes or has_batch_progress))
        or (is_program_creator and has_batch_progress)
        or (is_team_member and has_batch_progress)
    )

    # Students: Leads, Facilitators, or members/creators with batch/student progress tag (or legacy placement creators)
    can_access_students = bool(
        is_placement_lead
        or is_program_lead
        or is_sr_facilitator
        or is_junior_facilitator
        or (is_placement_creator and (not has_explicit_purposes or has_batch_progress or has_student_progress))
        or (is_program_creator and (has_batch_progress or has_student_progress))
        or (is_team_member and (has_batch_progress or has_student_progress))
    )

    # Colleges: Leads, Facilitators, or members/creators with college progress tag (or legacy placement creators)
    can_access_colleges = bool(
        is_placement_lead
        or is_program_lead
        or is_sr_facilitator
        or is_junior_facilitator
        or (is_placement_creator and (not has_explicit_purposes or has_college_progress))
        or (is_program_creator and has_college_progress)
        or (is_team_member and has_college_progress)
    )

    # 3. College Scoping:
    # Leads and Creators have unrestricted cross-college access (None).
    # Senior Facilitators are restricted to assigned colleges if provided; otherwise unrestricted (None).
    # Junior Facilitators are strictly restricted to assigned colleges.
    if is_placement_lead or is_program_lead or is_placement_creator or is_program_creator:
        colleges = None
    elif is_sr_facilitator:
        colleges = sr_colleges if sr_colleges else None
    elif is_junior_facilitator:
        colleges = junior_colleges if junior_colleges else []
    else:
        colleges = None

    is_team_lead = bool(
        is_placement_lead
        or is_program_lead
        or token_role in LEAD_ROLES
        or any(
            normalize_role(
                getattr(
                    (
                        item[0]
                        if isinstance(item, (tuple, list))
                        or hasattr(item, "__getitem__")
                        else item
                    ),
                    "access_role",
                    "",
                )
            )
            in LEAD_ROLES
            for item in memberships
        )
    )

    return {
        "can_create_roles": can_create_roles,
        "can_access_ats": can_access_ats,
        "can_edit": can_edit,
        "is_view_only": is_view_only,
        "colleges": colleges,
        "is_team_lead": is_team_lead,
        "can_access_dashboard": can_access_dashboard,
        "can_access_students": can_access_students,
        "can_access_colleges": can_access_colleges,
        "can_access_interviews": can_access_interviews,
        "can_access_practice_interview": can_access_practice_interview,
    }


async def get_user_active_memberships(
    user_id: int,
    session,
) -> list:
    """Fetch active (TeamMember, Team) tuples for a user across active teams,
    deterministically ordered by TeamMember.created_at asc, TeamMember.id asc.
    """
    if session is None or not user_id:
        return []
    from sqlalchemy import select
    from src.models.db.team import Team
    from src.models.db.team_member import TeamMember

    try:
        stmt = (
            select(TeamMember, Team)
            .join(Team, TeamMember.team_id == Team.id)
            .where(
                TeamMember.user_id == user_id,
                TeamMember.status == "active",
                Team.status == "active",
            )
            .order_by(TeamMember.created_at.asc(), TeamMember.id.asc())
        )
        res = await session.execute(stmt)
        return extract_memberships_from_result(res)
    except Exception:
        return []


async def get_current_admin_user(
    current_user: User = fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
) -> User:
    """Require an authenticated user authorised for cross-student data.
    Allows:
    1. Global Admins and Super Admins.
    2. Active staff / team members verified from the database.
    """
    if is_admin_user(current_user):
        return current_user

    if session is not None:
        memberships = await get_user_active_memberships(current_user.id, session)
        if memberships:
            return current_user
        # When DB session exists but user has no active memberships, fail closed
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )

    # Fallback only when session is None (e.g. lightweight tests)
    role_from_token = normalize_role(
        getattr(current_user, "_token_role", "") or getattr(current_user, "role", "")
    )
    if role_from_token in STAFF_ROLES:
        return current_user

    raise fastapi.HTTPException(
        status_code=fastapi.status.HTTP_403_FORBIDDEN,
        detail="Administrator access required.",
    )


async def get_current_creator_user(
    current_user: User = fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
) -> User:
    """Require user to have Creator Portal permissions.
    - Super Admins, Global Admins, Leads, Creators, and Sr Facilitators have full access.
    - Jr Facilitators and standard Team Members are rejected with 403 Forbidden.
    """
    if is_admin_user(current_user):
        return current_user

    if session is not None:
        memberships = await get_user_active_memberships(current_user.id, session)
        if memberships:
            perms = calculate_user_permissions(current_user, memberships)
            if perms["can_create_roles"]:
                return current_user
            roles = {
                normalize_role(
                    getattr(
                        (
                            item[0]
                            if (
                                isinstance(item, (tuple, list))
                                or hasattr(item, "__getitem__")
                            )
                            else item
                        ),
                        "access_role",
                        "",
                    )
                )
                for item in memberships
            }
            if roles & JUNIOR_ROLES:
                raise fastapi.HTTPException(
                    status_code=fastapi.status.HTTP_403_FORBIDDEN,
                    detail="Junior Facilitators do not have permission to access the Creator Portal or modify interview roles.",
                )
            if roles & TEAM_MEMBER_ROLES:
                raise fastapi.HTTPException(
                    status_code=fastapi.status.HTTP_403_FORBIDDEN,
                    detail="Team members have view-only access and do not have permission to access the Creator Portal or modify interview roles.",
                )
        else:
            # Check if this user had team memberships that are no longer active (revoked / deleted)
            from sqlalchemy import select
            from src.models.db.team_member import TeamMember

            try:
                stmt_any = (
                    select(TeamMember.id)
                    .where(TeamMember.user_id == current_user.id)
                    .limit(1)
                )
                res_any = await session.execute(stmt_any)
                has_records = False
                if hasattr(res_any, "scalars") and callable(res_any.scalars):
                    s_res = res_any.scalars().all()
                    if isinstance(s_res, list) and s_res:
                        has_records = True
                elif hasattr(res_any, "all") and callable(res_any.all):
                    a_res = res_any.all()
                    if isinstance(a_res, list) and a_res:
                        has_records = True
                if has_records:
                    raise fastapi.HTTPException(
                        status_code=fastapi.status.HTTP_403_FORBIDDEN,
                        detail="Creator Portal access required. Your team membership is no longer active.",
                    )
            except fastapi.HTTPException:
                raise
            except Exception:
                pass

    # Fallback to token claims (for SSO users without team records)
    role_from_token = normalize_role(
        getattr(current_user, "_token_role", "") or getattr(current_user, "role", "")
    )
    if role_from_token in CREATOR_ROLES:
        return current_user
    if role_from_token in JUNIOR_ROLES:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Junior Facilitators do not have permission to access the Creator Portal or modify interview roles.",
        )
    if role_from_token in TEAM_MEMBER_ROLES:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Team members have view-only access and do not have permission to access the Creator Portal or modify interview roles.",
        )

    raise fastapi.HTTPException(
        status_code=fastapi.status.HTTP_403_FORBIDDEN,
        detail="Creator Portal access required. Only Senior Facilitators, Creators, and Administrators can create or modify interview roles.",
    )


async def get_current_interview_viewer_user(
    current_user: User = fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
) -> User:
    """Require user to have interview portal view permissions.
    - Super Admins, Global Admins, Leads, Creators, and Facilitators (both Senior and Junior) have view access.
    - Standard Team Members without interview permissions are rejected with 403 Forbidden.
    """
    if is_admin_user(current_user):
        return current_user

    if session is not None:
        memberships = await get_user_active_memberships(current_user.id, session)
        if memberships:
            perms = calculate_user_permissions(current_user, memberships)
            if perms.get("can_access_interviews", False):
                return current_user

    role_from_token = normalize_role(
        getattr(current_user, "_token_role", "") or getattr(current_user, "role", "")
    )
    if role_from_token in CREATOR_ROLES or role_from_token in JUNIOR_ROLES:
        return current_user

    raise fastapi.HTTPException(
        status_code=fastapi.status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to view the interview portal.",
    )


async def get_user_allowed_colleges(
    user: User,
    session,
) -> set[str] | None:
    """Return set of allowed college names, or None if unrestricted.
    - Super Admins and Global Admins return None (access to all colleges).
    - Placement Creators, Program Creators, and Senior Facilitators return None (unrestricted).
    - Team Members return None (view access across all colleges without college filtering).
    - Junior Facilitators return set of assigned colleges from active team memberships.
    """
    if is_admin_user(user):
        return None

    if session is None:
        return set()

    memberships = await get_user_active_memberships(user.id, session)
    if not memberships:
        token_role = normalize_role(
            getattr(user, "_token_role", "") or getattr(user, "role", "")
        )
        if token_role in UNRESTRICTED_COLLEGE_ROLES:
            return None
        return set()

    perms = calculate_user_permissions(user, memberships)
    if perms["colleges"] is None:
        return None
    return set(perms["colleges"])


async def verify_college_access(
    user: User,
    college_name: str,
    session,
) -> None:
    """Verify if user has permission to access the specified college.
    Raises 403 Forbidden if user is restricted to assigned colleges and college is not in that list.
    """
    allowed = await get_user_allowed_colleges(user, session)
    if allowed is None:
        return  # Unrestricted admin access

    clean_req = (college_name or "").strip().lower()
    allowed_lower = {c.lower() for c in allowed}
    if clean_req not in allowed_lower:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail=f"Access denied. You do not have permission to access data for '{college_name}'.",
        )


_BULK_ATS_ROLES = {
    "recruiter",
    "placement staff",
    "placement_staff",
    "admin",
    "super admin",
    "super_admin",
}


def is_bulk_ats_user(user: User) -> bool:
    """True if `user` may use Bulk ATS (screening assistance, not students).

    Deliberately narrower than is_admin_user: a recruiter should not need
    cross-student analytics access to screen resumes, so this also accepts
    user.role in _BULK_ATS_ROLES on its own, in addition to the existing
    is_admin/ADMIN_EMAILS grant.
    """
    if is_admin_user(user):
        return True
    role = normalize_role(getattr(user, "_token_role", "") or getattr(user, "role", ""))
    return role in _BULK_ATS_ROLES


async def get_current_bulk_ats_user(
    current_user: User = fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
) -> User:
    """Require an authenticated recruiter/placement_staff/admin user or
    a Creator/Lead in the Placement or Partnerships team.

    - Super Admins, Global Admins, and users with recruiter/placement_staff roles have full access.
    - Creators / Leads in the Placement / Partnerships team have full access to Bulk ATS.
    - Regular Team Members (in Placement or Program team) are rejected with 403 Forbidden.
    - Students are rejected with 403 Forbidden.
    """
    if is_bulk_ats_user(current_user):
        return current_user

    if session is not None:
        memberships = await get_user_active_memberships(current_user.id, session)
        if memberships:
            perms = calculate_user_permissions(current_user, memberships)
            if perms["can_access_ats"]:
                return current_user

    raise fastapi.HTTPException(
        status_code=fastapi.status.HTTP_403_FORBIDDEN,
        detail="Bulk ATS access is restricted to Placement/Partnership team creators, recruiters, and administrators. Regular team members do not have access.",
    )


async def get_current_super_admin_user(
    current_user: User = fastapi.Depends(get_current_user),
) -> User:
    """Require an authenticated user with Super Admin privileges."""
    if not is_super_admin_user(current_user):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Super Administrator access required.",
        )
    return current_user


async def check_team_permission(
    user: User,
    team_id: int,
    session,
    require_lead: bool = False,
) -> bool:
    """Verify team-scoped permissions.
    - Super Admins and Global Admins always have access.
    - For team members: if require_lead=True, their access_role must be in LEAD_ROLES.
    """
    if is_admin_user(user):
        return True

    from sqlalchemy import select
    from src.models.db.team_member import TeamMember

    stmt = select(TeamMember).where(
        TeamMember.team_id == team_id,
        TeamMember.user_id == user.id,
        TeamMember.status == "active",
    )
    result = await session.execute(stmt)
    member = result.scalar_one_or_none()
    if not member:
        return False

    if require_lead:
        role = normalize_role(member.access_role)
        return role in LEAD_ROLES

    return True


async def is_team_authorized_user(user: User, team_id: int, session) -> bool:
    """Backward-compatible alias for checking general active membership in a team."""
    return await check_team_permission(user, team_id, session, require_lead=False)
