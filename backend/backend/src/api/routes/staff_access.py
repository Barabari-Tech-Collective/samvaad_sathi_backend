"""Membership-aware staff relationships and atomic scope configuration."""

import fastapi
import sqlalchemy as sa
from pydantic import Field, field_validator
from src.api.dependencies.admin import (
    get_current_admin_user,
    get_user_active_memberships,
    is_admin_user,
)
from src.api.dependencies.session import get_async_session
from src.api.routes.course_workspace import CourseAssignment, assignment_version
from src.models.db.team import Team
from src.models.db.team_member import TeamMember
from src.models.db.user import User
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.services.staff_roles import ROLE_CATALOGUE, canonical_role, relationship_rows

router = fastapi.APIRouter(prefix="/v2/staff-access", tags=["staff-access"])


class RelationshipUpdate(CourseAssignment):
    responsibility: str
    colleges: list[str] | None = Field(default=None, max_length=200)

    @field_validator("responsibility")
    @classmethod
    def role(cls, value):
        return canonical_role(value)

    @field_validator("colleges")
    @classmethod
    def clean_colleges(cls, value):
        if value is None:
            return None
        if any(not v.strip() or len(v.strip()) > 256 for v in value):
            raise ValueError("Use explicit college names without blanks.")
        return sorted(set(v.strip() for v in value))


def administrator(user):
    if not is_admin_user(user):
        raise fastapi.HTTPException(403, "Administrator access required.")


@router.get("/me")
async def my_relationships(
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    memberships = await get_user_active_memberships(user.id, session)
    return {
        "user_id": user.id,
        "administrator": is_admin_user(user),
        "relationships": relationship_rows(memberships),
        "identity_source": (
            "central_sso"
            if getattr(user, "_token_source", None) == "central_sso"
            else "local_compatibility"
        ),
        "notice": "Each team responsibility retains its own course and college boundaries. Unassigned scope grants no course access.",
    }


@router.get("/memberships")
async def memberships(
    page: int = fastapi.Query(1, ge=1),
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    administrator(user)
    rows = (
        await session.execute(
            sa.select(TeamMember, Team, User.name)
            .join(Team, Team.id == TeamMember.team_id)
            .join(User, User.id == TeamMember.user_id)
            .where(TeamMember.status == "active", Team.status == "active")
            .order_by(TeamMember.id)
            .offset((page - 1) * 50)
            .limit(51)
        )
    ).all()
    items = []
    for member, team, name in rows[:50]:
        item = relationship_rows([(member, team)])[0]
        item.update(
            user_id=member.user_id, name=name, version=assignment_version(member)
        )
        items.append(item)
    return {
        "items": items,
        "page": page,
        "has_more": len(rows) > 50,
        "responsibilities": ROLE_CATALOGUE,
    }


@router.put("/memberships/{membership_id}")
async def update_relationship(
    membership_id: int,
    body: RelationshipUpdate,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    administrator(user)
    member = (
        await session.execute(
            sa.select(TeamMember)
            .join(Team, Team.id == TeamMember.team_id)
            .where(
                TeamMember.id == membership_id,
                TeamMember.status == "active",
                Team.status == "active",
            )
            .with_for_update(of=TeamMember)
        )
    ).scalar_one_or_none()
    if member is None:
        raise fastapi.HTTPException(404, "Active membership not found.")
    if body.expected_version is None:
        raise fastapi.HTTPException(428, "Reload this membership before saving.")
    if body.expected_version != assignment_version(member):
        raise fastapi.HTTPException(
            409, "Membership changed. Reload and review current access."
        )
    if body.responsibility == "junior facilitator" and not body.colleges:
        raise fastapi.HTTPException(
            422, "Junior facilitator access requires explicit colleges."
        )
    before = {
        "responsibility": member.access_role,
        "course_ids": member.course_ids,
        "colleges": member.colleges,
    }
    member.access_role = body.responsibility
    member.course_ids = body.course_ids
    member.colleges = body.colleges
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id,
            action="staff_relationship_updated",
            purpose="access_management",
            target_type="team_member",
            target_id=str(member.id),
            reason=body.reason,
            event_metadata={
                "before": before,
                "after": {
                    "responsibility": member.access_role,
                    "course_ids": member.course_ids,
                    "colleges": member.colleges,
                },
            },
        )
    )
    await session.commit()
    return {
        "membership_id": member.id,
        "version": assignment_version(member),
        "responsibility": member.access_role,
        "course_ids": member.course_ids,
        "colleges": member.colleges,
    }
