import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import fastapi
import pytest
from pydantic import ValidationError
from src.models.db.user import User
from src.api.routes.course_shortlists import Create, Change
from src.services import course_shortlists as service
from src.services.feature_access import route_operation


@pytest.mark.parametrize("field", ["name", "opportunity", "role"])
def test_creation_fields_trim_before_minimum(field):
    data = dict(
        request_key=uuid4(),
        name="Review draft",
        opportunity="Internship",
        role="Engineer",
        expires_at=datetime.datetime.now(datetime.timezone.utc),
    )
    data[field] = " " * 12
    with pytest.raises(ValidationError):
        Create(**data)


def test_timezone_and_reason_are_required():
    with pytest.raises(ValidationError):
        Create(
            request_key=uuid4(),
            name="Review draft",
            opportunity="Internship",
            role="Engineer",
            expires_at=datetime.datetime.now(),
        )
    with pytest.raises(ValidationError):
        Change(expected_version=1, reason=" " * 20)


def test_missing_evidence_does_not_become_a_low_score():
    assert service.evidence(0, None, 1) == {
        "completed_practice": 0,
        "latest_completed_at": None,
        "state": "missing",
    }
    assert service.evidence(1, None, 3)["state"] == "insufficient"
    assert service.evidence(3, None, 3)["state"] == "available"


def test_publish_archive_operations_are_independent():
    assert (
        route_operation("POST", "/v2/course-shortlists/courses/a/1/finalize")
        == "publish"
    )
    assert (
        route_operation("POST", "/v2/course-shortlists/courses/a/1/archive") == "delete"
    )
    user = User(
        id=1,
        is_admin=False,
        is_super_admin=False,
        feature_permissions={"placement_shortlists": ["read"]},
    )
    assert service.permission(user, "read") and not service.permission(user, "publish")


@pytest.mark.asyncio
async def test_unconfigured_staff_denied_without_membership_query():
    user = User(id=1, is_admin=False, is_super_admin=False, feature_permissions=None)
    session = AsyncMock()
    with pytest.raises(fastapi.HTTPException) as error:
        await service.authorize(user, "course-a", session, "read")
    assert error.value.status_code == 403
    session.execute.assert_not_awaited()
