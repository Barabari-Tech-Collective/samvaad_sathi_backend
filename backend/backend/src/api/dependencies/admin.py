"""Authorization gate for cross-student (analytics/dashboard) endpoints.

Kept separate from api/dependencies/auth.py, which answers "who is this?".
This module answers "are they allowed to read other people's data?" - a
distinction the analytics endpoints previously did not make at all: they
depended on get_current_user purely to require *a* login, then discarded
it (`del current_user`) and queried across every user in the system. Any
logged-in student could therefore enumerate every other student's name,
college, scores and activity.
"""

import fastapi

from src.api.dependencies.auth import get_current_user
from src.models.db.user import User


async def get_current_admin_user(
    current_user: User = fastapi.Depends(get_current_user),
) -> User:
    """Require an authenticated user with the is_admin flag set.

    Returns 403 (not 404) because the caller is legitimately authenticated -
    they simply aren't authorized for this resource, and hiding that fact
    provides no benefit here: the endpoints are already discoverable in the
    OpenAPI schema.
    """
    if not getattr(current_user, "is_admin", False):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )
    return current_user
