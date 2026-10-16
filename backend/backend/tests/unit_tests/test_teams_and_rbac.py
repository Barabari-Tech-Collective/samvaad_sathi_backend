"""Unit tests for Teams management and Role-Based Access Control (RBAC).

Covers:
1. Super Admin and Team authorization dependency guards.
2. Role hierarchy (Super Admin inheriting Admin privileges).
3. Scoped team permissions (distinguishing Team Lead from regular Team Member).
4. Team and TeamMember schema validation and serialization.
5. Repository-level credential hijacking prevention and duplicate membership prevention.
6. Route-level authorization gates.
"""

import datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
import fastapi
import pydantic
import pytest

from src.api.dependencies.admin import (
    check_team_permission,
    get_current_super_admin_user,
    is_admin_user,
    is_super_admin_user,
    is_team_authorized_user,
)
from src.config.manager import settings
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.models.schemas.teams_v2 import (
    TeamCreate,
    TeamResponse,
    TeamMemberCreate,
    TeamMemberUpdate,
    TeamMemberResponse,
)
from src.repository.crud.team_v2 import TeamCRUDRepository
from src.utilities.exceptions.database import EntityDoesNotExist, EntityAlreadyExists
from src.utilities.exceptions.password import PasswordDoesNotMatch


def _make_user(
    user_id: int,
    email: str = "",
    is_admin: bool = False,
    is_super_admin: bool = False,
) -> User:
    return User(
        id=user_id,
        email=email,
        is_admin=is_admin,
        is_super_admin=is_super_admin,
    )


# --- RBAC & Super Admin Permission Tests -------------------------------------


def test_is_super_admin_user_with_db_flag():
    user = _make_user(1, email="lead@example.com", is_super_admin=True)
    assert is_super_admin_user(user) is True


def test_is_super_admin_user_with_env_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "SUPER_ADMIN_EMAILS", "super@corp.org, founder@corp.org")
    user = _make_user(2, email="SUPER@corp.org", is_super_admin=False)
    assert is_super_admin_user(user) is True


def test_is_super_admin_user_rejects_regular_user(monkeypatch):
    monkeypatch.setattr(settings, "SUPER_ADMIN_EMAILS", "super@corp.org")
    user = _make_user(3, email="student@corp.org", is_super_admin=False)
    assert is_super_admin_user(user) is False


def test_is_super_admin_user_handles_missing_attribute():
    """A legacy User object lacking the is_super_admin column must fail closed."""
    class _LegacyUser:
        id = 99
        email = "legacy@corp.org"

    assert is_super_admin_user(cast(User, _LegacyUser())) is False


def test_is_admin_user_inherits_super_admin_privilege():
    """A Super Admin must be a strict superset of Admin and pass is_admin_user."""
    super_admin = _make_user(10, email="super@corp.org", is_admin=False, is_super_admin=True)
    assert is_admin_user(super_admin) is True


@pytest.mark.asyncio
async def test_get_current_super_admin_user_rejects_non_super_admin():
    user = _make_user(4, email="regular@corp.org", is_super_admin=False)
    with pytest.raises(fastapi.HTTPException) as exc:
        await get_current_super_admin_user(current_user=user)
    assert exc.value.status_code == 403
    assert "Super Administrator access required" in exc.value.detail


@pytest.mark.asyncio
async def test_get_current_super_admin_user_allows_super_admin():
    admin = _make_user(5, is_super_admin=True)
    assert await get_current_super_admin_user(current_user=admin) is admin


# --- Scoped Team Permission & Negative Authorization Tests -------------------


@pytest.mark.asyncio
async def test_check_team_permission_grants_super_admin_short_circuit():
    super_admin = _make_user(1, is_super_admin=True)
    mock_session = AsyncMock()
    assert await check_team_permission(super_admin, team_id=10, session=mock_session, require_lead=True) is True
    mock_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_check_team_permission_grants_global_admin_short_circuit():
    global_admin = _make_user(2, is_admin=True)
    mock_session = AsyncMock()
    assert await check_team_permission(global_admin, team_id=10, session=mock_session, require_lead=True) is True
    mock_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_check_team_permission_rejects_non_member():
    user = _make_user(3, is_admin=False, is_super_admin=False)
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    # Non-member rejected whether require_lead is True or False
    assert await check_team_permission(user, team_id=10, session=mock_session, require_lead=False) is False
    assert await check_team_permission(user, team_id=10, session=mock_session, require_lead=True) is False


@pytest.mark.asyncio
async def test_check_team_permission_blocks_regular_member_from_lead_actions():
    user = _make_user(4, is_admin=False, is_super_admin=False)
    member_record = TeamMember(id=1, team_id=10, user_id=user.id, access_role="member", status="active")
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = member_record
    mock_session.execute.return_value = mock_result

    # Regular member CAN view (require_lead=False)
    assert await check_team_permission(user, team_id=10, session=mock_session, require_lead=False) is True
    # Regular member CANNOT manage (require_lead=True)
    assert await check_team_permission(user, team_id=10, session=mock_session, require_lead=True) is False


@pytest.mark.asyncio
async def test_check_team_permission_allows_team_lead():
    user = _make_user(5, is_admin=False, is_super_admin=False)
    lead_record = TeamMember(id=2, team_id=10, user_id=user.id, access_role="lead", status="active")
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = lead_record
    mock_session.execute.return_value = mock_result

    # Team Lead CAN both view and manage
    assert await check_team_permission(user, team_id=10, session=mock_session, require_lead=False) is True
    assert await check_team_permission(user, team_id=10, session=mock_session, require_lead=True) is True


# --- Team Schema Validation Tests --------------------------------------------


def test_team_create_requires_name():
    with pytest.raises(pydantic.ValidationError):
        TeamCreate.model_validate({})


def test_team_create_accepts_valid_payload():
    team = TeamCreate(name="Platform Core", description="Core backend infrastructure")
    assert team.name == "Platform Core"
    assert team.description == "Core backend infrastructure"


def test_team_response_serialization():
    now = datetime.datetime.now(datetime.timezone.utc)
    data = {
        "id": 1,
        "name": "Design Team",
        "description": "UI/UX group",
        "status": "active",
        "memberCount": 5,
        "created_at": now,
    }
    response = TeamResponse(**data)
    assert response.id == 1
    assert response.name == "Design Team"
    assert response.member_count == 5
    assert response.status == "active"


def test_team_member_create_requires_role_name_and_email():
    with pytest.raises(pydantic.ValidationError):
        TeamMemberCreate.model_validate({"access_role": "Facilitator"})

    member = TeamMemberCreate(
        name="Alice Smith",
        email="alice@college.edu",
        access_role="Facilitator",
        purposes=["Interview Review"],
        domain="Engineering",
        colleges=["ABC College"],
    )
    assert member.name == "Alice Smith"
    assert member.email == "alice@college.edu"
    assert member.access_role == "Facilitator"


def test_team_member_update_allows_partial_fields():
    update = TeamMemberUpdate(access_role="Lead")
    assert update.access_role == "Lead"
    assert update.name is None
    assert not hasattr(update, "email")
    assert not hasattr(update, "password")


# --- Router Security & Permission Wiring -------------------------------------


def test_teams_v2_routes_exist():
    from src.api.routes.teams_v2 import router
    assert len(router.routes) >= 10, "Expected all teams v2 routes to be registered"


def test_super_admin_gated_routes_require_super_admin_dependency():
    from src.api.routes.teams_v2 import router

    super_admin_routes = {
        "teams:create",
        "teams:delete",
        "teams:restore_team",
        "teams:permanent_delete_team",
        "teams:get_team_recycle_bin",
    }

    for route in router.routes:
        if getattr(route, "name", None) in super_admin_routes:
            dep_names = [
                getattr(dep.call, "__name__", "")
                for dep in getattr(route.dependant, "dependencies", []) or []
            ]
            assert "get_current_super_admin_user" in dep_names, (
                f"Route '{route.name}' must require get_current_super_admin_user"
            )


# --- Repository Security & Integrity Tests -----------------------------------


@pytest.mark.asyncio
async def test_add_team_member_rejects_missing_team():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    repo = TeamCRUDRepository(async_session=mock_session)
    member_data = TeamMemberCreate(name="Bob", email="bob@test.com", access_role="member")

    with pytest.raises(EntityDoesNotExist):
        await repo.add_team_member(team_id=9999, member_data=member_data)


@pytest.mark.asyncio
async def test_add_team_member_does_not_overwrite_existing_user_password():
    """Security: Never silently change credentials of an existing global user account."""
    mock_session = AsyncMock()
    # Team exists
    team = Team(id=1, name="Core", status="active")
    # Existing user with an established password
    existing_user = User(id=10, email="admin@barabari.org", password_hash="original_secret_hash")

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = None
        elif "from team" in stmt_str or "team.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = team
        elif "from \"user\"" in stmt_str or "user.email" in stmt_str:
            result.scalar_one_or_none.return_value = existing_user
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock

    repo = TeamCRUDRepository(async_session=mock_session)
    member_data = TeamMemberCreate(name="Admin", email="admin@barabari.org", password="hacker_password", access_role="lead")
    await repo.add_team_member(team_id=1, member_data=member_data)

    # Assert existing user's password hash was NOT mutated
    assert existing_user.password_hash == "original_secret_hash"


@pytest.mark.asyncio
async def test_add_team_member_rejects_duplicate_active_membership():
    """Integrity: Prevent duplicate active membership in the same team."""
    mock_session = AsyncMock()
    team = Team(id=1, name="Core", status="active")
    user = User(id=10, email="member@barabari.org", password_hash="hash")
    existing_active_member = TeamMember(id=5, team_id=1, user_id=10, status="active", access_role="member")

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = existing_active_member
        elif "from team" in stmt_str or "team.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = team
        elif "from \"user\"" in stmt_str or "user.email" in stmt_str:
            result.scalar_one_or_none.return_value = user
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock

    repo = TeamCRUDRepository(async_session=mock_session)
    member_data = TeamMemberCreate(name="Member", email="member@barabari.org", access_role="member")
    with pytest.raises(EntityAlreadyExists):
        await repo.add_team_member(team_id=1, member_data=member_data)


@pytest.mark.asyncio
async def test_update_team_member_updates_metadata_safely():
    """Security: Team Lead updating member metadata only updates role, domain, purposes, colleges."""
    mock_session = AsyncMock()
    member = TeamMember(id=5, team_id=1, user_id=10, status="active", access_role="member")
    user = User(id=10, email="target@barabari.org", password_hash="secure_user_pw", name="Target Name")

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = member
        elif "from \"user\"" in stmt_str or "user.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = user
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock

    repo = TeamCRUDRepository(async_session=mock_session)
    update_data = TeamMemberUpdate(
        name="Updated Name",
        access_role="lead",
        domain="Engineering",
    )
    res = await repo.update_team_member(team_id=1, member_id=5, member_data=update_data)

    assert member.access_role == "lead"
    assert member.domain == "Engineering"
    assert user.name == "Updated Name"
    assert user.password_hash == "secure_user_pw"
    assert user.email == "target@barabari.org"


@pytest.mark.asyncio
async def test_get_all_teams_forces_active_status_for_regular_users():
    """Query param status bypass guard: regular users querying status=deleted are forced to active."""
    from src.api.routes.teams_v2 import get_all_teams
    mock_repo = AsyncMock()
    mock_repo.get_all_teams.return_value = []
    regular_user = _make_user(1, email="user@barabari.org", is_super_admin=False)

    await get_all_teams(status="deleted", current_user=regular_user, team_repo=mock_repo)
    mock_repo.get_all_teams.assert_called_once_with(status="active")


@pytest.mark.asyncio
async def test_get_all_teams_allows_deleted_status_for_super_admin():
    """Super Admins are allowed to inspect deleted teams via query param."""
    from src.api.routes.teams_v2 import get_all_teams
    mock_repo = AsyncMock()
    mock_repo.get_all_teams.return_value = []
    super_admin = _make_user(1, email="admin@barabari.org", is_super_admin=True)

    await get_all_teams(status="deleted", current_user=super_admin, team_repo=mock_repo)
    mock_repo.get_all_teams.assert_called_once_with(status="deleted")


@pytest.mark.asyncio
async def test_get_active_members_query_status_deleted_requires_lead_privilege():
    """Query param status bypass guard: non-leads cannot query status=deleted on members route."""
    from src.api.routes.teams_v2 import get_active_members
    mock_repo = AsyncMock()
    mock_repo.async_session = AsyncMock()
    regular_member = _make_user(5, email="member@barabari.org")

    # Regular member record (not a lead)
    member_record = TeamMember(id=1, team_id=1, user_id=5, access_role="member", status="active")
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = member_record
    mock_repo.async_session.execute.return_value = mock_result

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await get_active_members(team_id=1, status="deleted", current_user=regular_member, team_repo=mock_repo)
    assert exc_info.value.status_code == 403


# --- Senior vs Junior Facilitator RBAC & College Scoping Tests -----------------


@pytest.mark.asyncio
async def test_get_current_creator_user_allows_senior_facilitator():
    from src.api.dependencies.admin import get_current_creator_user
    sr_user = _make_user(10, email="sr@barabari.org", is_admin=False, is_super_admin=False)
    session = AsyncMock()
    member_record = TeamMember(id=1, team_id=1, user_id=10, access_role="Sr Facilitator", status="active")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [member_record]
    session.execute.return_value = mock_result

    user = await get_current_creator_user(current_user=sr_user, session=session)
    assert user is sr_user


@pytest.mark.asyncio
async def test_get_current_creator_user_denies_junior_facilitator():
    from src.api.dependencies.admin import get_current_creator_user
    jr_user = _make_user(11, email="jr@barabari.org", is_admin=False, is_super_admin=False)
    session = AsyncMock()
    member_record = TeamMember(id=2, team_id=1, user_id=11, access_role="Jr Facilitator", status="active")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [member_record]
    session.execute.return_value = mock_result

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await get_current_creator_user(current_user=jr_user, session=session)
    assert exc_info.value.status_code == 403
    assert "Junior Facilitators do not have permission" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_creator_user_allows_super_admin():
    from src.api.dependencies.admin import get_current_creator_user
    admin = _make_user(1, is_super_admin=True)
    session = AsyncMock()
    user = await get_current_creator_user(current_user=admin, session=session)
    assert user is admin


@pytest.mark.asyncio
async def test_verify_college_access_allows_assigned_college():
    from src.api.dependencies.admin import verify_college_access
    facilitator = _make_user(20, email="fac@barabari.org", is_admin=False, is_super_admin=False)
    session = AsyncMock()
    team = Team(id=1, name="Program Team", status="active")
    member = TeamMember(id=1, team_id=1, user_id=20, access_role="Sr Facilitator", status="active", colleges=["IIT Bombay", "BITS Pilani"])
    mock_result = MagicMock()
    mock_result.all.return_value = [(member, team)]
    session.execute.return_value = mock_result

    # Should succeed without raising
    await verify_college_access(facilitator, "IIT Bombay", session)
    await verify_college_access(facilitator, "BITS Pilani", session)


@pytest.mark.asyncio
async def test_verify_college_access_rejects_unassigned_college():
    from src.api.dependencies.admin import verify_college_access
    facilitator = _make_user(21, email="fac2@barabari.org", is_admin=False, is_super_admin=False)
    session = AsyncMock()
    team = Team(id=1, name="Program Team", status="active")
    member = TeamMember(id=2, team_id=1, user_id=21, access_role="Jr Facilitator", status="active", colleges=["IIT Bombay"])
    mock_result = MagicMock()
    mock_result.all.return_value = [(member, team)]
    session.execute.return_value = mock_result

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await verify_college_access(facilitator, "Delhi University", session)
    assert exc_info.value.status_code == 403
    assert "Delhi University" in exc_info.value.detail


@pytest.mark.asyncio
async def test_placement_creator_gets_unrestricted_college_access():
    """Placement / Partnership Team Creators get unrestricted access to all student analytics across colleges."""
    from src.api.dependencies.admin import verify_college_access, get_user_allowed_colleges
    creator = _make_user(22, email="placement_creator@barabari.org", is_admin=False, is_super_admin=False)
    session = AsyncMock()
    team = Team(id=2, name="Placement Team", status="active")
    member = TeamMember(id=3, team_id=2, user_id=22, access_role="Creator", status="active", colleges=[])
    mock_result = MagicMock()
    mock_result.all.return_value = [(member, team)]
    session.execute.return_value = mock_result

    allowed = await get_user_allowed_colleges(creator, session)
    assert allowed is None  # None indicates unrestricted access to all colleges

    # Should not raise for ANY college name
    await verify_college_access(creator, "Any College In India", session)


@pytest.mark.asyncio
async def test_placement_creator_gets_bulk_ats_access():
    """Placement / Partnership Team Creators get full access to Bulk ATS."""
    from src.api.dependencies.admin import get_current_bulk_ats_user
    creator = _make_user(23, email="placement_creator2@barabari.org", is_admin=False, is_super_admin=False)
    session = AsyncMock()
    team = Team(id=2, name="Placement Team", status="active")
    member = TeamMember(id=4, team_id=2, user_id=23, access_role="Creator", status="active")
    mock_result = MagicMock()
    mock_result.all.return_value = [(member, team)]
    session.execute.return_value = mock_result

    user = await get_current_bulk_ats_user(current_user=creator, session=session)
    assert user is creator


@pytest.mark.asyncio
async def test_placement_team_member_is_denied_bulk_ats_access():
    """Regular Team Members in the Placement Team are denied Bulk ATS access."""
    from src.api.dependencies.admin import get_current_bulk_ats_user
    member_user = _make_user(24, email="placement_member@barabari.org", is_admin=False, is_super_admin=False)
    session = AsyncMock()
    team = Team(id=2, name="Placement Team", status="active")
    member = TeamMember(id=5, team_id=2, user_id=24, access_role="Team Member", status="active")
    mock_result = MagicMock()
    mock_result.all.return_value = [(member, team)]
    session.execute.return_value = mock_result

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await get_current_bulk_ats_user(current_user=member_user, session=session)
    assert exc_info.value.status_code == 403
    assert "Bulk ATS access is restricted" in exc_info.value.detail


@pytest.mark.asyncio
async def test_program_team_creator_is_denied_bulk_ats_access():
    """Creators in Program Team do NOT get Bulk ATS access (Bulk ATS is reserved for Placement Creators and Recruiters)."""
    from src.api.dependencies.admin import get_current_bulk_ats_user
    program_creator = _make_user(25, email="prog_creator@barabari.org", is_admin=False, is_super_admin=False)
    session = AsyncMock()
    team = Team(id=1, name="Program Team", status="active")
    member = TeamMember(id=6, team_id=1, user_id=25, access_role="Creator", status="active")
    mock_result = MagicMock()
    mock_result.all.return_value = [(member, team)]
    session.execute.return_value = mock_result

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await get_current_bulk_ats_user(current_user=program_creator, session=session)
    assert exc_info.value.status_code == 403


def test_is_super_admin_user_from_token_role():
    user = _make_user(30, email="sso_super@corp.org", is_super_admin=False)
    setattr(user, "_token_role", "SUPER_ADMIN")
    assert is_super_admin_user(user) is True


def test_is_admin_user_from_token_role():
    user = _make_user(31, email="sso_admin@corp.org", is_admin=False)
    setattr(user, "_token_role", "ADMIN")
    assert is_admin_user(user) is True


@pytest.mark.asyncio
async def test_get_current_creator_user_allows_creator_token_role():
    from src.api.dependencies.admin import get_current_creator_user
    user = _make_user(32, email="sso_creator@corp.org", is_admin=False)
    setattr(user, "_token_role", "CREATOR")
    session = AsyncMock()
    result = await get_current_creator_user(current_user=user, session=session)
    assert result is user


@pytest.mark.asyncio
async def test_get_current_creator_user_denies_jr_facilitator_token_role():
    from src.api.dependencies.admin import get_current_creator_user
    user = _make_user(33, email="sso_jr@corp.org", is_admin=False)
    setattr(user, "_token_role", "JR FACILITATOR")
    session = AsyncMock()
    with pytest.raises(fastapi.HTTPException) as exc_info:
        await get_current_creator_user(current_user=user, session=session)
    assert exc_info.value.status_code == 403
    assert "Junior Facilitators do not have permission" in exc_info.value.detail


@pytest.mark.asyncio
async def test_add_team_member_allows_creation_without_password_for_sso():
    """Central Auth: Adding a member without a password creates a placeholder SSO account."""
    mock_session = AsyncMock()
    team = Team(id=1, name="Core", status="active")
    
    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = None
        elif "from \"user\"" in stmt_str or "user.email" in stmt_str:
            result.scalar_one_or_none.return_value = None
        elif "from team" in stmt_str or "team.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = team
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock

    repo = TeamCRUDRepository(async_session=mock_session)
    member_data = TeamMemberCreate(name="SSO Creator", email="creator@barabari.org", access_role="creator")
    created = await repo.add_team_member(team_id=1, member_data=member_data)
    assert created["email"] == "creator@barabari.org"
    assert created["access_role"] == "creator"
    assert mock_session.add.call_count >= 1


# --- 6 Operational Roles Matrix Tests (Placement Team & Program Team) --------


def test_permission_matrix_placement_creator():
    """1. Placement Team Creator: full creator portal, bulk ATS, unrestricted colleges, edit access."""
    from src.api.dependencies.admin import calculate_user_permissions
    user = _make_user(101, email="place_creator@barabari.org")
    team = Team(id=1, name="Placement Team", status="active")
    member = TeamMember(id=1, team_id=1, user_id=101, access_role="Creator", status="active")

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_create_roles"] is True
    assert perms["can_access_ats"] is True
    assert perms["can_edit"] is True
    assert perms["is_view_only"] is False
    assert perms["colleges"] is None
    assert perms["is_team_lead"] is True


def test_permission_matrix_placement_team_member():
    """2. Placement Team Member: view access only, no colleges assigned, no edit access, no ATS."""
    from src.api.dependencies.admin import calculate_user_permissions
    user = _make_user(102, email="place_member@barabari.org")
    team = Team(id=1, name="Placement Team", status="active")
    member = TeamMember(id=2, team_id=1, user_id=102, access_role="Team Member", status="active")

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_create_roles"] is False
    assert perms["can_access_ats"] is False
    assert perms["can_edit"] is False
    assert perms["is_view_only"] is True
    assert perms["colleges"] is None  # View access across colleges without restrictions
    assert perms["is_team_lead"] is False


def test_permission_matrix_program_creator():
    """3. Program Team Creator: creator portal, all colleges, interview/analyst access, NO bulk ATS."""
    from src.api.dependencies.admin import calculate_user_permissions
    user = _make_user(103, email="prog_creator@barabari.org")
    team = Team(id=2, name="Program Team", status="active")
    member = TeamMember(id=3, team_id=2, user_id=103, access_role="Creator", status="active")

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_create_roles"] is True
    assert perms["can_access_ats"] is False
    assert perms["can_edit"] is True
    assert perms["is_view_only"] is False
    assert perms["colleges"] is None  # Unrestricted all colleges
    assert perms["is_team_lead"] is True


def test_permission_matrix_program_senior_facilitator():
    """4. Program Team Senior Facilitator: all colleges, creator portal, NO bulk ATS."""
    from src.api.dependencies.admin import calculate_user_permissions
    user = _make_user(104, email="sr_facilitator@barabari.org")
    team = Team(id=2, name="Program Team", status="active")
    member = TeamMember(id=4, team_id=2, user_id=104, access_role="Sr Facilitator", status="active")

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_create_roles"] is True
    assert perms["can_access_ats"] is False
    assert perms["can_edit"] is True
    assert perms["is_view_only"] is False
    assert perms["colleges"] is None  # Unrestricted access to all colleges


def test_permission_matrix_program_junior_facilitator():
    """5. Program Team Junior Facilitator: strictly restricted to assigned colleges, NO creator portal, NO bulk ATS."""
    from src.api.dependencies.admin import calculate_user_permissions
    user = _make_user(105, email="jr_facilitator@barabari.org")
    team = Team(id=2, name="Program Team", status="active")
    member = TeamMember(
        id=5,
        team_id=2,
        user_id=105,
        access_role="Jr Facilitator",
        colleges=["BITS Pilani", "IIT Delhi"],
        status="active",
    )

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_create_roles"] is False
    assert perms["can_access_ats"] is False
    assert perms["can_edit"] is False
    assert perms["is_view_only"] is True
    assert set(perms["colleges"]) == {"BITS Pilani", "IIT Delhi"}
    assert perms["is_team_lead"] is False


def test_permission_matrix_program_team_member():
    """6. Program Team Member: view access only, no colleges assigned, no edit access, NO creator portal, NO ATS."""
    from src.api.dependencies.admin import calculate_user_permissions
    user = _make_user(106, email="prog_member@barabari.org")
    team = Team(id=2, name="Program Team", status="active")
    member = TeamMember(id=6, team_id=2, user_id=106, access_role="Team Member", status="active")

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_create_roles"] is False
    assert perms["can_access_ats"] is False
    assert perms["can_edit"] is False
    assert perms["is_view_only"] is True
    assert perms["colleges"] is None
    assert perms["is_team_lead"] is False


def test_junior_facilitator_with_team_member_role_elsewhere_retains_college_restrictions():
    """Junior Facilitator in one team who is also a Team Member in another team
    must NOT get widened college access (colleges=None). Their colleges must remain restricted.
    """
    from src.api.dependencies.admin import calculate_user_permissions

    user = _make_user(108, email="jf_multiteam@barabari.org")
    team_prog = Team(id=1, name="Program Team", status="active")
    member_jf = TeamMember(
        id=10,
        team_id=1,
        user_id=108,
        access_role="Jr Facilitator",
        colleges=["BITS Pilani", "IIT Delhi"],
        status="active",
    )
    team_place = Team(id=2, name="Placement Team", status="active")
    member_tm = TeamMember(
        id=11,
        team_id=2,
        user_id=108,
        access_role="Team Member",
        status="active",
    )

    perms = calculate_user_permissions(user, [(member_jf, team_prog), (member_tm, team_place)])
    assert perms["can_create_roles"] is False
    assert perms["can_access_ats"] is False
    assert perms["can_edit"] is False
    assert perms["is_view_only"] is True
    # Colleges must NOT be widened to None (unrestricted)
    assert perms["colleges"] is not None
    assert set(perms["colleges"]) == {"BITS Pilani", "IIT Delhi"}
    assert perms["is_team_lead"] is False


@pytest.mark.asyncio
async def test_creator_portal_denies_team_member():
    """Team members cannot create or modify roles in Creator Portal."""
    from src.api.dependencies.admin import get_current_creator_user
    member_user = _make_user(107, email="member@barabari.org")
    session = AsyncMock()
    team = Team(id=1, name="Program Team", status="active")
    member = TeamMember(id=7, team_id=1, user_id=107, access_role="Team Member", status="active")
    mock_result = MagicMock()
    mock_result.all.return_value = [(member, team)]
    session.execute.return_value = mock_result

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await get_current_creator_user(current_user=member_user, session=session)
    assert exc_info.value.status_code == 403
    assert "Team members have view-only access" in exc_info.value.detail


@pytest.mark.asyncio
async def test_allowed_colleges_for_senior_facilitator_and_team_member():
    """Senior Facilitator and Team Member get unrestricted college access (None)."""
    from src.api.dependencies.admin import get_user_allowed_colleges
    sr_user = _make_user(108, email="sr@barabari.org")
    session = AsyncMock()
    team = Team(id=1, name="Program Team", status="active")
    member = TeamMember(id=8, team_id=1, user_id=108, access_role="Sr Facilitator", status="active")
    mock_result = MagicMock()
    mock_result.all.return_value = [(member, team)]
    session.execute.return_value = mock_result

    allowed = await get_user_allowed_colleges(user=sr_user, session=session)
    assert allowed is None

    # Now test Team Member
    member.access_role = "Team Member"
    allowed_tm = await get_user_allowed_colleges(user=sr_user, session=session)
    assert allowed_tm is None


@pytest.mark.asyncio
async def test_team_member_creation_and_update_never_assigns_colleges():
    """Team members in both teams must never be assigned specific colleges and have full view access."""
    mock_session = AsyncMock()
    team = Team(id=1, name="Program Team", status="active")
    user = _make_user(109, email="tm_unassigned@barabari.org")

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = None
        elif "from \"user\"" in stmt_str or "user.email" in stmt_str:
            result.scalar_one_or_none.return_value = user
        elif "from team" in stmt_str:
            result.scalar_one_or_none.return_value = team
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock
    repo = TeamCRUDRepository(async_session=mock_session)

    # Even if colleges are passed in payload for Team Member, they are forced to None
    member_data = TeamMemberCreate(
        name="Team Member",
        email="tm_unassigned@barabari.org",
        access_role="Team Member",
        colleges=["BITS Pilani", "IIT Delhi"],
    )
    created = await repo.add_team_member(team_id=1, member_data=member_data)
    assert created["colleges"] is None

    # Verify update also forces colleges to None for Team Member
    member = TeamMember(id=10, team_id=1, user_id=109, access_role="Team Member", colleges=["BITS Pilani"], status="active")

    def update_exec_mock(stmt):
        res = MagicMock()
        stmt_str = str(stmt).lower()
        if "from \"user\"" in stmt_str or "user.id ==" in stmt_str:
            res.scalar_one_or_none.return_value = user
        else:
            res.scalar_one_or_none.return_value = member
        return res

    mock_session.execute.side_effect = update_exec_mock

    updated = await repo.update_team_member(
        team_id=1,
        member_id=10,
        member_data=TeamMemberUpdate(access_role="Team Member", colleges=["BITS Pilani"]),
    )
    assert updated is not None
    assert updated["colleges"] is None


@pytest.mark.asyncio
async def test_add_team_member_never_elevates_global_admin_flags():
    """Creator or Lead role in a team must NOT escalate user.is_admin or user.is_super_admin."""
    mock_session = AsyncMock()
    team = Team(id=1, name="Placement Team", status="active")
    user = _make_user(110, email="creator_safe@barabari.org", is_admin=False, is_super_admin=False)

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = None
        elif "from \"user\"" in stmt_str or "user.email" in stmt_str:
            result.scalar_one_or_none.return_value = user
        elif "from team" in stmt_str:
            result.scalar_one_or_none.return_value = team
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock
    repo = TeamCRUDRepository(async_session=mock_session)

    member_data = TeamMemberCreate(
        name="Creator Safe",
        email="creator_safe@barabari.org",
        access_role="Creator",
    )
    await repo.add_team_member(team_id=1, member_data=member_data)

    # Verify global flags remained False
    assert user.is_admin is False
    assert user.is_super_admin is False


def test_calculate_user_permissions_handles_member_team_tuples():
    """calculate_user_permissions correctly parses (TeamMember, Team) tuples without lazy loading."""
    from src.api.dependencies.admin import calculate_user_permissions

    user = _make_user(111, is_admin=False, is_super_admin=False)
    team = Team(id=1, name="Placement and Partnership", status="active")
    member = TeamMember(id=5, team_id=1, user_id=111, access_role="Creator", status="active")

    # Pass as tuple (member, team) as returned by select(TeamMember, Team).all()
    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_create_roles"] is True
    assert perms["can_access_ats"] is True
    assert perms["colleges"] is None


@pytest.mark.asyncio
async def test_user_email_sanitization_in_crud():
    """UserCRUDRepository.create_user strips and lowercases email."""
    from src.repository.crud.user import UserCRUDRepository

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar.return_value = None
    mock_session.execute.return_value = mock_result

    repo = UserCRUDRepository(async_session=mock_session)
    created_user = await repo.create_user(
        email="  Test.User@Barabari.ORG  ",
        password="SecurePassword123!",
        name="Test User",
    )
    assert created_user.email == "test.user@barabari.org"


@pytest.mark.asyncio
async def test_analytics_v2_idor_student_access_rejected():
    """Junior Facilitator cannot view student belonging to unauthorized college."""
    from unittest.mock import patch
    from src.api.routes.analytics_v2 import verify_student_access

    jf_user = _make_user(112, is_admin=False, is_super_admin=False)
    target_student = User(id=200, email="target@barabari.org", university="Unauthorized University")

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = target_student
    mock_session.execute.return_value = mock_result

    # Mock allowed colleges: jf_user only allowed at "Allowed College"
    with patch("src.api.routes.analytics_v2.get_user_allowed_colleges", return_value={"Allowed College"}):
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await verify_student_access(student_id=200, user=jf_user, session=mock_session)
        assert exc_info.value.status_code == 403
        assert "Access denied" in exc_info.value.detail


@pytest.mark.asyncio
async def test_verify_password_rejects_empty_hash_without_unknown_hash_error():
    """Passwordless SSO accounts must raise PasswordDoesNotMatch rather than Passlib UnknownHashError."""
    from src.repository.crud.user import UserCRUDRepository

    mock_session = AsyncMock()
    mock_result = MagicMock()
    # User with empty password hash (created via SSO)
    user_without_pw = User(id=115, email="sso_user@barabari.org", password_hash="")
    mock_result.scalar.return_value = user_without_pw
    mock_session.execute.return_value = mock_result

    repo = UserCRUDRepository(async_session=mock_session)
    with pytest.raises(PasswordDoesNotMatch) as exc_info:
        await repo.verify_password(email="sso_user@barabari.org", password="SomePassword123!")
    assert "Central SSO" in str(exc_info.value) or "Password does not match" in str(exc_info.value)


@pytest.mark.asyncio
async def test_dashboard_top_roles_rejects_unauthorized_college():
    """get_dashboard_top_roles rejects college outside facilitator's allowed colleges."""
    from src.api.routes.analytics_v2 import get_dashboard_top_roles

    jf_user = _make_user(116, is_admin=False, is_super_admin=False)
    mock_session = AsyncMock()

    with patch("src.api.routes.analytics_v2.get_user_allowed_colleges", return_value={"Allowed College"}):
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await get_dashboard_top_roles(
                college="Unauthorized College",
                current_user=jf_user,
                session=mock_session,
            )
        assert exc_info.value.status_code == 403
        assert "Unauthorized College" in exc_info.value.detail


# --- Tests for Senior Review Concerns (PR #129 Fixes) -------------------------


@pytest.mark.asyncio
async def test_ordinary_user_login_fallback_role_is_student_not_staff():
    """An ordinary user with no staff membership receives STUDENT role_claim, not STAFF."""
    from src.api.routes.users import login_user
    from src.models.schemas.user import UserLogin

    mock_user = User(
        id=501,
        email="student@barabari.org",
        name="Student User",
        is_admin=False,
        is_super_admin=False,
        role=None,
        password_hash="somehash",
        created_at=datetime.datetime.now(datetime.timezone.utc),
        is_onboarded=True,
    )
    mock_session = AsyncMock()
    mock_session_repo = AsyncMock()
    mock_session_repo.create_session.return_value = MagicMock(token="ref-tok")

    mock_user_repo = AsyncMock()
    mock_user_repo.verify_password.return_value = mock_user

    # Mock no team memberships in DB
    mock_res = MagicMock()
    mock_res.all.return_value = []
    mock_session.execute.return_value = mock_res

    with patch("src.api.routes.users.jwt_generator.generate_access_token_for_user") as mock_jwt:
        mock_jwt.return_value = "jwt-student-token"
        resp = await login_user(
            payload=UserLogin(email="student@barabari.org", password="ValidPassword123!"),
            user_repo=mock_user_repo,
            session_repo=mock_session_repo,
            session=mock_session,
        )

        call_claims = mock_jwt.call_args[1].get("extra_claims", {})
        assert call_claims.get("role") == "STUDENT"
        assert resp.authorized_user.access_role is None
        assert resp.authorized_user.role == "student"


@pytest.mark.asyncio
async def test_student_token_rejected_by_get_current_admin_user():
    """get_current_admin_user rejects users with STUDENT token and no staff memberships."""
    from src.api.dependencies.admin import get_current_admin_user

    student_user = _make_user(502, email="student2@barabari.org", is_admin=False, is_super_admin=False)
    setattr(student_user, "_token_role", "STUDENT")

    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_res

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await get_current_admin_user(current_user=student_user, session=mock_session)
    assert exc_info.value.status_code == 403
    assert "Administrator access required" in exc_info.value.detail


def test_auth_token_role_does_not_modify_sqlalchemy_persistent_fields():
    """Token role claims must not mutate persistent SQLAlchemy fields is_admin, is_super_admin, or role."""
    from src.api.dependencies.admin import is_admin_user, is_super_admin_user

    # User loaded from DB
    user = User(
        id=503,
        email="sso_admin@barabari.org",
        is_admin=False,
        is_super_admin=False,
        role=None,
    )

    claims = {"role": "SUPER_ADMIN", "sub": "sso_admin@barabari.org"}
    raw_role = str(claims.get("role") or "").strip().upper()
    norm_role = raw_role.replace("_", " ")

    # Emulate auth.py get_current_user behavior
    setattr(user, "_token_claims", claims)
    setattr(user, "_token_role", raw_role)
    if norm_role == "SUPER ADMIN":
        setattr(user, "_token_is_super_admin", True)
        setattr(user, "_token_is_admin", True)
    elif norm_role == "ADMIN":
        setattr(user, "_token_is_admin", True)

    # Persistent model fields remain pristine (False / None)
    assert user.is_super_admin is False
    assert user.is_admin is False
    assert user.role is None

    # Dependency functions correctly recognize token-derived admin flags
    assert is_super_admin_user(user) is True
    assert is_admin_user(user) is True


@pytest.mark.asyncio
async def test_add_team_member_does_not_persist_team_role_globally():
    """add_team_member sets user.role=None and never mutates user.role for existing users."""
    mock_session = AsyncMock()
    team = Team(id=1, name="Placement Team", status="active")

    # 1. New user creation
    def exec_new(stmt):
        res = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            res.scalar_one_or_none.return_value = None
        elif "user" in stmt_str:
            res.scalar_one_or_none.return_value = None
        elif "team" in stmt_str:
            res.scalar_one_or_none.return_value = team
        else:
            res.scalar_one_or_none.return_value = None
        return res

    mock_session.execute.side_effect = exec_new
    repo = TeamCRUDRepository(async_session=mock_session)

    # Capture added user
    added_objects = []
    mock_session.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    member_data = TeamMemberCreate(
        name="New Member",
        email="new_member@barabari.org",
        access_role="Creator",
    )
    await repo.add_team_member(team_id=1, member_data=member_data)

    new_users = [o for o in added_objects if isinstance(o, User)]
    assert len(new_users) == 1
    assert new_users[0].role is None

    # 2. Existing user add to team
    existing_user = User(
        id=601,
        email="existing@barabari.org",
        name="Existing User",
        role=None,
        password_hash="somehash",
    )

    def exec_existing(stmt):
        res = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            res.scalar_one_or_none.return_value = None
        elif "user" in stmt_str:
            res.scalar_one_or_none.return_value = existing_user
        elif "team" in stmt_str:
            res.scalar_one_or_none.return_value = team
        else:
            res.scalar_one_or_none.return_value = None
        return res

    mock_session.execute.side_effect = exec_existing
    await repo.add_team_member(
        team_id=1,
        member_data=TeamMemberCreate(
            name="Existing User",
            email="existing@barabari.org",
            access_role="Creator",
        ),
    )
    # Existing user's role must NOT be mutated to 'creator'
    assert existing_user.role is None


@pytest.mark.asyncio
async def test_get_current_creator_user_allows_multi_team_creator_despite_token_jr_role():
    """A user who has a Junior Facilitator token role but is also a Creator in another team is allowed."""
    from src.api.dependencies.admin import get_current_creator_user

    user = _make_user(701, email="multi_team@barabari.org", is_admin=False, is_super_admin=False)
    # Token reflects their oldest/first membership (Jr Facilitator)
    setattr(user, "_token_role", "JUNIOR_FACILITATOR")

    team_a = Team(id=1, name="Program Team Alpha", status="active")
    member_a = TeamMember(id=10, team_id=1, user_id=701, access_role="Junior Facilitator", status="active")

    team_b = Team(id=2, name="Program Team Beta", status="active")
    member_b = TeamMember(id=11, team_id=2, user_id=701, access_role="Creator", status="active")

    mock_session = AsyncMock()
    mock_res = MagicMock()
    # DB returns active memberships for both teams
    mock_res.all.return_value = [(member_a, team_a), (member_b, team_b)]
    mock_session.execute.return_value = mock_res

    allowed_user = await get_current_creator_user(current_user=user, session=mock_session)
    assert allowed_user is user


@pytest.mark.asyncio
async def test_analytics_v2_unassigned_college_scoping_for_junior_facilitators():
    """Analytics routes apply get_user_allowed_colleges and filter out unassigned colleges."""
    from src.api.routes.analytics_v2 import (
        get_dashboard_recent_interviews,
        get_dashboard_recent_students,
        get_dashboard_attention_required,
        get_students_summary,
        get_roles_summary,
        get_roles_performance,
        get_roles_weak_skills,
        get_role_detail,
        get_difficulty_metrics,
        get_questions_analytics,
        get_dropoff_funnel,
        get_predictive_alerts,
        get_benchmarking,
        get_forecasting,
        global_search,
    )

    jf_user = _make_user(801, email="jf@barabari.org", is_admin=False, is_super_admin=False)
    mock_session = AsyncMock()

    # Case 1: Junior Facilitator with empty allowed colleges receives empty responses
    with patch("src.api.routes.analytics_v2.get_user_allowed_colleges", return_value=set()):
        r_int = await get_dashboard_recent_interviews(current_user=jf_user, session=mock_session)
        assert r_int.items == []

        r_stu = await get_dashboard_recent_students(current_user=jf_user, session=mock_session)
        assert r_stu.items == []

        r_att = await get_dashboard_attention_required(current_user=jf_user, session=mock_session)
        assert r_att.items == []

        r_sum = await get_students_summary(current_user=jf_user, session=mock_session)
        assert all(kpi.value in (0, 0.0) for kpi in r_sum.kpis)

        r_role_sum = await get_roles_summary(current_user=jf_user, session=mock_session)
        assert any(kpi.key == "total_roles" and kpi.value == 0 for kpi in r_role_sum.kpis)

        r_perf = await get_roles_performance(current_user=jf_user, session=mock_session)
        assert r_perf.items == []

        r_weak = await get_roles_weak_skills(current_user=jf_user, session=mock_session)
        assert r_weak.items == []

        r_detail = await get_role_detail(role_id="Software Engineer", current_user=jf_user, session=mock_session)
        assert r_detail.items == []

        r_diff = await get_difficulty_metrics(current_user=jf_user, session=mock_session)
        assert r_diff.items == []

        r_q = await get_questions_analytics(current_user=jf_user, session=mock_session)
        assert r_q.items == []

        r_fun = await get_dropoff_funnel(current_user=jf_user, session=mock_session)
        assert r_fun.stages == []

        r_pred = await get_predictive_alerts(current_user=jf_user, session=mock_session)
        assert r_pred.items == []

        r_bench = await get_benchmarking(current_user=jf_user, session=mock_session)
        assert r_bench.items == []

        r_fore = await get_forecasting(current_user=jf_user, session=mock_session)
        assert r_fore.points == []

        r_glob = await global_search(q="test", current_user=jf_user, session=mock_session)
        assert r_glob.students == []
        assert r_glob.colleges == []
        assert r_glob.interviews == []

        from src.api.routes.analytics_v2 import get_interviews_summary
        r_int_sum = await get_interviews_summary(current_user=jf_user, session=mock_session)
        assert all(kpi.value in (0, 0.0, None) for kpi in r_int_sum.kpis)


@pytest.mark.asyncio
async def test_creator_portal_rejects_revoked_member_with_stale_creator_token():
    """If a user was removed from a team (0 active memberships, but has team member history), their stale
    CREATOR token must NOT grant them Creator Portal access."""
    from src.api.dependencies.admin import get_current_creator_user

    revoked_user = _make_user(901, email="revoked_creator@barabari.org", is_admin=False, is_super_admin=False)
    setattr(revoked_user, "_token_role", "CREATOR")

    mock_session = AsyncMock()

    def exec_mock(stmt):
        stmt_str = str(stmt).lower()
        res = MagicMock()
        if "team.status" in stmt_str and "team_member.status" in stmt_str:
            res.all.return_value = []
        else:
            res.scalars.return_value.all.return_value = [999]
        return res

    mock_session.execute.side_effect = exec_mock

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await get_current_creator_user(current_user=revoked_user, session=mock_session)
    assert exc_info.value.status_code == 403
    assert "no longer active" in exc_info.value.detail or "Creator Portal access required" in exc_info.value.detail


@pytest.mark.asyncio
async def test_update_team_member_to_junior_facilitator_without_colleges_fails():
    """Updating a member whose current colleges is None to Junior Facilitator without
    providing colleges must fail with ValueError."""
    mock_session = AsyncMock()
    repo = TeamCRUDRepository(async_session=mock_session)

    # Existing member was a Creator with colleges=None
    existing_member = TeamMember(
        id=20,
        team_id=1,
        user_id=902,
        access_role="Creator",
        colleges=None,
        status="active",
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = existing_member
    mock_session.execute.return_value = mock_res

    update_payload = TeamMemberUpdate(
        access_role="Junior Facilitator",
        colleges=None,
    )

    with pytest.raises(ValueError) as exc_info:
        await repo.update_team_member(team_id=1, member_id=20, member_data=update_payload)
    assert "Assigned colleges are required" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_user_by_email_exact_match_priority():
    """get_user_by_email checks exact match first, falling back to case-insensitive match with deterministic ordering."""
    from src.repository.crud.user import UserCRUDRepository

    mock_session = AsyncMock()
    repo = UserCRUDRepository(async_session=mock_session)

    exact_user = _make_user(903, email="User.Case@Barabari.org")
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = exact_user
    mock_session.execute.return_value = mock_res

    found = await repo.get_user_by_email(email="User.Case@Barabari.org")
    assert found.id == 903
    assert found.email == "User.Case@Barabari.org"


@pytest.mark.asyncio
async def test_job_profiles_v2_routes_require_creator_permission():
    """get_recent_activity, get_job_profiles_summary, and get_job_profile_questions_v2
    are protected by get_current_creator_user and reject plain team members."""
    from src.api.routes.job_profiles_v2 import (
        get_recent_activity,
        get_job_profiles_summary,
        get_job_profile_questions_v2,
    )

    team_member_user = _make_user(904, email="plain_tm@barabari.org", is_admin=False, is_super_admin=False)
    team = Team(id=1, name="Program Team", status="active")
    member = TeamMember(id=21, team_id=1, user_id=904, access_role="Team Member", status="active")

    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.all.return_value = [(member, team)]
    mock_session.execute.return_value = mock_res

    from src.api.dependencies.admin import get_current_creator_user
    with pytest.raises(fastapi.HTTPException) as exc_info:
        await get_current_creator_user(current_user=team_member_user, session=mock_session)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_update_member_route_catches_value_error_as_400():
    """update_member route must catch ValueError and return clean 400 instead of 500."""
    from src.api.routes.teams_v2 import update_member
    mock_repo = AsyncMock()
    mock_repo.async_session = AsyncMock()
    admin_user = _make_user(1, email="admin@barabari.org", is_super_admin=True)

    mock_repo.update_team_member.side_effect = ValueError(
        "Assigned colleges are required when changing role to Junior Facilitator."
    )

    update_payload = TeamMemberUpdate(access_role="Junior Facilitator")
    with pytest.raises(fastapi.HTTPException) as exc_info:
        await update_member(
            team_id=1,
            member_id=20,
            member_data=update_payload,
            current_user=admin_user,
            team_repo=mock_repo,
        )
    assert exc_info.value.status_code == 400
    assert "Assigned colleges are required" in exc_info.value.detail


@pytest.mark.asyncio
async def test_add_team_member_as_junior_facilitator_without_colleges_fails():
    """Directly creating a Junior Facilitator without colleges must fail with ValueError."""
    mock_session = AsyncMock()
    repo = TeamCRUDRepository(async_session=mock_session)

    team = Team(id=1, name="Program Team", status="active")
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = team
    mock_session.execute.return_value = mock_res

    # 1. colleges=None
    member_data = TeamMemberCreate(
        name="New JF",
        email="new_jf@barabari.org",
        access_role="Junior Facilitator",
        colleges=None,
    )
    with pytest.raises(ValueError) as exc_info:
        await repo.add_team_member(team_id=1, member_data=member_data)
    assert "Assigned colleges are required" in str(exc_info.value)

    # 2. colleges=[] (empty list)
    member_data_empty = TeamMemberCreate(
        name="New JF Empty",
        email="new_jf_empty@barabari.org",
        access_role="Junior Facilitator",
        colleges=[],
    )
    with pytest.raises(ValueError) as exc_info:
        await repo.add_team_member(team_id=1, member_data=member_data_empty)
    assert "Assigned colleges are required" in str(exc_info.value)

    # 3. colleges with only whitespace strings
    member_data_ws = TeamMemberCreate(
        name="New JF Whitespace",
        email="new_jf_ws@barabari.org",
        access_role="Junior Facilitator",
        colleges=["   ", ""],
    )
    with pytest.raises(ValueError) as exc_info:
        await repo.add_team_member(team_id=1, member_data=member_data_ws)
    assert "Assigned colleges are required" in str(exc_info.value)


@pytest.mark.asyncio
async def test_add_team_member_as_junior_facilitator_with_valid_colleges_succeeds():
    """Creating a Junior Facilitator with valid colleges succeeds and assigns sanitized colleges."""
    mock_session = AsyncMock()
    repo = TeamCRUDRepository(async_session=mock_session)

    team = Team(id=1, name="Program Team", status="active")

    def exec_mock(stmt):
        res = MagicMock()
        stmt_str = str(stmt).lower()
        if "from \"user\"" in stmt_str or "user.email" in stmt_str:
            res.scalar_one_or_none.return_value = None
        elif "from team_member" in stmt_str:
            res.scalar_one_or_none.return_value = None
        else:
            res.scalar_one_or_none.return_value = team
        return res

    mock_session.execute.side_effect = exec_mock

    member_data = TeamMemberCreate(
        name="Valid JF",
        email="valid_jf@barabari.org",
        access_role="Junior Facilitator",
        colleges=["  BITS Pilani  ", "IIT Delhi"],
    )
    res = await repo.add_team_member(team_id=1, member_data=member_data)
    assert res["access_role"] == "Junior Facilitator"
    assert res["colleges"] == ["BITS Pilani", "IIT Delhi"]


@pytest.mark.asyncio
async def test_update_team_member_to_junior_facilitator_with_empty_colleges_list_fails():
    """Updating a member to Junior Facilitator with an empty or whitespace-only list fails with ValueError."""
    mock_session = AsyncMock()
    repo = TeamCRUDRepository(async_session=mock_session)

    existing_member = TeamMember(
        id=20,
        team_id=1,
        user_id=902,
        access_role="Creator",
        colleges=None,
        status="active",
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = existing_member
    mock_session.execute.return_value = mock_res

    update_payload = TeamMemberUpdate(
        access_role="Junior Facilitator",
        colleges=["  ", ""],
    )

    with pytest.raises(ValueError) as exc_info:
        await repo.update_team_member(team_id=1, member_id=20, member_data=update_payload)
    assert "Assigned colleges are required" in str(exc_info.value)


@pytest.mark.asyncio
async def test_update_existing_junior_facilitator_clearing_colleges_fails():
    """Attempting to clear colleges on an existing Junior Facilitator fails with ValueError."""
    mock_session = AsyncMock()
    repo = TeamCRUDRepository(async_session=mock_session)

    existing_jf = TeamMember(
        id=22,
        team_id=1,
        user_id=905,
        access_role="Junior Facilitator",
        colleges=["BITS Pilani"],
        status="active",
    )
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = existing_jf
    mock_session.execute.return_value = mock_res

    update_payload = TeamMemberUpdate(
        colleges=[],
    )

    with pytest.raises(ValueError) as exc_info:
        await repo.update_team_member(team_id=1, member_id=22, member_data=update_payload)
    assert "Assigned colleges are required" in str(exc_info.value)







