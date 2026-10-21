import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import fastapi

from src.services.central_auth_service import (
    provision_or_invite_staff,
    CentralAuthProvisioningError,
    CentralAuthReconciliationError,
)
from src.repository.crud.team_v2 import TeamCRUDRepository
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.models.schemas.teams_v2 import TeamMemberCreate


# ---------------------------------------------------------------------------
# Unit tests for provision_or_invite_staff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_or_invite_staff_missing_base_url_raises_error(monkeypatch):
    from src.config.manager import settings
    monkeypatch.setattr(settings, "AUTH_SERVICE_BASE_URL", "")
    monkeypatch.setattr(settings, "CENTRAL_PROFILE_API_KEY", "secret-key")

    with pytest.raises(CentralAuthProvisioningError, match="missing AUTH_SERVICE_BASE_URL"):
        await provision_or_invite_staff(email="test@barabari.org", name="Test User")


@pytest.mark.asyncio
async def test_provision_or_invite_staff_missing_api_key_raises_error(monkeypatch):
    from src.config.manager import settings
    monkeypatch.setattr(settings, "AUTH_SERVICE_BASE_URL", "https://auth.barabari.org")
    monkeypatch.setattr(settings, "CENTRAL_PROFILE_API_KEY", "")

    with pytest.raises(CentralAuthProvisioningError, match="missing AUTH_SERVICE_BASE_URL or CENTRAL_PROFILE_API_KEY"):
        await provision_or_invite_staff(email="test@barabari.org", name="Test User")


@pytest.mark.asyncio
async def test_provision_or_invite_staff_http_transport_error_raises_error(monkeypatch):
    from src.config.manager import settings
    monkeypatch.setattr(settings, "AUTH_SERVICE_BASE_URL", "https://auth.barabari.org")
    monkeypatch.setattr(settings, "CENTRAL_PROFILE_API_KEY", "secret-key")

    with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("Connection refused")):
        with pytest.raises(CentralAuthProvisioningError, match="unreachable"):
            await provision_or_invite_staff(email="test@barabari.org", name="Test User")


@pytest.mark.asyncio
async def test_provision_or_invite_staff_rejection_raises_error(monkeypatch):
    from src.config.manager import settings
    monkeypatch.setattr(settings, "AUTH_SERVICE_BASE_URL", "https://auth.barabari.org")
    monkeypatch.setattr(settings, "CENTRAL_PROFILE_API_KEY", "secret-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = {"isSuccess": False, "errorMessage": "Invalid staff role"}

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        with pytest.raises(CentralAuthProvisioningError, match="Invalid staff role"):
            await provision_or_invite_staff(email="test@barabari.org", name="Test User")


@pytest.mark.asyncio
async def test_provision_or_invite_staff_missing_central_user_id_raises_error(monkeypatch):
    from src.config.manager import settings
    monkeypatch.setattr(settings, "AUTH_SERVICE_BASE_URL", "https://auth.barabari.org")
    monkeypatch.setattr(settings, "CENTRAL_PROFILE_API_KEY", "secret-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = {"isSuccess": True, "data": {}}

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        with pytest.raises(CentralAuthProvisioningError, match="did not return a centralUserId"):
            await provision_or_invite_staff(email="test@barabari.org", name="Test User")


@pytest.mark.asyncio
async def test_provision_or_invite_staff_success(monkeypatch):
    from src.config.manager import settings
    monkeypatch.setattr(settings, "AUTH_SERVICE_BASE_URL", "https://auth.barabari.org")
    monkeypatch.setattr(settings, "CENTRAL_PROFILE_API_KEY", "secret-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = {
        "isSuccess": True,
        "data": {"centralUserId": "central-uuid-999", "isNewUser": True},
    }

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        cid = await provision_or_invite_staff(email="test@barabari.org", name="Test User")
        assert cid == "central-uuid-999"


# ---------------------------------------------------------------------------
# Integration tests in TeamCRUDRepository.add_team_member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_team_member_aborts_on_provisioning_error():
    """Verify that when Central Auth provisioning fails, no user or membership is committed."""
    mock_session = AsyncMock()
    team = Team(id=1, name="Core", status="active")

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "from team" in stmt_str or "team.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = team
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock
    repo = TeamCRUDRepository(async_session=mock_session)

    member_data = TeamMemberCreate(name="New Staff", email="newstaff@barabari.org", access_role="creator")

    with patch(
        "src.repository.crud.team_v2.provision_or_invite_staff",
        new=AsyncMock(side_effect=CentralAuthProvisioningError("Central Auth unreachable")),
    ):
        with pytest.raises(CentralAuthProvisioningError, match="Central Auth unreachable"):
            await repo.add_team_member(team_id=1, member_data=member_data)

    # Ensure no local entities were added to the session
    assert mock_session.add.call_count == 0
    assert mock_session.commit.call_count == 0


@pytest.mark.asyncio
async def test_add_team_member_raises_reconciliation_conflict_for_mismatched_student_id():
    """When a local user already exists with a different central ID, raise CentralAuthReconciliationError."""
    mock_session = AsyncMock()
    team = Team(id=1, name="Core", status="active")
    existing_user = User(
        id=42,
        email="reconcile@barabari.org",
        student_id="existing-old-central-id",
        password_hash="",
    )

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = None
        elif "from team" in stmt_str or "team.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = team
        elif 'from "user"' in stmt_str or "user.email" in stmt_str:
            result.scalar_one_or_none.return_value = existing_user
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock
    repo = TeamCRUDRepository(async_session=mock_session)

    member_data = TeamMemberCreate(name="Reconcile", email="reconcile@barabari.org", access_role="lead")

    # Central Auth returns a DIFFERENT centralUserId
    with patch(
        "src.repository.crud.team_v2.provision_or_invite_staff",
        new=AsyncMock(return_value="new-different-central-id"),
    ):
        with pytest.raises(CentralAuthReconciliationError, match="Manual reconciliation required"):
            await repo.add_team_member(team_id=1, member_data=member_data)

    # Verify no mutation was made to TeamMember and commit was NOT called
    assert mock_session.add.call_count == 0
    assert mock_session.commit.call_count == 0


@pytest.mark.asyncio
async def test_add_team_member_binds_unbound_existing_user():
    """When a local user exists with student_id=None, bind it to central_user_id and succeed."""
    mock_session = AsyncMock()
    team = Team(id=1, name="Core", status="active")
    existing_unbound_user = User(
        id=43,
        email="unbound@barabari.org",
        student_id=None,
        password_hash="",
    )

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = None
        elif "from team" in stmt_str or "team.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = team
        elif 'from "user"' in stmt_str or "user.email" in stmt_str:
            result.scalar_one_or_none.return_value = existing_unbound_user
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock
    repo = TeamCRUDRepository(async_session=mock_session)

    member_data = TeamMemberCreate(name="Unbound", email="unbound@barabari.org", access_role="creator")

    with patch(
        "src.repository.crud.team_v2.provision_or_invite_staff",
        new=AsyncMock(return_value="newly-bound-central-id"),
    ):
        res = await repo.add_team_member(team_id=1, member_data=member_data)

    assert existing_unbound_user.student_id == "newly-bound-central-id"
    assert res["email"] == "unbound@barabari.org"
    assert mock_session.commit.call_count >= 1
