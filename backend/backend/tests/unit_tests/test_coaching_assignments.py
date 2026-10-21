from unittest.mock import AsyncMock, patch
from uuid import uuid4
import fastapi
import pytest
from pydantic import ValidationError
from src.api.routes.coaching_assignments import AssignmentCreate, AssignmentCancel
from src.models.db.user import User
from src.services import coaching_assignments as service


@pytest.mark.parametrize("reason", [" " * 20, "short"])
def test_trimmed_reason_minimum(reason):
    with pytest.raises(ValidationError):
        AssignmentCreate(request_key=uuid4(), module="warm_up", reason=reason)
    with pytest.raises(ValidationError):
        AssignmentCancel(expected_version=1, reason=reason)


def test_only_connected_drills_can_be_assigned():
    with pytest.raises(ValidationError):
        AssignmentCreate(
            request_key=uuid4(), module="pace", reason="Practise a clearer introduction"
        )


@pytest.mark.asyncio
async def test_personal_create_revocation_prevents_scope_query():
    user = User(
        id=1,
        is_admin=False,
        is_super_admin=False,
        feature_permissions={
            "course_workspace": ["read"],
            "practice_coaching": ["read"],
        },
    )
    session = AsyncMock()
    with pytest.raises(fastapi.HTTPException) as error:
        await service.authorize_staff(session, user, "course-a", 2, "create")
    assert error.value.status_code == 403
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_coaching_scope_is_requested_instead_of_placement_scope():
    user = User(id=1, is_admin=False, is_super_admin=False)
    session = AsyncMock()
    with patch.object(
        service,
        "_shared_student_predicate",
        AsyncMock(side_effect=fastapi.HTTPException(403, "Denied")),
    ) as predicate:
        with pytest.raises(fastapi.HTTPException):
            await service.authorize_staff(session, user, "course-a", 2, "read")
    assert predicate.await_args.kwargs == {"coaching_only": True}
