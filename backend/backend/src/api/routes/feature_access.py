"""Admin-managed personal dashboard permissions; no role/data-scope escalation."""
import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator
from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.session import get_async_session
from src.models.db.user import User
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.services.feature_access import CATALOGUE, validate_permissions

router = fastapi.APIRouter(prefix="/v2/feature-access", tags=["feature-access"])

class PermissionUpdate(BaseModel):
    expected_version: int = Field(ge=0)
    permissions: dict[str, list[str]]
    reason: str = Field(min_length=10, max_length=1000)

    @field_validator("permissions")
    @classmethod
    def valid_permissions(cls, value):
        return validate_permissions(value)

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


def admin_only(user):
    if not is_admin_user(user):
        raise fastapi.HTTPException(403, "Administrator access required.")


@router.get("/me")
async def me(user=fastapi.Depends(get_current_admin_user)):
    return {"administrator": is_admin_user(user), "permissions": user.feature_permissions,
            "version": user.feature_permissions_version, "catalogue": CATALOGUE,
            "notice": "Feature selections remain subject to role, course, college and sharing rules."}


@router.get("/people")
async def people(page: int = fastapi.Query(1, ge=1), user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session)):
    admin_only(user)
    active = sa.exists(sa.select(TeamMember.id).join(Team, Team.id == TeamMember.team_id).where(
        TeamMember.user_id == User.id, TeamMember.status == "active", Team.status == "active"))
    rows = (await session.execute(sa.select(User).where(active).order_by(User.id).offset((page-1)*50).limit(51))).scalars().all()
    return {"page": page, "has_more": len(rows) > 50, "items": [
        {"id": row.id, "name": row.name, "administrator": is_admin_user(row),
         "permissions": row.feature_permissions, "version": row.feature_permissions_version}
        for row in rows[:50]]}


@router.put("/people/{person_id}")
async def update(person_id: int, body: PermissionUpdate, user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session)):
    admin_only(user)
    target = (await session.execute(sa.select(User).where(User.id == person_id).with_for_update())).scalar_one_or_none()
    active = (await session.execute(sa.select(TeamMember.id).join(Team, Team.id == TeamMember.team_id).where(
        TeamMember.user_id == person_id, TeamMember.status == "active", Team.status == "active").limit(1))).scalar_one_or_none()
    if target is None or active is None:
        raise fastapi.HTTPException(404, "Active staff account not found.")
    if is_admin_user(target):
        raise fastapi.HTTPException(403, "Administrator authority cannot be changed in the feature editor.")
    if target.feature_permissions_version != body.expected_version:
        raise fastapi.HTTPException(409, "Access changed. Reload and review before saving.")
    before = target.feature_permissions
    target.feature_permissions = body.permissions
    target.feature_permissions_version += 1
    session.add(PracticeDashboardAuditEvent(actor_id=user.id, action="personal_feature_access_updated",
        purpose="access_management", target_type="user", target_id=str(person_id), reason=body.reason,
        event_metadata={"before": before, "after": body.permissions, "version": target.feature_permissions_version}))
    await session.commit()
    return {"id": target.id, "permissions": target.feature_permissions, "version": target.feature_permissions_version}
