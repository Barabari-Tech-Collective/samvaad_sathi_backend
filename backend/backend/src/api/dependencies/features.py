import fastapi
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.admin import is_admin_user, normalize_role
from src.services.feature_access import require_permission, route_operation

# The only (method, path) pairs under a staff-gated router that students legitimately use.
# Everything else behind a gate is refused for a central STUDENT account before any request
# body is parsed or any handler runs, so staff writes answer 403 rather than leaking a 422.
STUDENT_ALLOWED = frozenset(
    {
        ("GET", "/v2/job-profiles"),
        ("GET", "/v2/practice-dashboard/consents/me"),
        ("PUT", "/v2/practice-dashboard/consents/me"),
    }
)


def _student_may_call(method: str, path: str) -> bool:
    path = path.rstrip("/")
    return any(method == m and path.endswith(allowed) for m, allowed in STUDENT_ALLOWED)


def feature_gate(feature):
    async def gate(request: fastapi.Request, user=fastapi.Depends(get_current_user)):
        if normalize_role(getattr(user, "_token_role", "")) == "student" and not _student_may_call(
            request.method, request.url.path
        ):
            raise fastapi.HTTPException(403, "This area is not available to student accounts.")
        if not is_admin_user(user):
            require_permission(user.feature_permissions, feature, route_operation(request.method, request.url.path))
    return gate
