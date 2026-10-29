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

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)) as mock_post:
        cid = await provision_or_invite_staff(
            email="test@barabari.org",
            name="Test User",
            password="TestPassword123",
        )
        assert cid == "central-uuid-999"
        mock_post.assert_awaited_once()
        _, kwargs = mock_post.await_args
        assert kwargs["json"]["password"] == "TestPassword123"


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


@pytest.mark.asyncio
async def test_add_team_member_forwards_explicit_password_for_existing_non_admin_user():
    """Verify that when an Admin explicitly provides a password for an existing user during team addition,
    the password is forwarded to Central Auth so the user is provisioned with an active bcrypt hash.
    """
    mock_session = AsyncMock()
    team = Team(id=1, name="Program Team", status="active")
    existing_user_with_pw = User(
        id=77,
        email="facilitator@barabari.org",
        student_id="existing-central-id",
        password_hash="old_test_hash",
        is_admin=False,
        is_super_admin=False,
    )

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = None
        elif "from team" in stmt_str or "team.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = team
        elif 'from "user"' in stmt_str or "user.email" in stmt_str:
            result.scalar_one_or_none.return_value = existing_user_with_pw
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock
    repo = TeamCRUDRepository(async_session=mock_session)

    member_data = TeamMemberCreate(
        name="Facilitator",
        email="facilitator@barabari.org",
        access_role="sr facilitator",
        password="ActivePassword123!",
    )

    with patch(
        "src.repository.crud.team_v2.provision_or_invite_staff",
        new=AsyncMock(return_value="existing-central-id"),
    ) as mock_prov:
        res = await repo.add_team_member(team_id=1, member_data=member_data)

    mock_prov.assert_awaited_once_with(
        email="facilitator@barabari.org",
        name="Facilitator",
        role="FACILITATOR",
        send_invite_email=True,
        password="ActivePassword123!",
    )
    assert res["email"] == "facilitator@barabari.org" 


@pytest.mark.asyncio
async def test_add_team_member_compensating_revocation_on_local_commit_failure():
    """Verify saga compensation: if local commit fails after central provisioning, revoke_provisioning is called."""
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
    mock_session.commit.side_effect = RuntimeError("Database disk full / transaction aborted")
    repo = TeamCRUDRepository(async_session=mock_session)

    member_data = TeamMemberCreate(name="Orphan Candidate", email="orphan@barabari.org", access_role="creator")

    from src.services.central_auth_service import ProvisionResult
    provision_res = ProvisionResult(central_user_id="central-orphan-123", is_new_user=True)

    with patch("src.repository.crud.team_v2.provision_or_invite_staff", new=AsyncMock(return_value=provision_res)), \
         patch("src.repository.crud.team_v2.revoke_provisioning", new=AsyncMock(return_value=True)) as mock_revoke:
        with pytest.raises(RuntimeError, match="Database disk full"):
            await repo.add_team_member(team_id=1, member_data=member_data)

        # Verify rollback was called and compensating revocation was executed
        assert mock_session.rollback.call_count >= 1
        mock_revoke.assert_awaited_once_with(
            email="orphan@barabari.org",
            is_new_user=True,
            central_user_id="central-orphan-123",
        )


@pytest.mark.asyncio
async def test_update_team_member_resets_password_with_canonical_role():
    """Verify that password reset derives canonical role from access_role (FACILITATOR for facilitator roles)."""
    from src.models.schemas.teams_v2 import TeamMemberUpdate
    mock_session = AsyncMock()
    member = TeamMember(id=5, team_id=1, user_id=10, access_role="junior_facilitator", status="active")
    user = User(id=10, email="fac@barabari.org", name="Facilitator", password_hash="oldhash", is_admin=False, is_super_admin=False)
    admin_caller = User(id=1, email="admin@barabari.org", is_admin=True, is_super_admin=False)

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

    update_data = TeamMemberUpdate(password="NewSecurePassword123!")

    with patch("src.repository.crud.team_v2.provision_or_invite_staff", new=AsyncMock()) as mock_prov:
        await repo.update_team_member(team_id=1, member_id=5, member_data=update_data, caller_user=admin_caller)

        mock_prov.assert_awaited_once_with(
            email="fac@barabari.org",
            name="Facilitator",
            role="FACILITATOR",
            send_invite_email=False,
            password="NewSecurePassword123!",
        )
        assert mock_session.commit.call_count >= 1
        assert user.password_hash != "oldhash"


@pytest.mark.asyncio
async def test_update_team_member_password_reset_aborts_on_central_failure_preventing_split_brain():
    """Verify fail-closed behavior: Central Auth outage prevents local password_hash commit."""
    from src.models.schemas.teams_v2 import TeamMemberUpdate
    mock_session = AsyncMock()
    member = TeamMember(id=5, team_id=1, user_id=10, access_role="member", status="active")
    user = User(id=10, email="member@barabari.org", name="Member", password_hash="original_hash", is_admin=False, is_super_admin=False)
    admin_caller = User(id=1, email="admin@barabari.org", is_admin=True, is_super_admin=False)

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

    update_data = TeamMemberUpdate(password="AttemptedNewPassword")

    with patch(
        "src.repository.crud.team_v2.provision_or_invite_staff",
        new=AsyncMock(side_effect=CentralAuthProvisioningError("Central Auth 503 Outage")),
    ):
        with pytest.raises(CentralAuthProvisioningError, match="Central Auth 503 Outage"):
            await repo.update_team_member(team_id=1, member_id=5, member_data=update_data, caller_user=admin_caller)

    # Local password hash must remain untouched, and transaction must not be committed
    assert user.password_hash == "original_hash"
    assert mock_session.commit.call_count == 0


@pytest.mark.asyncio
async def test_update_team_member_role_change_syncs_to_central_auth():
    """Verify that updating access_role (without password) syncs the new canonical role to Central Auth."""
    from src.models.schemas.teams_v2 import TeamMemberUpdate
    mock_session = AsyncMock()
    member = TeamMember(id=7, team_id=1, user_id=12, access_role="member", status="active")
    user = User(id=12, email="staff_to_fac@barabari.org", name="Promoted User", password_hash="existinghash", is_admin=False, is_super_admin=False)
    admin_caller = User(id=1, email="admin@barabari.org", is_admin=True, is_super_admin=False)

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

    update_data = TeamMemberUpdate(access_role="junior_facilitator", colleges=["Engineering College"])

    with patch("src.repository.crud.team_v2.provision_or_invite_staff", new=AsyncMock()) as mock_prov:
        await repo.update_team_member(team_id=1, member_id=7, member_data=update_data, caller_user=admin_caller)

        mock_prov.assert_awaited_once_with(
            email="staff_to_fac@barabari.org",
            name="Promoted User",
            role="FACILITATOR",
            send_invite_email=False,
            password=None,
        )
        assert mock_session.commit.call_count >= 1


# ---------------------------------------------------------------------------
# Unit tests for distributed transaction compensation & durable reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_provisioning_refuses_to_revoke_existing_users():
    """revoke_provisioning must refuse to revoke pre-existing entitlements when is_new_user=False."""
    from src.services.central_auth_service import revoke_provisioning
    with patch("httpx.AsyncClient.post", new=AsyncMock()) as mock_post:
        res = await revoke_provisioning(email="existing@barabari.org", is_new_user=False)
        assert res is False
        assert mock_post.call_count == 0


@pytest.mark.asyncio
async def test_add_team_member_does_not_revoke_entitlement_for_existing_user_on_local_commit_failure():
    """Verify that when local commit fails for an existing user (is_new_user=False), pre-existing entitlement is NOT revoked."""
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
    mock_session.commit.side_effect = RuntimeError("Database deadlock / disk error")
    repo = TeamCRUDRepository(async_session=mock_session)

    member_data = TeamMemberCreate(name="Existing Staff", email="existing@barabari.org", access_role="creator")

    from src.services.central_auth_service import ProvisionResult
    # Central Auth returned is_new_user=False (pre-existing user)
    provision_res = ProvisionResult(central_user_id="central-existing-999", is_new_user=False)

    with patch("src.repository.crud.team_v2.provision_or_invite_staff", new=AsyncMock(return_value=provision_res)), \
         patch("src.repository.crud.team_v2.revoke_provisioning", new=AsyncMock(return_value=True)) as mock_revoke:
        with pytest.raises(RuntimeError, match="Database deadlock"):
            await repo.add_team_member(team_id=1, member_data=member_data)

        # Verify rollback was called
        assert mock_session.rollback.call_count >= 1
        # CRITICAL: revoke_provisioning must NEVER be called for an existing user!
        assert mock_revoke.call_count == 0


@pytest.mark.asyncio
async def test_add_team_member_restores_role_for_existing_user_on_local_commit_failure():
    """Verify that when local commit fails for an existing user whose role was updated in Central Auth, role is compensated back."""
    mock_session = AsyncMock()
    team = Team(id=1, name="Core", status="active")
    existing_user = User(
        id=55,
        email="existing_member@barabari.org",
        student_id="central-existing-55",
        password_hash="hash",
    )
    # Existing soft-deleted or previous member with access_role='member' (STAFF)
    existing_member = TeamMember(
        id=101, team_id=1, user_id=55, access_role="member", status="deleted"
    )

    def execute_mock(stmt):
        result = MagicMock()
        stmt_str = str(stmt).lower()
        if "team_member" in stmt_str:
            result.scalar_one_or_none.return_value = existing_member
        elif "from team" in stmt_str or "team.id ==" in stmt_str:
            result.scalar_one_or_none.return_value = team
        elif 'from "user"' in stmt_str or "user.email" in stmt_str:
            result.scalar_one_or_none.return_value = existing_user
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = execute_mock
    mock_session.commit.side_effect = RuntimeError("Database constraint failure")
    repo = TeamCRUDRepository(async_session=mock_session)

    # Adding with access_role='junior_facilitator' (canonical FACILITATOR)
    member_data = TeamMemberCreate(
        name="Existing Member",
        email="existing_member@barabari.org",
        access_role="junior_facilitator",
        colleges=["Engineering College"],
    )

    from src.services.central_auth_service import ProvisionResult
    provision_res = ProvisionResult(central_user_id="central-existing-55", is_new_user=False)

    with patch("src.repository.crud.team_v2.provision_or_invite_staff", new=AsyncMock(return_value=provision_res)), \
         patch("src.repository.crud.team_v2.compensate_central_role", new=AsyncMock(return_value=True)) as mock_comp_role, \
         patch("src.repository.crud.team_v2.revoke_provisioning", new=AsyncMock(return_value=True)) as mock_revoke:
        with pytest.raises(RuntimeError, match="Database constraint failure"):
            await repo.add_team_member(team_id=1, member_data=member_data)

        assert mock_session.rollback.call_count >= 1
        # Entitlement must not be revoked
        assert mock_revoke.call_count == 0
        # Role must be compensated back to STAFF
        mock_comp_role.assert_awaited_once_with(
            email="existing_member@barabari.org",
            name=existing_user.name,
            previous_canonical_role="STAFF",
        )


@pytest.mark.asyncio
async def test_update_team_member_compensates_central_role_on_local_commit_failure():
    """
    Verify exact ordering for update_team_member:
    Central Auth succeeds in updating role -> local commit fails -> compensating role revert is executed in Central Auth.
    """
    from src.models.schemas.teams_v2 import TeamMemberUpdate
    mock_session = AsyncMock()
    # Member currently has access_role='member' (canonical role STAFF)
    member = TeamMember(id=5, team_id=1, user_id=10, access_role="member", status="active")
    user = User(id=10, email="fac_promote@barabari.org", name="Fac Promote", password_hash="hash", is_admin=False, is_super_admin=False)
    admin_caller = User(id=1, email="admin@barabari.org", is_admin=True, is_super_admin=False)

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
    # Simulate: Central Auth succeeds, but local commit fails afterward
    mock_session.commit.side_effect = RuntimeError("Database transaction aborted unexpectedly")
    repo = TeamCRUDRepository(async_session=mock_session)

    # Request updating to junior_facilitator (canonical role FACILITATOR)
    update_data = TeamMemberUpdate(access_role="junior_facilitator", colleges=["IIT Bombay"])

    with patch("src.repository.crud.team_v2.provision_or_invite_staff", new=AsyncMock()) as mock_prov, \
         patch("src.repository.crud.team_v2.compensate_central_role", new=AsyncMock(return_value=True)) as mock_comp_role:
        with pytest.raises(RuntimeError, match="Database transaction aborted unexpectedly"):
            await repo.update_team_member(team_id=1, member_id=5, member_data=update_data, caller_user=admin_caller)

        # 1. Central Auth was updated with new canonical role FACILITATOR
        mock_prov.assert_awaited_once_with(
            email="fac_promote@barabari.org",
            name="Fac Promote",
            role="FACILITATOR",
            send_invite_email=False,
            password=None,
        )
        # 2. Local session rollback was called
        assert mock_session.rollback.call_count >= 1
        # 3. Compensating call was executed to restore previous canonical role STAFF
        mock_comp_role.assert_awaited_once_with(
            email="fac_promote@barabari.org",
            name="Fac Promote",
            previous_canonical_role="STAFF",
        )


@pytest.mark.asyncio
async def test_update_team_member_durable_credential_reconciliation_on_local_commit_failure():
    """
    Verify exact ordering for credential reset in update_team_member:
    Central Auth succeeds in updating password -> local commit fails -> durable credential reconciliation is executed.
    """
    from src.models.schemas.teams_v2 import TeamMemberUpdate
    mock_session = AsyncMock()
    member = TeamMember(id=5, team_id=1, user_id=10, access_role="member", status="active")
    user = User(
        id=10,
        email="reset_target@barabari.org",
        name="Target User",
        student_id="central-uuid-10",
        password_hash="original_hash",
        is_admin=False,
        is_super_admin=False,
    )
    admin_caller = User(id=1, email="admin@barabari.org", is_admin=True, is_super_admin=False)

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
    # Central Auth update will succeed, but local commit fails
    mock_session.commit.side_effect = RuntimeError("Database connection lost during commit")
    repo = TeamCRUDRepository(async_session=mock_session)

    update_data = TeamMemberUpdate(password="NewSecurePass123!")

    with patch("src.repository.crud.team_v2.provision_or_invite_staff", new=AsyncMock()) as mock_prov, \
         patch("src.repository.crud.team_v2.record_durable_credential_reconciliation", new=AsyncMock()) as mock_reconcile:
        with pytest.raises(RuntimeError, match="Database connection lost during commit"):
            await repo.update_team_member(team_id=1, member_id=5, member_data=update_data, caller_user=admin_caller)

        # Central Auth was updated with new password
        mock_prov.assert_awaited_once_with(
            email="reset_target@barabari.org",
            name="Target User",
            role="STAFF",
            send_invite_email=False,
            password="NewSecurePass123!",
        )
        # Rollback was executed
        assert mock_session.rollback.call_count >= 1
        # Durable credential reconciliation path was triggered to prevent split-brain
        mock_reconcile.assert_awaited_once()
        _, rec_kwargs = mock_reconcile.await_args
        assert rec_kwargs["email"] == "reset_target@barabari.org"
        assert rec_kwargs["user_id"] == 10
        assert rec_kwargs["central_user_id"] == "central-uuid-10"
        assert rec_kwargs["new_password_hash"] is not None


@pytest.mark.asyncio
async def test_durable_credential_reconciliation_resolves_split_brain_on_verify_password():
    """
    Verify that when local password_hash is stale after a commit failure,
    the durable reconciliation record in Redis reconciles the local password on next login attempt.
    """
    from src.repository.crud.user import UserCRUDRepository
    from src.services.central_auth_service import record_durable_credential_reconciliation
    from src.securities.hashing.password import pwd_generator
    import json

    mock_redis_client = AsyncMock()
    stored_data = {}

    async def mock_set(key, val, ex=None):
        stored_data[key] = val

    async def mock_get(key):
        return stored_data.get(key)

    async def mock_delete(key):
        stored_data.pop(key, None)

    mock_redis_client.set.side_effect = mock_set
    mock_redis_client.get.side_effect = mock_get
    mock_redis_client.delete.side_effect = mock_delete

    with patch("src.repository.redis_client.async_redis.client", mock_redis_client):
        # 1. Simulate record_durable_credential_reconciliation being written
        new_password = "NewlyReconciledPassword123!"
        new_hash = pwd_generator.generate_hashed_password(hash_salt="", new_password=new_password)

        await record_durable_credential_reconciliation(
            user_id=20,
            email="split_brain@barabari.org",
            new_password_hash=new_hash,
            central_user_id="central-20",
        )

        assert f"SSO_CRED_RECONCILE:split_brain@barabari.org" in stored_data

        # 2. Simulate User in DB still having old password_hash
        old_hash = pwd_generator.generate_hashed_password(hash_salt="", new_password="OldStalePassword123!")
        user = User(
            id=20,
            email="split_brain@barabari.org",
            name="Split Brain User",
            password_hash=old_hash,
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_result.scalar.return_value = user
        mock_session.execute.return_value = mock_result

        user_repo = UserCRUDRepository(async_session=mock_session)
        user_repo.get_user_by_email = AsyncMock(return_value=user)

        # 3. User logs in with new_password: local check fails, reconciliation kicks in and updates hash!
        verified_user = await user_repo.verify_password(
            email="split_brain@barabari.org",
            password=new_password,
        )

        assert verified_user.id == 20
        assert user.password_hash == new_hash
        assert mock_session.commit.call_count >= 1
        # Redis reconciliation key is cleared after successful healing
        assert f"SSO_CRED_RECONCILE:split_brain@barabari.org" not in stored_data


@pytest.mark.asyncio
async def test_durable_credential_reconciliation_resolves_split_brain_for_user_with_empty_password_hash():
    """
    Verify that an SSO user with empty password_hash whose credentials were set by admin
    is properly reconciled on local login instead of receiving 'account is configured for SSO'.
    """
    from src.repository.crud.user import UserCRUDRepository
    from src.services.central_auth_service import record_durable_credential_reconciliation
    from src.securities.hashing.password import pwd_generator

    mock_redis_client = AsyncMock()
    stored_data = {}

    async def mock_set(key, val, ex=None):
        stored_data[key] = val

    async def mock_get(key):
        return stored_data.get(key)

    async def mock_delete(key):
        stored_data.pop(key, None)

    mock_redis_client.set.side_effect = mock_set
    mock_redis_client.get.side_effect = mock_get
    mock_redis_client.delete.side_effect = mock_delete

    with patch("src.repository.redis_client.async_redis.client", mock_redis_client):
        new_password = "FirstTimePassword123!"
        new_hash = pwd_generator.generate_hashed_password(hash_salt="", new_password=new_password)

        await record_durable_credential_reconciliation(
            user_id=25,
            email="sso_first_pass@barabari.org",
            new_password_hash=new_hash,
            central_user_id="central-25",
        )

        user = User(
            id=25,
            email="sso_first_pass@barabari.org",
            name="SSO User",
            password_hash="",  # uninitialized locally
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_result.scalar.return_value = user
        mock_session.execute.return_value = mock_result

        user_repo = UserCRUDRepository(async_session=mock_session)
        user_repo.get_user_by_email = AsyncMock(return_value=user)

        verified_user = await user_repo.verify_password(
            email="sso_first_pass@barabari.org",
            password=new_password,
        )

        assert verified_user.id == 25
        assert user.password_hash == new_hash
        assert mock_session.commit.call_count >= 1






@pytest.mark.asyncio
async def test_provision_or_invite_staff_sends_product_scoped_headers(monkeypatch):
    """Satellite caller must authenticate and assert its product identity via X-Product-Unique-Id header."""
    from src.config.manager import settings
    monkeypatch.setattr(settings, "AUTH_SERVICE_BASE_URL", "http://auth-service:8081")
    monkeypatch.setattr(settings, "CENTRAL_PROFILE_API_KEY", "secret-key")
    monkeypatch.setattr(settings, "SAMPARK_PRODUCT_UNIQUE_ID", "81c53f68-35f4-4133-9e35-06f5c30354b785")

    captured_headers = {}

    async def mock_post(url, headers, json):
        nonlocal captured_headers
        captured_headers = headers
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {
            "isSuccess": True,
            "data": {
                "centralUserId": "uuid-12345",
                "isNewUser": True,
            },
        }
        return mock_resp

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        cid = await provision_or_invite_staff(email="scoped@barabari.org", name="Scoped User")
        assert cid == "uuid-12345"
        assert captured_headers.get("X-Product-Unique-Id") == "81c53f68-35f4-4133-9e35-06f5c30354b785"
        assert captured_headers.get("X-Internal-Api-Key") == "secret-key"


@pytest.mark.asyncio
async def test_revoke_provisioning_sends_product_scoped_headers(monkeypatch):
    """Satellite caller must send X-Product-Unique-Id header and state during compensating revocation."""
    from src.config.manager import settings
    from src.services.central_auth_service import revoke_provisioning
    monkeypatch.setattr(settings, "AUTH_SERVICE_BASE_URL", "http://auth-service:8081")
    monkeypatch.setattr(settings, "CENTRAL_PROFILE_API_KEY", "secret-key")
    monkeypatch.setattr(settings, "SAMPARK_PRODUCT_UNIQUE_ID", "81c53f68-35f4-4133-9e35-06f5c30354b785")

    captured_headers = {}
    captured_payload = {}

    async def mock_post(url, headers, json):
        nonlocal captured_headers, captured_payload
        captured_headers = headers
        captured_payload = json
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {"isSuccess": True}
        return mock_resp

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        res = await revoke_provisioning(
            email="revert_role@barabari.org",
            is_new_user=False,
            previous_role="STAFF",
            previous_password_hash="$2a$10$prevHash",
        )
        assert res is True
        assert captured_headers.get("X-Product-Unique-Id") == "81c53f68-35f4-4133-9e35-06f5c30354b785"
        assert captured_payload.get("previousRole") == "STAFF"
        assert captured_payload.get("previousPasswordHash") == "$2a$10$prevHash"
        assert captured_payload.get("isNewUser") is False
