"""Regression tests for the analytics authorization gap.

Before this fix, every endpoint on the /v2/analytics router depended on
get_current_user purely to require *a* login and then discarded it
(`del current_user`) before querying across all users - so any logged-in
student could enumerate every other student's name, college, scores and
activity. /analytics/student/{user_id} and /analytics/interview/{id} were
worse still: they took the target id straight off the path and never
compared it to the caller.
"""

import fastapi
import pytest

from src.api.dependencies.admin import get_current_admin_user


class _User:
    def __init__(self, user_id: int, is_admin: bool = False):
        self.id = user_id
        self.is_admin = is_admin


@pytest.mark.asyncio
async def test_admin_dependency_rejects_non_admin():
    with pytest.raises(fastapi.HTTPException) as exc:
        await get_current_admin_user(current_user=_User(1, is_admin=False))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_dependency_allows_admin():
    admin = _User(1, is_admin=True)
    assert await get_current_admin_user(current_user=admin) is admin


@pytest.mark.asyncio
async def test_admin_dependency_rejects_user_missing_the_attribute():
    """A User object predating the is_admin column must not be treated as admin."""

    class _Legacy:
        id = 7

    with pytest.raises(fastapi.HTTPException) as exc:
        await get_current_admin_user(current_user=_Legacy())
    assert exc.value.status_code == 403


def test_v2_analytics_router_is_admin_gated():
    """The gate is on the router, so it cannot be forgotten on a new endpoint."""
    from src.api.routes.analytics_v2 import router

    gated = any(
        getattr(d.dependency, "__name__", "") == "get_current_admin_user"
        for d in (router.dependencies or [])
    )
    assert gated, "/v2/analytics router must carry the admin dependency"


def test_every_v2_analytics_route_requires_admin():
    """Belt-and-braces: assert it actually propagated to every route."""
    from src.api.routes.analytics_v2 import router

    assert router.routes, "expected analytics v2 routes to exist"
    for route in router.routes:
        resolved = [
            getattr(dep.call, "__name__", "")
            for dep in getattr(route.dependant, "dependencies", []) or []
        ]
        assert "get_current_admin_user" in resolved, f"{route.path} is not admin-gated"


def test_v1_aggregate_analytics_endpoints_require_admin():
    from src.api.routes import analytics as v1

    admin_only = [
        v1.get_role_segment_analytics,
        v1.get_difficulty_segment_analytics,
        v1.get_college_segment_analytics,
        v1.get_system_analytics,
        v1.get_scoring_analytics,
        v1.get_analytics_alerts,
    ]
    for fn in admin_only:
        defaults = [
            getattr(d, "dependency", None)
            for d in fn.__defaults__ or ()
            if hasattr(d, "dependency")
        ]
        names = [getattr(d, "__name__", "") for d in defaults if d]
        assert "get_current_admin_user" in names, f"{fn.__name__} must be admin-only"
