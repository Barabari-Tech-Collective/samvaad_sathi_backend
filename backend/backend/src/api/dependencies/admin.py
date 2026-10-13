"""Authorization gate for cross-student (analytics/dashboard) endpoints.

Kept separate from api/dependencies/auth.py, which answers "who is this?".
This module answers "are they allowed to read other people's data?" - a
distinction the analytics endpoints previously did not make at all: they
depended on get_current_user purely to require *a* login, then discarded
it (`del current_user`) and queried across every user in the system. Any
logged-in student could therefore enumerate every other student's name,
college, scores and activity.

Access is granted two ways, and either is sufficient:

  1. user.is_admin in the database - the intended long-term mechanism.
  2. the user's email appearing in the ADMIN_EMAILS setting.

(2) exists because (1) alone is unreachable in some environments: the
column defaults to false for every pre-existing row, so granting it
requires an UPDATE, and not everyone who can deploy this service can also
run SQL against its database (Render's free tier has no shell, and DB
console access is not always shared with whoever owns the deploy). An
env-var allow-list is settable by anyone who can configure the service,
which keeps the dashboard operable without weakening the gate for
students - they appear in neither list.
"""

import fastapi

from src.api.dependencies.auth import get_current_user
from src.config.manager import settings
from src.models.db.user import User


def _allow_listed_emails() -> set[str]:
    """Parse ADMIN_EMAILS into a normalised set. Comma-separated; blanks and
    surrounding whitespace ignored so a trailing comma isn't a footgun."""
    raw = getattr(settings, "ADMIN_EMAILS", "") or ""
    return {entry.strip().lower() for entry in raw.split(",") if entry.strip()}


def is_admin_user(user: User) -> bool:
    """True if `user` may read cross-student data."""
    if getattr(user, "is_admin", False):
        return True
    email = (getattr(user, "email", "") or "").strip().lower()
    return bool(email) and email in _allow_listed_emails()


async def get_current_admin_user(
    current_user: User = fastapi.Depends(get_current_user),
) -> User:
    """Require an authenticated user authorised for cross-student data.

    Returns 403 (not 404) because the caller is legitimately authenticated -
    they simply aren't authorized for this resource, and hiding that fact
    provides no benefit here: the endpoints are already discoverable in the
    OpenAPI schema.
    """
    if not is_admin_user(current_user):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )
    return current_user


_BULK_ATS_ROLES = {"recruiter", "placement_staff", "admin"}


def is_bulk_ats_user(user: User) -> bool:
    """True if `user` may use Bulk ATS (screening assistance, not students).

    Deliberately narrower than is_admin_user: a recruiter should not need
    cross-student analytics access to screen resumes, so this also accepts
    user.role in _BULK_ATS_ROLES on its own, in addition to the existing
    is_admin/ADMIN_EMAILS grant.
    """
    if is_admin_user(user):
        return True
    role = (getattr(user, "role", "") or "").strip().lower()
    return role in _BULK_ATS_ROLES


async def get_current_bulk_ats_user(
    current_user: User = fastapi.Depends(get_current_user),
) -> User:
    """Require an authenticated recruiter/placement_staff/admin user.

    Students (role is NULL, is_admin false) are rejected with 403. This is a
    backend-level gate, not a UI hide: every Bulk ATS route depends on this,
    not on get_current_user alone.
    """
    if not is_bulk_ats_user(current_user):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_403_FORBIDDEN,
            detail="Bulk ATS access is restricted to recruiter, placement staff, or admin accounts.",
        )
    return current_user
