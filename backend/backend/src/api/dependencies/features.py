import fastapi
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.admin import is_admin_user
from src.services.feature_access import require_permission, route_operation


def feature_gate(feature):
    async def gate(request: fastapi.Request, user=fastapi.Depends(get_current_user)):
        if not is_admin_user(user):
            require_permission(user.feature_permissions, feature, route_operation(request.method, request.url.path))
    return gate
