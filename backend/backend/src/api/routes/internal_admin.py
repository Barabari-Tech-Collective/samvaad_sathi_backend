"""
Super-admin plan, Phase 12: lets auth-service's SUPER_ADMIN panel designate/revoke a
Samvaad Saathi admin. Samvaad Saathi's "admin" concept (User.is_admin, see
api/dependencies/admin.py) is entirely separate from auth-service's Roles/UserIdentity
system - this is a small, purpose-built bridge between them, not a general-purpose API.

Deliberately NOT gated by get_current_user: the caller is auth-service itself
(server-to-server), not a logged-in Samvaad Saathi user, so there is no student/owner JWT
to check here. Trust is a shared secret instead (SUPER_ADMIN_API_KEY), compared with
secrets.compare_digest to avoid a timing side-channel. An unset key disables the endpoint
outright (503) rather than leaving it open - an empty key must never accidentally mean
"no check required".
"""

import secrets

import fastapi
import pydantic

from src.api.dependencies.repository import get_repository
from src.config.manager import settings
from src.repository.crud.user import UserCRUDRepository
from src.utilities.exceptions.database import EntityDoesNotExist

router = fastapi.APIRouter(prefix="/internal/super-admin", tags=["users"])


async def require_internal_super_admin_key(
    x_internal_api_key: str = fastapi.Header(default=""),
) -> None:
    if not settings.SUPER_ADMIN_API_KEY:
        raise fastapi.HTTPException(status_code=503, detail="Super-admin bridge is not configured")
    if not x_internal_api_key or not secrets.compare_digest(x_internal_api_key, settings.SUPER_ADMIN_API_KEY):
        raise fastapi.HTTPException(status_code=401, detail="Invalid internal API key")


class SetAdminStatusBody(pydantic.BaseModel):
    email: str
    is_admin: bool


@router.post("/set-admin-status", dependencies=[fastapi.Depends(require_internal_super_admin_key)])
async def set_admin_status(
    payload: SetAdminStatusBody,
    user_repo: UserCRUDRepository = fastapi.Depends(get_repository(repo_type=UserCRUDRepository)),
):
    try:
        user = await user_repo.set_admin_status(email=payload.email, is_admin=payload.is_admin)
    except EntityDoesNotExist:
        raise fastapi.HTTPException(status_code=404, detail=f"No Samvaad Saathi account found for {payload.email}")

    return {"email": user.email, "is_admin": user.is_admin}


@router.get("/admins", dependencies=[fastapi.Depends(require_internal_super_admin_key)])
async def list_admins(
    user_repo: UserCRUDRepository = fastapi.Depends(get_repository(repo_type=UserCRUDRepository)),
):
    admins = await user_repo.list_admins()
    return [{"email": a.email, "name": a.name} for a in admins]
