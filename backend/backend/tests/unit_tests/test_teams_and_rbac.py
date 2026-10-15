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
from unittest.mock import AsyncMock, MagicMock
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



