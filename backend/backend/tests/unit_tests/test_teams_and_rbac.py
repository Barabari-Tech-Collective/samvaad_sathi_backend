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
    assert update.email is None


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
async def test_update_team_member_does_not_mutate_credentials():
    """Security: Team Lead updating member metadata cannot alter user's email or password."""
    mock_session = AsyncMock()
    member = TeamMember(id=5, team_id=1, user_id=10, status="active", access_role="member")
    user = User(id=10, email="target@barabari.org", password_hash="secure_user_pw")

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
        access_role="lead",
        domain="Engineering",
        password="malicious_overwrite_pw",
        email="attacker@takeover.org",
    )
    res = await repo.update_team_member(team_id=1, member_id=5, member_data=update_data)

    assert member.access_role == "lead"
    assert member.domain == "Engineering"
    assert user.password_hash == "secure_user_pw"
    assert user.email == "target@barabari.org"
