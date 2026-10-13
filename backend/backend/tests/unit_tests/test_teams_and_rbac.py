"""Unit tests for Teams management and Role-Based Access Control (RBAC).

Covers:
1. Super Admin and Team authorization dependency guards.
2. Team and TeamMember schema validation and serialization.
3. Route-level authorization gates ensuring endpoints cannot be accessed without
   the requisite privilege level.
"""

import datetime
from typing import cast
from unittest.mock import AsyncMock
import fastapi
import pydantic
import pytest

from src.api.dependencies.admin import (
    get_current_super_admin_user,
    is_super_admin_user,
    is_team_authorized_user,
)
from src.config.manager import settings
from src.models.db.user import User
from src.models.schemas.teams_v2 import (
    TeamCreate,
    TeamResponse,
    TeamMemberCreate,
    TeamMemberUpdate,
    TeamMemberResponse,
)


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


@pytest.mark.asyncio
async def test_is_team_authorized_user_grants_super_admin():
    super_admin = _make_user(1, is_super_admin=True)
    mock_session = AsyncMock()
    assert await is_team_authorized_user(super_admin, team_id=10, session=mock_session) is True
    # Should short-circuit without querying the database
    mock_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_is_team_authorized_user_grants_global_admin():
    global_admin = _make_user(2, is_admin=True)
    mock_session = AsyncMock()
    assert await is_team_authorized_user(global_admin, team_id=10, session=mock_session) is True
    mock_session.execute.assert_not_called()


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
    assert response.memberCount == 5
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


def test_admin_gated_routes_require_admin_dependency():
    from src.api.routes.teams_v2 import router

    admin_routes = {
        "teams:get_team_recycle_bin",
        "teams:get_active_members",
    }

    for route in router.routes:
        if getattr(route, "name", None) in admin_routes:
            dep_names = [
                getattr(dep.call, "__name__", "")
                for dep in getattr(route.dependant, "dependencies", []) or []
            ]
            assert "get_current_admin_user" in dep_names, (
                f"Route '{route.name}' must require get_current_admin_user"
            )
