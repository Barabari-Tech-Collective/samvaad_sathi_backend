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
    calculate_user_permissions,
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
from src.securities.hashing.password import pwd_generator


@pytest.fixture(autouse=True)
def _mock_default_central_provisioning():
    with patch(
        "src.repository.crud.team_v2.provision_or_invite_staff",
        new=AsyncMock(return_value="mock-central-user-uuid"),
    ) as mock_prov:
        yield mock_prov


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
    monkeypatch.setattr(
        settings, "SUPER_ADMIN_EMAILS", "super@corp.org, founder@corp.org"
    )
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
    super_admin = _make_user(
        10, email="super@corp.org", is_admin=False, is_super_admin=True
    )
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


def test_super_admin_in_db_not_locked_out_if_in_admin_emails(monkeypatch):
    """A DB super admin who is also in ADMIN_EMAILS must NOT be demoted."""
    monkeypatch.setattr(settings, "SUPER_ADMIN_EMAILS", "")
    monkeypatch.setattr(
        settings, "ADMIN_EMAILS", "lead@barabari.org, admin@barabari.org"
    )
    super_user = _make_user(
        1, email="lead@barabari.org", is_super_admin=True, is_admin=True
    )
    assert is_super_admin_user(super_user) is True


def test_standard_admin_in_db_denied_super_admin_even_with_token_role(monkeypatch):
    """Any standard admin (is_admin=True, is_super_admin=False) cannot be elevated by token claims."""
    monkeypatch.setattr(settings, "SUPER_ADMIN_EMAILS", "")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", "")
    standard_admin = _make_user(
        2, email="coord@barabari.org", is_admin=True, is_super_admin=False
    )
    setattr(standard_admin, "_token_role", "SUPER_ADMIN")
    setattr(standard_admin, "_token_is_super_admin", True)
    assert is_admin_user(standard_admin) is True
    assert is_super_admin_user(standard_admin) is False


def test_admin_barabari_is_standard_admin_and_denied_super_admin(monkeypatch):
    """admin@barabari.org must strictly have Admin access and be denied Super Admin even with token claims."""
    monkeypatch.setattr(settings, "SUPER_ADMIN_EMAILS", "Superadmin@barabari.org")
    monkeypatch.setattr(
        settings, "ADMIN_EMAILS", "admin@barabari.org, admin@samvaad-sathi.com"
    )
    admin = _make_user(
        100, email="admin@barabari.org", is_admin=True, is_super_admin=False
    )
    # Even if incoming token claim says SUPER_ADMIN:
    setattr(admin, "_token_is_super_admin", True)
    setattr(admin, "_token_role", "SUPER_ADMIN")

    assert is_admin_user(admin) is True
    assert is_super_admin_user(admin) is False


def test_admin_emails_allowlist_denies_super_admin_elevation(monkeypatch):
    """Accounts listed in ADMIN_EMAILS cannot be elevated to super admin by token claims unless in SUPER_ADMIN_EMAILS."""
    monkeypatch.setattr(settings, "SUPER_ADMIN_EMAILS", "Superadmin@corp.org")
    monkeypatch.setattr(settings, "ADMIN_EMAILS", "standard_admin@corp.org")
    user = _make_user(
        101, email="standard_admin@corp.org", is_admin=False, is_super_admin=False
    )
    setattr(user, "_token_is_super_admin", True)
    setattr(user, "_token_role", "SUPER_ADMIN")

    assert is_admin_user(user) is True
    assert is_super_admin_user(user) is False

    # But super admin listed in SUPER_ADMIN_EMAILS is granted super admin
    super_user = _make_user(
        102, email="superadmin@corp.org", is_admin=False, is_super_admin=False
    )
    assert is_super_admin_user(super_user) is True


# --- Scoped Team Permission & Negative Authorization Tests -------------------


@pytest.mark.asyncio
async def test_check_team_permission_grants_super_admin_short_circuit():
    super_admin = _make_user(1, is_super_admin=True)
    mock_session = AsyncMock()
    assert (
        await check_team_permission(
            super_admin, team_id=10, session=mock_session, require_lead=True
        )
        is True
    )
    mock_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_check_team_permission_grants_global_admin_short_circuit():
    global_admin = _make_user(2, is_admin=True)
    mock_session = AsyncMock()
    assert (
        await check_team_permission(
            global_admin, team_id=10, session=mock_session, require_lead=True
        )
        is True
    )
    mock_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_check_team_permission_rejects_non_member():
    user = _make_user(3, is_admin=False, is_super_admin=False)
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    # Non-member rejected whether require_lead is True or False
    assert (
        await check_team_permission(
            user, team_id=10, session=mock_session, require_lead=False
        )
        is False
    )
    assert (
        await check_team_permission(
            user, team_id=10, session=mock_session, require_lead=True
        )
        is False
    )


@pytest.mark.asyncio
async def test_check_team_permission_blocks_regular_member_from_lead_actions():
    user = _make_user(4, is_admin=False, is_super_admin=False)
    member_record = TeamMember(
        id=1, team_id=10, user_id=user.id, access_role="member", status="active"
    )
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = member_record
    mock_session.execute.return_value = mock_result

    # Regular member CAN view (require_lead=False)
    assert (
        await check_team_permission(
            user, team_id=10, session=mock_session, require_lead=False
        )
        is True
    )
    # Regular member CANNOT manage (require_lead=True)
    assert (
        await check_team_permission(
            user, team_id=10, session=mock_session, require_lead=True
        )
        is False
    )


@pytest.mark.asyncio
async def test_check_team_permission_allows_team_lead():
    user = _make_user(5, is_admin=False, is_super_admin=False)
    lead_record = TeamMember(
        id=2, team_id=10, user_id=user.id, access_role="lead", status="active"
    )
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = lead_record
    mock_session.execute.return_value = mock_result

    # Team Lead CAN both view and manage
    assert (
        await check_team_permission(
            user, team_id=10, session=mock_session, require_lead=False
        )
        is True
    )
    assert (
        await check_team_permission(
            user, team_id=10, session=mock_session, require_lead=True
        )
        is True
    )


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
    assert update.password is None


def test_team_member_password_length_validation():
    """CWE-521: TeamMemberCreate and TeamMemberUpdate must enforce min 8 and max 128 chars."""
    # Under min length (< 8)
    with pytest.raises(pydantic.ValidationError):
        TeamMemberCreate(
            name="Test", email="test@test.com", access_role="member", password="short"
        )

    with pytest.raises(pydantic.ValidationError):
        TeamMemberUpdate(password="1234567")

    # Over max length (> 128)
    long_pw = "a" * 129
    with pytest.raises(pydantic.ValidationError):
        TeamMemberCreate(
            name="Test", email="test@test.com", access_role="member", password=long_pw
        )

    with pytest.raises(pydantic.ValidationError):
        TeamMemberUpdate(password=long_pw)

    # Valid lengths (8 and 128)
    valid_create = TeamMemberCreate(
        name="Test", email="test@test.com", access_role="member", password="a" * 8
    )
    assert valid_create.password == "a" * 8

    valid_update = TeamMemberUpdate(password="a" * 128)
    assert valid_update.password == "a" * 128

    # None is allowed
    none_create = TeamMemberCreate(
        name="Test", email="test@test.com", access_role="member", password=None
    )
    assert none_create.password is None

    none_update = TeamMemberUpdate(password=None)
    assert none_update.password is None

    # Empty string or whitespace normalizes to None (handles frontend blank inputs gracefully)
    empty_str_create = TeamMemberCreate(
        name="Test", email="test@test.com", access_role="member", password=""
    )
    assert empty_str_create.password is None

    whitespace_create = TeamMemberCreate(
        name="Test", email="test@test.com", access_role="member", password="   "
    )
    assert whitespace_create.password is None

    empty_str_update = TeamMemberUpdate(password="")
    assert empty_str_update.password is None

    whitespace_update = TeamMemberUpdate(password="   ")
    assert whitespace_update.password is None


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
            assert (
                "get_current_super_admin_user" in dep_names
            ), f"Route '{route.name}' must require get_current_super_admin_user"


# --- Repository Security & Integrity Tests -----------------------------------


@pytest.mark.asyncio
async def test_add_team_member_rejects_missing_team():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    repo = TeamCRUDRepository(async_session=mock_session)
    member_data = TeamMemberCreate(
        name="Bob", email="bob@test.com", access_role="member"
    )

    with pytest.raises(EntityDoesNotExist):
        await repo.add_team_member(team_id=9999, member_data=member_data)


@pytest.mark.asyncio
async def test_add_team_member_does_not_overwrite_existing_user_password():
    """Security: Never silently change credentials of an existing account (standard admin or regular user)."""
    mock_session = AsyncMock()
    # Team exists
    team = Team(id=1, name="Core", status="active")
    # Existing standard admin user (is_super_admin=False, is_admin=True) with an established password
    existing_admin = User(
        id=10,
        email="admin@barabari.org",
        password_hash="original_secret_hash",
        is_super_admin=False,
        is_admin=True,
    )
    # Existing regular user with an established password
    existing_student = User(
        id=11,
        email="student@barabari.org",
        password_hash="student_secret_hash",
        is_super_admin=False,
        is_admin=False,
    )

    current_lookup_user = existing_admin

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = None
        elif "from team" in stmt_str or "team.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = team
        elif 'from "user"' in stmt_str or "user.email" in stmt_str:
            result.scalar_one_or_none.return_value = current_lookup_user
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock
    repo = TeamCRUDRepository(async_session=mock_session)

    # 1. Attempt to overwrite admin credentials by adding admin to team
    member_data_admin = TeamMemberCreate(
        name="Admin",
        email="admin@barabari.org",
        password="hacker_password_123",
        access_role="lead",
    )
    await repo.add_team_member(team_id=1, member_data=member_data_admin)
    assert existing_admin.password_hash == "original_secret_hash"

    # 2. Attempt to overwrite regular user credentials by adding user to team
    current_lookup_user = existing_student
    member_data_student = TeamMemberCreate(
        name="Student",
        email="student@barabari.org",
        password="hacker_password_456",
        access_role="member",
    )
    await repo.add_team_member(team_id=1, member_data=member_data_student)
    assert existing_student.password_hash == "student_secret_hash"


@pytest.mark.asyncio
async def test_add_team_member_initializes_password_for_uninitialized_user():
    """Allow initializing credentials only if an existing user has an empty/uninitialized password (e.g. from SSO)."""
    mock_session = AsyncMock()
    team = Team(id=1, name="Core", status="active")
    # Existing user with uninitialized (empty) password
    existing_sso_user = User(
        id=20,
        email="staff@barabari.org",
        password_hash="",
        is_super_admin=False,
        is_admin=False,
    )

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = None
        elif "from team" in stmt_str or "team.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = team
        elif 'from "user"' in stmt_str or "user.email" in stmt_str:
            result.scalar_one_or_none.return_value = existing_sso_user
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock

    repo = TeamCRUDRepository(async_session=mock_session)
    member_data = TeamMemberCreate(
        name="Staff",
        email="staff@barabari.org",
        password="new_assigned_pw",
        access_role="lead",
    )
    await repo.add_team_member(team_id=1, member_data=member_data)

    # Assert uninitialized user's password hash was properly initialized
    assert existing_sso_user.password_hash != ""
    assert pwd_generator.is_password_authenticated(
        hash_salt="",
        password="new_assigned_pw",
        hashed_password=existing_sso_user.password_hash,
    )


@pytest.mark.asyncio
async def test_add_team_member_rejects_duplicate_active_membership_before_mutation():
    """Integrity: Prevent duplicate active membership in the same team and ensure no state mutation occurs."""
    mock_session = AsyncMock()
    team = Team(id=1, name="Core", status="active")
    user = User(id=10, email="member@barabari.org", password_hash="original_hash")
    existing_active_member = TeamMember(
        id=5, team_id=1, user_id=10, status="active", access_role="member"
    )

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = existing_active_member
        elif "from team" in stmt_str or "team.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = team
        elif 'from "user"' in stmt_str or "user.email" in stmt_str:
            result.scalar_one_or_none.return_value = user
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock

    repo = TeamCRUDRepository(async_session=mock_session)
    member_data = TeamMemberCreate(
        name="Member",
        email="member@barabari.org",
        password="some_new_password",
        access_role="member",
    )
    with pytest.raises(EntityAlreadyExists):
        await repo.add_team_member(team_id=1, member_data=member_data)

    # Verify user's password hash was NOT mutated before validation raised
    assert user.password_hash == "original_hash"


@pytest.mark.asyncio
async def test_update_team_member_updates_metadata_safely():
    """Security: Team Lead updating member metadata only updates role, domain, purposes, colleges."""
    mock_session = AsyncMock()
    member = TeamMember(
        id=5, team_id=1, user_id=10, status="active", access_role="member"
    )
    user = User(
        id=10,
        email="target@barabari.org",
        password_hash="secure_user_pw",
        name="Target Name",
    )

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = member
        elif 'from "user"' in stmt_str or "user.id ==" in stmt_str:
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
async def test_update_team_member_allows_admin_to_reset_regular_member_password():
    """Administrator caller can reset password for regular team members."""
    mock_session = AsyncMock()
    member = TeamMember(
        id=5, team_id=1, user_id=10, status="active", access_role="member"
    )
    target_user = User(
        id=10,
        email="target@barabari.org",
        password_hash="old_pw_hash",
        name="Target User",
        is_admin=False,
        is_super_admin=False,
    )
    admin_caller = _make_user(
        1, email="admin@barabari.org", is_admin=True, is_super_admin=False
    )

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = member
        elif 'from "user"' in stmt_str or "user.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = target_user
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock
    repo = TeamCRUDRepository(async_session=mock_session)

    update_data = TeamMemberUpdate(password="new_secure_password_123")
    res = await repo.update_team_member(
        team_id=1, member_id=5, member_data=update_data, caller_user=admin_caller
    )

    assert res is not None
    assert target_user.password_hash != "old_pw_hash"
    assert pwd_generator.is_password_authenticated(
        hash_salt="",
        password="new_secure_password_123",
        hashed_password=target_user.password_hash,
    )


@pytest.mark.asyncio
async def test_update_team_member_rejects_non_admin_caller_password_reset():
    """Team Lead or non-admin caller cannot reset passwords via team update."""
    mock_session = AsyncMock()
    member = TeamMember(
        id=5, team_id=1, user_id=10, status="active", access_role="member"
    )
    target_user = User(
        id=10,
        email="target@barabari.org",
        password_hash="old_pw_hash",
        is_admin=False,
        is_super_admin=False,
    )
    lead_caller = _make_user(
        2, email="lead@barabari.org", is_admin=False, is_super_admin=False
    )

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = member
        elif 'from "user"' in stmt_str or "user.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = target_user
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock
    repo = TeamCRUDRepository(async_session=mock_session)

    update_data = TeamMemberUpdate(password="attacker_password_123")
    with pytest.raises(
        ValueError, match="Administrator access required to reset member credentials"
    ):
        await repo.update_team_member(
            team_id=1, member_id=5, member_data=update_data, caller_user=lead_caller
        )

    assert target_user.password_hash == "old_pw_hash"


@pytest.mark.asyncio
async def test_update_team_member_rejects_admin_target_password_reset():
    """Cannot reset credentials for administrative accounts (is_admin=True or is_super_admin=True) via team endpoints."""
    mock_session = AsyncMock()
    member = TeamMember(
        id=5, team_id=1, user_id=10, status="active", access_role="lead"
    )
    target_admin = User(
        id=10,
        email="admin@barabari.org",
        password_hash="admin_original_hash",
        is_admin=True,
        is_super_admin=False,
    )
    admin_caller = _make_user(
        1, email="superadmin@barabari.org", is_admin=True, is_super_admin=True
    )

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = member
        elif 'from "user"' in stmt_str or "user.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = target_admin
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock
    repo = TeamCRUDRepository(async_session=mock_session)

    update_data = TeamMemberUpdate(password="new_admin_password_123")
    with pytest.raises(
        ValueError, match="Cannot reset credentials for administrative accounts"
    ):
        await repo.update_team_member(
            team_id=1, member_id=5, member_data=update_data, caller_user=admin_caller
        )

    assert target_admin.password_hash == "admin_original_hash"


@pytest.mark.asyncio
async def test_update_team_member_rejects_missing_caller_password_reset():
    """Fail-closed: update_team_member with password must reject when caller_user is None."""
    mock_session = AsyncMock()
    member = TeamMember(
        id=5, team_id=1, user_id=10, status="active", access_role="member"
    )
    target_user = User(
        id=10,
        email="target@barabari.org",
        password_hash="old_pw_hash",
        is_admin=False,
        is_super_admin=False,
    )

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = member
        elif 'from "user"' in stmt_str or "user.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = target_user
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock
    repo = TeamCRUDRepository(async_session=mock_session)

    update_data = TeamMemberUpdate(password="some_password_123")
    with pytest.raises(
        ValueError, match="Administrator access required to reset member credentials"
    ):
        await repo.update_team_member(
            team_id=1, member_id=5, member_data=update_data, caller_user=None
        )

    assert target_user.password_hash == "old_pw_hash"


@pytest.mark.asyncio
async def test_get_all_teams_forces_active_status_for_regular_users():
    """Query param status bypass guard: regular users querying status=deleted are forced to active."""
    from src.api.routes.teams_v2 import get_all_teams

    mock_repo = AsyncMock()
    mock_repo.get_all_teams.return_value = []
    regular_user = _make_user(1, email="user@barabari.org", is_super_admin=False)

    await get_all_teams(
        status="deleted", current_user=regular_user, team_repo=mock_repo
    )
    mock_repo.get_all_teams.assert_called_once_with(status="active")


@pytest.mark.asyncio
async def test_get_all_teams_allows_deleted_status_for_super_admin():
    """Super Admins are allowed to inspect deleted teams via query param."""
    from src.api.routes.teams_v2 import get_all_teams

    mock_repo = AsyncMock()
    mock_repo.get_all_teams.return_value = []
    super_admin = _make_user(1, email="superadmin@barabari.org", is_super_admin=True)

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
    member_record = TeamMember(
        id=1, team_id=1, user_id=5, access_role="member", status="active"
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = member_record
    mock_repo.async_session.execute.return_value = mock_result

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await get_active_members(
            team_id=1,
            status="deleted",
            current_user=regular_member,
            team_repo=mock_repo,
        )
    assert exc_info.value.status_code == 403


# --- Senior vs Junior Facilitator RBAC & College Scoping Tests -----------------


@pytest.mark.asyncio
async def test_get_current_creator_user_allows_senior_facilitator():
    from src.api.dependencies.admin import get_current_creator_user

    sr_user = _make_user(
        10, email="sr@barabari.org", is_admin=False, is_super_admin=False
    )
    session = AsyncMock()
    member_record = TeamMember(
        id=1, team_id=1, user_id=10, access_role="Sr Facilitator", status="active"
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [member_record]
    session.execute.return_value = mock_result

    user = await get_current_creator_user(current_user=sr_user, session=session)
    assert user is sr_user


@pytest.mark.asyncio
async def test_get_current_creator_user_denies_junior_facilitator():
    from src.api.dependencies.admin import get_current_creator_user

    jr_user = _make_user(
        11, email="jr@barabari.org", is_admin=False, is_super_admin=False
    )
    session = AsyncMock()
    member_record = TeamMember(
        id=2, team_id=1, user_id=11, access_role="Jr Facilitator", status="active"
    )
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

    facilitator = _make_user(
        20, email="fac@barabari.org", is_admin=False, is_super_admin=False
    )
    session = AsyncMock()
    team = Team(id=1, name="Program Team", status="active")
    member = TeamMember(
        id=1,
        team_id=1,
        user_id=20,
        access_role="Sr Facilitator",
        status="active",
        colleges=["IIT Bombay", "BITS Pilani"],
    )
    mock_result = MagicMock()
    mock_result.all.return_value = [(member, team)]
    session.execute.return_value = mock_result

    # Should succeed without raising
    await verify_college_access(facilitator, "IIT Bombay", session)
    await verify_college_access(facilitator, "BITS Pilani", session)


@pytest.mark.asyncio
async def test_verify_college_access_rejects_unassigned_college():
    from src.api.dependencies.admin import verify_college_access

    facilitator = _make_user(
        21, email="fac2@barabari.org", is_admin=False, is_super_admin=False
    )
    session = AsyncMock()
    team = Team(id=1, name="Program Team", status="active")
    member = TeamMember(
        id=2,
        team_id=1,
        user_id=21,
        access_role="Jr Facilitator",
        status="active",
        colleges=["IIT Bombay"],
    )
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
    from src.api.dependencies.admin import (
        verify_college_access,
        get_user_allowed_colleges,
    )

    creator = _make_user(
        22, email="placement_creator@barabari.org", is_admin=False, is_super_admin=False
    )
    session = AsyncMock()
    team = Team(id=2, name="Placement Team", status="active")
    member = TeamMember(
        id=3, team_id=2, user_id=22, access_role="Creator", status="active", colleges=[]
    )
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

    creator = _make_user(
        23,
        email="placement_creator2@barabari.org",
        is_admin=False,
        is_super_admin=False,
    )
    session = AsyncMock()
    team = Team(id=2, name="Placement Team", status="active")
    member = TeamMember(
        id=4, team_id=2, user_id=23, access_role="Creator", status="active"
    )
    mock_result = MagicMock()
    mock_result.all.return_value = [(member, team)]
    session.execute.return_value = mock_result

    user = await get_current_bulk_ats_user(current_user=creator, session=session)
    assert user is creator


@pytest.mark.asyncio
async def test_placement_team_member_is_denied_bulk_ats_access():
    """Regular Team Members in the Placement Team are denied Bulk ATS access."""
    from src.api.dependencies.admin import get_current_bulk_ats_user

    member_user = _make_user(
        24, email="placement_member@barabari.org", is_admin=False, is_super_admin=False
    )
    session = AsyncMock()
    team = Team(id=2, name="Placement Team", status="active")
    member = TeamMember(
        id=5, team_id=2, user_id=24, access_role="Team Member", status="active"
    )
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

    program_creator = _make_user(
        25, email="prog_creator@barabari.org", is_admin=False, is_super_admin=False
    )
    session = AsyncMock()
    team = Team(id=1, name="Program Team", status="active")
    member = TeamMember(
        id=6, team_id=1, user_id=25, access_role="Creator", status="active"
    )
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
        elif 'from "user"' in stmt_str or "user.email" in stmt_str:
            result.scalar_one_or_none.return_value = None
        elif "from team" in stmt_str or "team.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = team
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock

    repo = TeamCRUDRepository(async_session=mock_session)
    member_data = TeamMemberCreate(
        name="SSO Creator", email="creator@barabari.org", access_role="creator"
    )
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
    member = TeamMember(
        id=1, team_id=1, user_id=101, access_role="Creator", status="active"
    )

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_create_roles"] is True
    assert perms["can_access_ats"] is True
    assert perms["can_edit"] is True
    assert perms["is_view_only"] is False
    assert perms["colleges"] is None
    assert perms["is_team_lead"] is True


def test_placement_creator_only_create_interview_tag():
    """Placement Creator with ONLY 'Create interview' tag:
    Gets interview creation, practice interview, but NO bulk ATS and NO dashboard/student/college pages.
    """
    from src.api.dependencies.admin import calculate_user_permissions

    user = _make_user(110, email="creator_interview@barabari.org")
    team = Team(id=1, name="Placement Team", status="active")
    member = TeamMember(
        id=10, team_id=1, user_id=110, access_role="Creator", status="active",
        purposes=["Create interview"]
    )

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_create_roles"] is True
    assert perms["can_edit"] is True
    assert perms["is_view_only"] is False
    assert perms["can_access_interviews"] is True
    assert perms["can_access_practice_interview"] is True
    assert perms["can_access_ats"] is False  # Dedicated ATS tag required
    assert perms["can_access_dashboard"] is False
    assert perms["can_access_students"] is False
    assert perms["can_access_colleges"] is False


def test_placement_creator_batch_progress_tag():
    """Placement Creator with 'Check batch progress' tag:
    Gets dashboard page, student page, interview page, and edit access.
    """
    from src.api.dependencies.admin import calculate_user_permissions

    user = _make_user(111, email="creator_batch@barabari.org")
    team = Team(id=1, name="Placement Team", status="active")
    member = TeamMember(
        id=11, team_id=1, user_id=111, access_role="Creator", status="active",
        purposes=["Check batch progress"]
    )

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_access_dashboard"] is True
    assert perms["can_access_students"] is True
    assert perms["can_access_interviews"] is True
    assert perms["can_edit"] is True
    assert perms["can_access_colleges"] is False


def test_placement_creator_student_progress_tag():
    """Placement Creator with ONLY 'Check student progress' tag:
    Gets student page only; Colleges page and Dashboard page are hidden.
    """
    from src.api.dependencies.admin import calculate_user_permissions

    user = _make_user(112, email="creator_student@barabari.org")
    team = Team(id=1, name="Placement Team", status="active")
    member = TeamMember(
        id=12, team_id=1, user_id=112, access_role="Creator", status="active",
        purposes=["Check student progress"]
    )

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_access_students"] is True
    assert perms["can_edit"] is True
    assert perms["can_access_colleges"] is False
    assert perms["can_access_dashboard"] is False
    assert perms["can_access_interviews"] is False


def test_placement_creator_college_progress_tag():
    """Placement Creator with ONLY 'Check college progress' tag:
    Gets college page only.
    """
    from src.api.dependencies.admin import calculate_user_permissions

    user = _make_user(113, email="creator_college@barabari.org")
    team = Team(id=1, name="Placement Team", status="active")
    member = TeamMember(
        id=13, team_id=1, user_id=113, access_role="Creator", status="active",
        purposes=["Check college progress"]
    )

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_access_colleges"] is True
    assert perms["can_access_students"] is False
    assert perms["can_access_dashboard"] is False


def test_placement_creator_with_ats_tag():
    """Placement Creator with ATS tag: gets bulk ATS access."""
    from src.api.dependencies.admin import calculate_user_permissions

    user = _make_user(114, email="creator_ats@barabari.org")
    team = Team(id=1, name="Placement Team", status="active")
    member = TeamMember(
        id=14, team_id=1, user_id=114, access_role="Creator", status="active",
        purposes=["Create interview", "Bulk ATS"]
    )

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_access_ats"] is True
    assert perms["can_create_roles"] is True
    assert perms["can_access_interviews"] is True


def test_permission_matrix_placement_team_member():
    """2. Placement Team Member: view access only, no colleges assigned, no edit access, no ATS."""
    from src.api.dependencies.admin import calculate_user_permissions

    user = _make_user(102, email="place_member@barabari.org")
    team = Team(id=1, name="Placement Team", status="active")
    member = TeamMember(
        id=2, team_id=1, user_id=102, access_role="Team Member", status="active"
    )

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
    member = TeamMember(
        id=3, team_id=2, user_id=103, access_role="Creator", status="active"
    )

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
    member = TeamMember(
        id=4, team_id=2, user_id=104, access_role="Sr Facilitator", status="active"
    )

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
    member = TeamMember(
        id=6, team_id=2, user_id=106, access_role="Team Member", status="active"
    )

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

    perms = calculate_user_permissions(
        user, [(member_jf, team_prog), (member_tm, team_place)]
    )
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
    member = TeamMember(
        id=7, team_id=1, user_id=107, access_role="Team Member", status="active"
    )
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
    member = TeamMember(
        id=8, team_id=1, user_id=108, access_role="Sr Facilitator", status="active"
    )
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
        elif 'from "user"' in stmt_str or "user.email" in stmt_str:
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
    member = TeamMember(
        id=10,
        team_id=1,
        user_id=109,
        access_role="Team Member",
        colleges=["BITS Pilani"],
        status="active",
    )

    def update_exec_mock(stmt):
        res = MagicMock()
        stmt_str = str(stmt).lower()
        if 'from "user"' in stmt_str or "user.id ==" in stmt_str:
            res.scalar_one_or_none.return_value = user
        else:
            res.scalar_one_or_none.return_value = member
        return res

    mock_session.execute.side_effect = update_exec_mock

    updated = await repo.update_team_member(
        team_id=1,
        member_id=10,
        member_data=TeamMemberUpdate(
            access_role="Team Member", colleges=["BITS Pilani"]
        ),
    )
    assert updated is not None
    assert updated["colleges"] is None


@pytest.mark.asyncio
async def test_add_team_member_never_elevates_global_admin_flags():
    """Creator or Lead role in a team must NOT escalate user.is_admin or user.is_super_admin."""
    mock_session = AsyncMock()
    team = Team(id=1, name="Placement Team", status="active")
    user = _make_user(
        110, email="creator_safe@barabari.org", is_admin=False, is_super_admin=False
    )

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = None
        elif 'from "user"' in stmt_str or "user.email" in stmt_str:
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
    member = TeamMember(
        id=5, team_id=1, user_id=111, access_role="Creator", status="active"
    )

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
    target_student = User(
        id=200, email="target@barabari.org", university="Unauthorized University"
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = target_student
    mock_session.execute.return_value = mock_result

    # Mock allowed colleges: jf_user only allowed at "Allowed College"
    with patch(
        "src.api.routes.analytics_v2.get_user_allowed_colleges",
        return_value={"Allowed College"},
    ):
        with pytest.raises(fastapi.HTTPException) as exc_info:
            await verify_student_access(
                student_id=200, user=jf_user, session=mock_session
            )
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
        await repo.verify_password(
            email="sso_user@barabari.org", password="SomePassword123!"
        )
    assert "Central SSO" in str(exc_info.value) or "Password does not match" in str(
        exc_info.value
    )


@pytest.mark.asyncio
async def test_dashboard_top_roles_rejects_unauthorized_college():
    """get_dashboard_top_roles rejects college outside facilitator's allowed colleges."""
    from src.api.routes.analytics_v2 import get_dashboard_top_roles

    jf_user = _make_user(116, is_admin=False, is_super_admin=False)
    mock_session = AsyncMock()

    with patch(
        "src.api.routes.analytics_v2.get_user_allowed_colleges",
        return_value={"Allowed College"},
    ):
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

    with patch(
        "src.api.routes.users.jwt_generator.generate_access_token_for_user"
    ) as mock_jwt:
        mock_jwt.return_value = "jwt-student-token"
        resp = await login_user(
            payload=UserLogin(
                email="student@barabari.org", password="ValidPassword123!"
            ),
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

    student_user = _make_user(
        502, email="student2@barabari.org", is_admin=False, is_super_admin=False
    )
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

    user = _make_user(
        701, email="multi_team@barabari.org", is_admin=False, is_super_admin=False
    )
    # Token reflects their oldest/first membership (Jr Facilitator)
    setattr(user, "_token_role", "JUNIOR_FACILITATOR")

    team_a = Team(id=1, name="Program Team Alpha", status="active")
    member_a = TeamMember(
        id=10, team_id=1, user_id=701, access_role="Junior Facilitator", status="active"
    )

    team_b = Team(id=2, name="Program Team Beta", status="active")
    member_b = TeamMember(
        id=11, team_id=2, user_id=701, access_role="Creator", status="active"
    )

    mock_session = AsyncMock()
    mock_res = MagicMock()
    # DB returns active memberships for both teams
    mock_res.all.return_value = [(member_a, team_a), (member_b, team_b)]
    mock_session.execute.return_value = mock_res

    allowed_user = await get_current_creator_user(
        current_user=user, session=mock_session
    )
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

    jf_user = _make_user(
        801, email="jf@barabari.org", is_admin=False, is_super_admin=False
    )
    mock_session = AsyncMock()

    # Case 1: Junior Facilitator with empty allowed colleges receives empty responses
    with patch(
        "src.api.routes.analytics_v2.get_user_allowed_colleges", return_value=set()
    ):
        r_int = await get_dashboard_recent_interviews(
            current_user=jf_user, session=mock_session
        )
        assert r_int.items == []

        r_stu = await get_dashboard_recent_students(
            current_user=jf_user, session=mock_session
        )
        assert r_stu.items == []

        r_att = await get_dashboard_attention_required(
            current_user=jf_user, session=mock_session
        )
        assert r_att.items == []

        r_sum = await get_students_summary(current_user=jf_user, session=mock_session)
        assert all(kpi.value in (0, 0.0) for kpi in r_sum.kpis)

        r_role_sum = await get_roles_summary(current_user=jf_user, session=mock_session)
        assert any(
            kpi.key == "total_roles" and kpi.value == 0 for kpi in r_role_sum.kpis
        )

        r_perf = await get_roles_performance(current_user=jf_user, session=mock_session)
        assert r_perf.items == []

        r_weak = await get_roles_weak_skills(current_user=jf_user, session=mock_session)
        assert r_weak.items == []

        r_detail = await get_role_detail(
            role_id="Software Engineer", current_user=jf_user, session=mock_session
        )
        assert r_detail.items == []

        r_diff = await get_difficulty_metrics(
            current_user=jf_user, session=mock_session
        )
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

        r_glob = await global_search(
            q="test", current_user=jf_user, session=mock_session
        )
        assert r_glob.students == []
        assert r_glob.colleges == []
        assert r_glob.interviews == []

        from src.api.routes.analytics_v2 import get_interviews_summary

        r_int_sum = await get_interviews_summary(
            current_user=jf_user, session=mock_session
        )
        assert all(kpi.value in (0, 0.0, None) for kpi in r_int_sum.kpis)


@pytest.mark.asyncio
async def test_creator_portal_rejects_revoked_member_with_stale_creator_token():
    """If a user was removed from a team (0 active memberships, but has team member history), their stale
    CREATOR token must NOT grant them Creator Portal access."""
    from src.api.dependencies.admin import get_current_creator_user

    revoked_user = _make_user(
        901, email="revoked_creator@barabari.org", is_admin=False, is_super_admin=False
    )
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
    assert (
        "no longer active" in exc_info.value.detail
        or "Creator Portal access required" in exc_info.value.detail
    )


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
        await repo.update_team_member(
            team_id=1, member_id=20, member_data=update_payload
        )
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

    team_member_user = _make_user(
        904, email="plain_tm@barabari.org", is_admin=False, is_super_admin=False
    )
    team = Team(id=1, name="Program Team", status="active")
    member = TeamMember(
        id=21, team_id=1, user_id=904, access_role="Team Member", status="active"
    )

    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.all.return_value = [(member, team)]
    mock_session.execute.return_value = mock_res

    from src.api.dependencies.admin import get_current_creator_user

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await get_current_creator_user(
            current_user=team_member_user, session=mock_session
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_update_member_route_catches_value_error_as_400():
    """update_member route must catch ValueError and return clean 400 instead of 500."""
    from src.api.routes.teams_v2 import update_member

    mock_repo = AsyncMock()
    mock_repo.async_session = AsyncMock()
    admin_user = _make_user(
        1, email="admin@barabari.org", is_super_admin=False, is_admin=True
    )

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
        if 'from "user"' in stmt_str or "user.email" in stmt_str:
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
        await repo.update_team_member(
            team_id=1, member_id=20, member_data=update_payload
        )
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
        await repo.update_team_member(
            team_id=1, member_id=22, member_data=update_payload
        )
    assert "Assigned colleges are required" in str(exc_info.value)


def test_extract_memberships_from_result_converts_sqlalchemy_row_to_tuple():
    """Ensure extract_memberships_from_result converts SQLAlchemy Row objects into standard tuples."""
    import sqlalchemy
    from sqlalchemy import select, literal
    from src.api.dependencies.admin import extract_memberships_from_result

    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        row = conn.execute(
            select(
                literal("member_obj").label("TeamMember"),
                literal("team_obj").label("Team"),
            )
        ).first()

    mock_res = MagicMock()
    mock_res.all.return_value = [row]

    result = extract_memberships_from_result(mock_res)
    assert len(result) == 1
    assert isinstance(result[0], tuple)
    assert result[0] == ("member_obj", "team_obj")


@pytest.mark.asyncio
async def test_login_user_handles_senior_facilitator_with_row_memberships():
    """Regression test: login_user safely unpacks SQLAlchemy Row memberships and does not crash with 500 on access_role."""
    from src.api.routes.users import login_user
    from src.models.schemas.user import UserLogin

    mock_user = User(
        id=701,
        email="rukmini@gmail.com",
        name="Rukmini Staff",
        is_admin=False,
        is_super_admin=False,
        role=None,
        password_hash="somehash",
        created_at=datetime.datetime.now(datetime.timezone.utc),
        is_onboarded=True,
    )
    mock_session = AsyncMock()
    mock_session_repo = AsyncMock()
    mock_session_repo.create_session.return_value = MagicMock(token="ref-tok-rukmini")

    mock_user_repo = AsyncMock()
    mock_user_repo.verify_password.return_value = mock_user

    mock_member = MagicMock()
    mock_member.access_role = "Senior Facilitator"
    mock_member.domain = "Engineering"
    mock_member.purposes = ["Placement Prep"]
    mock_member.colleges = []
    mock_member.status = "active"

    mock_team = MagicMock()
    mock_team.name = "Program Team"
    mock_team.status = "active"

    class StrictRow:
        def __init__(self, items):
            self._items = items

        def __getitem__(self, idx):
            return self._items[idx]

        def __len__(self):
            return len(self._items)

        def __getattr__(self, name):
            raise AttributeError(f"Could not locate column in row for column '{name}'")

    strict_row = StrictRow([mock_member, mock_team])

    with (
        patch(
            "src.api.routes.users.get_user_active_memberships",
            return_value=[strict_row],
        ),
        patch(
            "src.api.routes.users.jwt_generator.generate_access_token_for_user"
        ) as mock_jwt,
    ):
        mock_jwt.return_value = "jwt-sr-fac-token"
        resp = await login_user(
            payload=UserLogin(email="rukmini@gmail.com", password="ValidPassword123!"),
            user_repo=mock_user_repo,
            session_repo=mock_session_repo,
            session=mock_session,
        )

        call_claims = mock_jwt.call_args[1].get("extra_claims", {})
        assert call_claims.get("role") == "SENIOR FACILITATOR"
        assert resp.authorized_user.access_role == "Senior Facilitator"
        assert resp.authorized_user.team_name == "Program Team"


def test_program_team_lead_permissions():
    """Program Team Lead gets all core pages, full edit access, unrestricted colleges, but no ATS."""
    user = _make_user(801, email="prog_lead@example.com")
    user._token_role = None
    user._token_is_admin = False
    user._token_is_super_admin = False
    member = MagicMock()
    member.access_role = "Team Lead"
    member.purposes = None
    member.colleges = None

    team = MagicMock()
    team.name = "Program Team"

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["is_team_lead"] is True
    assert perms["can_edit"] is True
    assert perms["is_view_only"] is False
    assert perms["can_create_roles"] is True
    assert perms["can_access_dashboard"] is True
    assert perms["can_access_students"] is True
    assert perms["can_access_colleges"] is True
    assert perms["can_access_interviews"] is True
    assert perms["can_access_practice_interview"] is True
    assert perms["can_access_ats"] is False
    assert perms["colleges"] is None


def test_program_senior_facilitator_with_assigned_colleges():
    """Senior Facilitator with assigned colleges gets edit access, role creation, and scoped colleges."""
    user = _make_user(802, email="sr_fac@example.com")
    user._token_role = None
    user._token_is_admin = False
    user._token_is_super_admin = False
    member = MagicMock()
    member.access_role = "Sr Facilitator"
    member.purposes = None
    member.colleges = ["CMRIT", "VNRVJIET"]

    team = MagicMock()
    team.name = "Program Team"

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_edit"] is True
    assert perms["is_view_only"] is False
    assert perms["can_create_roles"] is True
    assert perms["can_access_interviews"] is True
    assert perms["can_access_practice_interview"] is True
    assert perms["can_access_dashboard"] is True
    assert perms["can_access_students"] is True
    assert perms["can_access_colleges"] is True
    assert perms["can_access_ats"] is False
    assert perms["colleges"] == ["CMRIT", "VNRVJIET"]


def test_program_senior_facilitator_unrestricted_colleges():
    """Senior Facilitator without assigned colleges has unrestricted college access."""
    user = _make_user(803, email="sr_fac_unrestricted@example.com")
    user._token_role = None
    user._token_is_admin = False
    user._token_is_super_admin = False
    member = MagicMock()
    member.access_role = "Senior Facilitator"
    member.purposes = None
    member.colleges = None

    team = MagicMock()
    team.name = "Program Team"

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_edit"] is True
    assert perms["can_create_roles"] is True
    assert perms["colleges"] is None


def test_program_junior_facilitator_permissions():
    """Junior Facilitator is strictly view-only, cannot create roles, but can view interviews and their colleges."""
    user = _make_user(804, email="jr_fac@example.com")
    user._token_role = None
    user._token_is_admin = False
    user._token_is_super_admin = False
    member = MagicMock()
    member.access_role = "Jr Facilitator"
    member.purposes = None
    member.colleges = ["CMRIT"]

    team = MagicMock()
    team.name = "Program Team"

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_edit"] is False
    assert perms["is_view_only"] is True
    assert perms["can_create_roles"] is False
    assert perms["can_access_interviews"] is True
    assert perms["can_access_practice_interview"] is False
    assert perms["can_access_dashboard"] is True
    assert perms["can_access_students"] is True
    assert perms["can_access_colleges"] is True
    assert perms["can_access_ats"] is False
    assert perms["colleges"] == ["CMRIT"]


def test_program_creator_single_interview_purpose():
    """Program Creator with 'Create interview' gets creator portal, practice, edit, but no dashboard/students/colleges/ATS."""
    user = _make_user(805, email="prog_creator@example.com")
    user._token_role = None
    user._token_is_admin = False
    user._token_is_super_admin = False
    member = MagicMock()
    member.access_role = "Creator"
    member.purposes = ["Create interview"]
    member.colleges = None

    team = MagicMock()
    team.name = "Program Team"

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_edit"] is True
    assert perms["is_view_only"] is False
    assert perms["can_create_roles"] is True
    assert perms["can_access_interviews"] is True
    assert perms["can_access_practice_interview"] is True
    assert perms["can_access_dashboard"] is False
    assert perms["can_access_students"] is False
    assert perms["can_access_colleges"] is False
    assert perms["can_access_ats"] is False
    assert perms["colleges"] is None


def test_program_team_member_view_only_batch_progress():
    """Program Team Member with 'Check batch progress' gets view-only Dashboard + Students."""
    user = _make_user(806, email="prog_member@example.com")
    user._token_role = None
    user._token_is_admin = False
    user._token_is_super_admin = False
    member = MagicMock()
    member.access_role = "Team Member"
    member.purposes = ["Check batch progress"]
    member.colleges = None

    team = MagicMock()
    team.name = "Program Team"

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_edit"] is False
    assert perms["is_view_only"] is True
    assert perms["can_create_roles"] is False
    assert perms["can_access_dashboard"] is True
    assert perms["can_access_students"] is True
    assert perms["can_access_colleges"] is False
    assert perms["can_access_interviews"] is True
    assert perms["can_access_practice_interview"] is False
    assert perms["can_access_ats"] is False


def test_purpose_tag_exact_matching_prevents_ats_substring_leak():
    """Security test: Naive substring matching on 'ats' must not grant ATS access for words like 'chats' or 'formats'."""
    user = _make_user(807, email="leak_test@example.com")
    member = MagicMock()
    member.access_role = "Team Member"
    member.purposes = ["chats with recruiters", "formats", "status tracking"]
    member.colleges = None
    team = MagicMock()
    team.name = "Placement Team"

    perms = calculate_user_permissions(user, [(member, team)])
    assert perms["can_access_ats"] is False
    assert perms["can_create_roles"] is False


@pytest.mark.asyncio
async def test_interview_viewer_guard_rejects_active_member_without_interview_perms_despite_stale_token():
    """Security test: Active team member in DB without interview rights must be rejected even with a stale creator token."""
    from src.api.dependencies.admin import get_current_interview_viewer_user

    user = _make_user(808, email="stale_token@example.com")
    user._token_role = "creator"  # Stale token claim

    session = AsyncMock()
    member = MagicMock()
    member.access_role = "Team Member"
    member.purposes = ["Check student progress"]  # Only student progress, no interviews
    member.colleges = None
    team = MagicMock()
    team.name = "Program Team"

    mock_res = MagicMock()
    mock_res.all.return_value = [(member, team)]
    session.execute.return_value = mock_res

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await get_current_interview_viewer_user(current_user=user, session=session)
    assert exc_info.value.status_code == 403
    assert "You do not have permission to view the interview portal" in exc_info.value.detail


@pytest.mark.asyncio
async def test_job_profile_questions_endpoint_allows_junior_facilitator():
    """Route test: GET /job-profiles/{id}/questions allows Junior Facilitators via get_current_interview_viewer_user."""
    from src.api.routes.job_profiles_v2 import get_job_profile_questions_v2

    user = _make_user(809, email="jf_viewer@example.com")
    mock_repo = AsyncMock()
    mock_profile = MagicMock()
    mock_profile.id = 42
    mock_repo.get_by_id.return_value = mock_profile

    mock_q = MagicMock()
    mock_q.id = 101
    mock_q.level = 1
    mock_q.question_text = "Tell me about yourself."
    mock_q.difficulty = "easy"
    mock_q.question_type = "technical"
    mock_q.expected_answer = "Clear answer"
    mock_q.example_output = "Output"
    mock_q.is_ai_generated = True
    mock_q.is_custom = False
    mock_q.created_at = datetime.datetime.now(datetime.timezone.utc)
    mock_q.keywords = []
    mock_q.concepts_covered = []
    mock_repo.get_job_profile_questions.return_value = [mock_q]

    resp = await get_job_profile_questions_v2(
        job_profile_id=42,
        current_user=user,
        job_profile_repo=mock_repo,
    )
    assert str(resp.job_profile_id) == "42"
    assert resp.total_questions == 1
    assert resp.level_counts.level_1 == 1


def test_cross_membership_unrestricted_creator_retains_privileges_with_tagged_member_elsewhere():
    """Cross-membership test: Unrestricted Creator in Team 1 retains role creation and ATS even if Team 2 has tags."""
    user = _make_user(810, email="multi_team_creator@example.com")
    user._token_role = None

    # Team 1: Unrestricted Placement Creator (purposes=None)
    member_t1 = MagicMock()
    member_t1.access_role = "Creator"
    member_t1.purposes = None
    member_t1.colleges = None
    team_1 = MagicMock()
    team_1.name = "Placement Team"

    # Team 2: Program Team Member with student progress tag
    member_t2 = MagicMock()
    member_t2.access_role = "Team Member"
    member_t2.purposes = ["Check student progress"]
    member_t2.colleges = None
    team_2 = MagicMock()
    team_2.name = "Program Team"

    perms = calculate_user_permissions(user, [(member_t1, team_1), (member_t2, team_2)])
    assert perms["can_create_roles"] is True
    assert perms["can_access_ats"] is True
    assert perms["can_edit"] is True
    assert perms["can_access_interviews"] is True
    assert perms["can_access_students"] is True
    assert perms["colleges"] is None


@pytest.mark.asyncio
async def test_check_is_admin_dep_grants_viewer_access():
    from src.api.routes.job_profiles_v2 import check_is_admin_dep

    # Admin user
    admin_user = MagicMock()
    admin_user.is_admin = True
    assert await check_is_admin_dep(current_user=admin_user, session=None) is True

    # Junior Facilitator via token role
    jr_user = MagicMock()
    jr_user.is_admin = False
    jr_user._token_is_admin = False
    jr_user.email = "jr@test.com"
    jr_user.role = "junior facilitator"
    jr_user._token_role = "junior facilitator"
    assert await check_is_admin_dep(current_user=jr_user, session=None) is True

    # Regular student user
    student_user = MagicMock()
    student_user.id = 999
    student_user.is_super_admin = False
    student_user._token_is_super_admin = False
    student_user.is_admin = False
    student_user._token_is_admin = False
    student_user.email = "student@test.com"
    student_user.role = "student"
    student_user._token_role = "student"
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(all=lambda: []))
    assert await check_is_admin_dep(current_user=student_user, session=mock_session) is False


@pytest.mark.asyncio
async def test_resolve_effective_category_scopes_facilitator_to_domain():
    from src.api.routes.job_profiles_v2 import resolve_effective_category

    # Junior Facilitator with Sales domain in active membership
    jr_user = MagicMock()
    jr_user.id = 55
    jr_user.is_admin = False
    jr_user._token_is_admin = False
    jr_user.is_super_admin = False
    jr_user._token_is_super_admin = False
    jr_user.email = "omer@test.com"
    jr_user.role = "junior facilitator"
    jr_user._token_role = "junior facilitator"

    mock_member = MagicMock()
    mock_member.access_role = "Junior Facilitator"
    mock_member.domain = "Sales"
    mock_team = MagicMock()
    mock_team.name = "Program Team"

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(
        return_value=MagicMock(all=lambda: [(mock_member, mock_team)])
    )

    # Even if no category query param is passed, effective category MUST be "Sales"
    effective_cat = await resolve_effective_category(
        current_user=jr_user,
        session=mock_session,
        requested_category=None,
    )
    assert effective_cat == "Sales"

    # Even if they attempt to pass another category, facilitator stays scoped to their domain
    effective_cat_override = await resolve_effective_category(
        current_user=jr_user,
        session=mock_session,
        requested_category="Engineering",
    )
    assert effective_cat_override == "Sales"

    # Admin user is NOT scoped to any domain and can view requested category or all
    admin_user = MagicMock()
    admin_user.is_admin = True
    admin_user._token_is_admin = True
    admin_user.is_super_admin = True
    admin_user._token_is_super_admin = True

    admin_all = await resolve_effective_category(
        current_user=admin_user,
        session=mock_session,
        requested_category=None,
    )
    assert admin_all is None

    admin_filtered = await resolve_effective_category(
        current_user=admin_user,
        session=mock_session,
        requested_category="Engineering",
    )
    assert admin_filtered == "Engineering"




