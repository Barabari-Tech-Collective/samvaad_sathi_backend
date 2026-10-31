"""Ready-to-put-forward recommendations for the program team (and placement).

Rules are per course and visible; a person acts on the list by assigning an AI interview or adding
the learner to an opening with the tools that already exist. Nothing is decided automatically."""

import datetime as dt

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator

from src.api.dependencies.admin import get_current_admin_user, is_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes.course_workspace import _shared_student_predicate
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.readiness_rule import ReadinessRule
from src.models.db.user import User
from src.services import readiness as service
from src.services.feature_access import permits

router = fastapi.APIRouter(prefix="/v2/readiness", tags=["readiness"])
ORDER = {"ready": 0, "almost": 1, "not_yet": 2}


class RuleBody(BaseModel):
    expected_version: int = Field(ge=0)
    window_days: int = Field(ge=7, le=365)
    min_mock_interviews: int = Field(ge=1, le=20)
    min_average_score: int = Field(ge=0, le=100)
    min_drills: int = Field(ge=0, le=200)
    require_improving: bool = False
    reason: str = Field(min_length=10, max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def trim(cls, v):
        return v.strip() if isinstance(v, str) else v


def _can(user, operation):
    """Program team (course workspace, coaching) and placement (shortlists) may use this."""
    if is_admin_user(user):
        return True
    policy = user.feature_permissions
    return isinstance(policy, dict) and (
        permits(policy, "course_workspace", operation)
        or permits(policy, "placement_shortlists", operation)
        or (operation == "read" and permits(policy, "practice_coaching", "read"))
    )


def _may_edit(user):
    return _can(user, "update")


@router.get("/courses/{course_id}")
async def recommendations(
    course_id: str,
    response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if not _can(user, "read"):
        raise fastapi.HTTPException(403, "Readiness suggestions need course workspace or placement access.")
    scope = await _shared_student_predicate(course_id, user, session)
    response.headers["Cache-Control"] = "no-store"
    rule = service.rule_view(await service.rule_for(session, course_id))
    people = (
        await session.execute(
            sa.select(User.id, User.name, User.university).where(scope).order_by(User.name).limit(500)
        )
    ).all()
    found = await service.assess(session, course_id, [p.id for p in people], rule)
    items = [
        {"student_id": p.id, "name": p.name, "college": p.university} | found[p.id] for p in people
    ]
    items.sort(key=lambda i: (ORDER[i["status"]], i["name"] or ""))
    return {
        "rule": rule,
        "items": items,
        "counts": {k: sum(1 for i in items if i["status"] == k) for k in ORDER},
        "can_edit": _may_edit(user),
        "notice": "A suggestion from your rules, not a decision. Look at each learner before acting.",
    }


@router.put("/courses/{course_id}/rule")
async def set_rule(
    course_id: str,
    body: RuleBody,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    if not _may_edit(user):
        raise fastapi.HTTPException(403, "Changing readiness rules needs edit access.")
    await _shared_student_predicate(course_id, user, session)
    row = (
        await session.execute(
            sa.select(ReadinessRule).where(ReadinessRule.course_id == course_id).with_for_update()
        )
    ).scalar_one_or_none()
    current = row.version if row else 0
    if current != body.expected_version:
        raise fastapi.HTTPException(409, "The rules changed. Reload them.")
    if row is None:
        row = ReadinessRule(course_id=course_id, version=0)
        session.add(row)
    row.window_days, row.min_mock_interviews = body.window_days, body.min_mock_interviews
    row.min_average_score, row.min_drills = body.min_average_score, body.min_drills
    row.require_improving, row.version, row.updated_by = body.require_improving, current + 1, user.id
    row.updated_at = dt.datetime.now(dt.timezone.utc)
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action="readiness_rule_changed", purpose="readiness",
            target_type="course", target_id=course_id, reason=body.reason,
            event_metadata={"version": row.version},
        )
    )
    await session.commit()
    return service.rule_view(row)
