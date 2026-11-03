from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from src.services.feature_access import permits, route_operation, validate_permissions
from src.api.routes.feature_access import PermissionUpdate, update

@pytest.mark.parametrize("policy,allowed", [(None, True), ({}, False), ({"teams":["read"]}, True), ({"teams":["delete"]}, False), ({"unknown":["read"]}, False)])
def test_personal_read_policy(policy, allowed):
    assert permits(policy, "teams", "read") is allowed

def test_operation_revocation_is_independent():
    assert permits({"teams":["read","update"]}, "teams", "update")
    assert not permits({"teams":["read","update"]}, "teams", "delete")

@pytest.mark.parametrize("policy", [{"bogus":["read"]}, {"teams":["publish"]}, {"teams":["update"]}])
def test_unknown_or_incomplete_grants_rejected(policy):
    with pytest.raises(ValueError): validate_permissions(policy)

@pytest.mark.parametrize("method,path,operation", [("POST","/v2/job-profiles/1/questions/generate","generate"),("POST","/v2/practice-templates/1/submit","publish"),("PATCH","/v2/assessment-roles/1/review","publish"),("GET","/v2/teams","read"),("DELETE","/v2/teams/1","delete"),("PUT","/v2/course-workspace/members/1/courses","update")])
def test_route_operations(method,path,operation):
    assert route_operation(method,path) == operation

def test_reason_whitespace_is_not_audit_reason():
    with pytest.raises(ValidationError): PermissionUpdate(expected_version=0, permissions={}, reason=" "*10)

@pytest.mark.asyncio
async def test_non_admin_cannot_assign_permissions():
    session = AsyncMock()
    user = SimpleNamespace(id=1,email="test@example.test",is_admin=False,is_super_admin=False,role=None)
    with pytest.raises(HTTPException) as error:
        await update(2, PermissionUpdate(expected_version=0,permissions={},reason="Revoke access now"),user,session)
    assert error.value.status_code == 403
    session.execute.assert_not_awaited()

@pytest.mark.asyncio
async def test_concurrent_permission_update_rejected():
    session = AsyncMock()
    target = SimpleNamespace(id=2,email="person@example.test",is_admin=False,is_super_admin=False,role=None, feature_permissions_version=2)
    result, active = MagicMock(), MagicMock()
    result.scalar_one_or_none.return_value=target
    active.scalar_one_or_none.return_value=7
    session.execute.side_effect=[result,active]
    admin = SimpleNamespace(id=1,email="admin@example.test",is_admin=True,is_super_admin=False)
    with pytest.raises(HTTPException) as error:
        await update(2,PermissionUpdate(expected_version=1,permissions={},reason="Revoke access now"),admin,session)
    assert error.value.status_code == 409
    session.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_assignment_audited_and_version_incremented():
    session=AsyncMock(); session.add=MagicMock()
    target=SimpleNamespace(id=2,email="person@example.test",is_admin=False,is_super_admin=False,role=None,feature_permissions=None,feature_permissions_version=0)
    result,active=MagicMock(),MagicMock(); result.scalar_one_or_none.return_value=target;active.scalar_one_or_none.return_value=7
    session.execute.side_effect=[result,active]
    admin=SimpleNamespace(id=1,email="admin@example.test",is_admin=True,is_super_admin=False)
    response=await update(2,PermissionUpdate(expected_version=0,permissions={},reason="Revoke access now"),admin,session)
    assert response["permissions"] == {} and response["version"] == 1
    assert session.add.call_args.args[0].event_metadata["before"] is None
    session.commit.assert_awaited_once()
