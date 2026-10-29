"""Students must be refused at the gate of every staff-gated router, before body parsing."""
from types import SimpleNamespace

import fastapi
import pytest

from src.api.dependencies.features import feature_gate


def _request(method, path):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


def _user(role, permissions=None):
    return SimpleNamespace(_token_role=role, feature_permissions=permissions, is_admin=False, is_super_admin=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,feature",
    [
        ("POST", "/api/v2/teams/1/members", "teams"),
        ("PUT", "/api/v2/practice-dashboard/controls/x", "practice_coaching"),
        ("POST", "/api/v2/practice-dashboard/shortlists", "practice_coaching"),
        ("POST", "/api/v2/practice-dashboard/students/9/assignments", "practice_coaching"),
        ("GET", "/api/bulk-ats/batches", "bulk_ats"),
        ("GET", "/api/v2/analytics/students", "analytics"),
        ("POST", "/api/v2/job-profiles", "interview_authoring"),
        ("GET", "/api/v2/course-workspace/members", "course_workspace"),
    ],
)
async def test_student_is_refused_on_staff_routes(method, path, feature):
    with pytest.raises(fastapi.HTTPException) as caught:
        await feature_gate(feature)(_request(method, path), _user("STUDENT"))
    assert caught.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,feature",
    [
        ("GET", "/api/v2/job-profiles", "interview_authoring"),
        ("GET", "/api/v2/practice-dashboard/consents/me", "practice_coaching"),
        ("PUT", "/api/v2/practice-dashboard/consents/me", "practice_coaching"),
    ],
)
async def test_student_keeps_their_own_pages(method, path, feature):
    assert await feature_gate(feature)(_request(method, path), _user("STUDENT")) is None


@pytest.mark.asyncio
async def test_staff_are_not_blocked_by_the_student_rule():
    assert await feature_gate("teams")(_request("GET", "/api/v2/teams"), _user("FACILITATOR")) is None
