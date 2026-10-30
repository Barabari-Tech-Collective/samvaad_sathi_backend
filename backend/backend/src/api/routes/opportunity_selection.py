"""Formal opportunity recommendations, immutable locks and explicit student release."""

import datetime as dt
from typing import Literal
from uuid import UUID
import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator
from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.opportunity import Opportunity
from src.models.db.opportunity_selection import (
    OpportunitySelection,
    OpportunitySelectionEntry,
)
from src.models.db.user import User
from src.services import opportunities as access
from src.services import opportunity_selection as service
from src.services.course_access import student_predicate

router = fastapi.APIRouter(prefix="/v2/opportunities", tags=["opportunity-selection"])


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    reason: str = Field(min_length=10, max_length=1000)

    @field_validator("reason", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class Recommendation(Change):
    student_id: int = Field(ge=1)
    decision: Literal["include", "exclude", "needs_evidence"]


class Decision(Change):
    student_id: int = Field(ge=1)
    decision: Literal["include", "exclude"]
    student_feedback: str = Field(min_length=10, max_length=2000)
    next_step: str = Field(min_length=3, max_length=1000)

    _trim_fields = field_validator("student_feedback", "next_step", mode="before")(
        classmethod(lambda cls, v: v.strip() if isinstance(v, str) else v)
    )


class Release(Change):
    request_key: UUID


async def load(session, user, opportunity_id, operation, lock=False):
    query = sa.select(Opportunity).where(Opportunity.id == opportunity_id)
    if lock:
        query = query.with_for_update()
    opening = (await session.execute(query)).scalar_one_or_none()
    if not opening:
        raise fastapi.HTTPException(404, "Opportunity not found.")
    predicate = await access.authorize(user, opening.course_id, session, operation)
    row = await service.latest(session, opening.id)
    return opening, row, predicate


async def draft(session, user, opening, row, expected):
    service.verify_version(row, expected)
    service.require_draft(row, opening)
    if not row:
        row = OpportunitySelection(
            opportunity_id=opening.id,
            revision=1,
            version=1,
            status="draft",
            rules=service.rules(opening),
            created_by=user.id,
        )
        session.add(row)
        await session.flush()
    return row


async def entry_for(session, opening, row, predicate, student_id):
    people = await service.candidates(session, opening, predicate)
    if student_id not in {p.id for p in people}:
        raise fastapi.HTTPException(
            404, "Candidate is outside this opportunity or your current scope."
        )
    entry = (
        await session.execute(
            sa.select(OpportunitySelectionEntry).where(
                OpportunitySelectionEntry.selection_id == row.id,
                OpportunitySelectionEntry.student_id == student_id,
            )
        )
    ).scalar_one_or_none()
    if not entry:
        entry = OpportunitySelectionEntry(selection_id=row.id, student_id=student_id)
        session.add(entry)
    return entry


@router.get("/me/outcomes")
async def my_outcomes(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await session.execute(
            sa.select(OpportunitySelection, Opportunity)
            .join(Opportunity, Opportunity.id == OpportunitySelection.opportunity_id)
            .where(
                OpportunitySelection.status == "released",
                sa.exists(
                    sa.select(OpportunitySelectionEntry.id).where(
                        OpportunitySelectionEntry.selection_id
                        == OpportunitySelection.id,
                        OpportunitySelectionEntry.student_id == user.id,
                    )
                ),
            )
            .order_by(
                OpportunitySelection.released_at.desc(), OpportunitySelection.id.desc()
            )
        )
    ).all()
    items, seen = [], set()
    for row, opening in rows:
        if opening.id in seen:
            continue
        # Only own frozen outcome, with current enrollment; no private review fields.
        visible = (
            await session.execute(
                sa.select(User.id).where(
                    User.id == user.id,
                    student_predicate(opening.course_id, (), global_access=True),
                )
            )
        ).scalar_one_or_none()
        if not visible:
            continue
        item = next(
            (
                i
                for i in (row.snapshot or {}).get("items", [])
                if i["student_id"] == user.id
            ),
            None,
        )
        if not item:
            continue
        seen.add(opening.id)
        decision = item["decision"]
        items.append(
            {
                "opportunity_id": opening.id,
                "revision": row.revision,
                "title": row.snapshot["title"],
                "organisation": row.snapshot["organisation"],
                "decision": decision["decision"],
                "feedback": decision["student_feedback"],
                "next_step": decision["next_step"],
                "released_at": row.released_at,
            }
        )
    return {"items": items}


@router.get("/{opportunity_id}/selection")
async def detail(
    opportunity_id: int,
    response: fastapi.Response,
    revision: int | None = fastapi.Query(None, ge=1),
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    opening, row, predicate = await load(session, user, opportunity_id, "read")
    historical = revision is not None and (row is None or revision != row.revision)
    if historical:
        row = (
            await session.execute(
                sa.select(OpportunitySelection).where(
                    OpportunitySelection.opportunity_id == opening.id,
                    OpportunitySelection.revision == revision,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise fastapi.HTTPException(404, "Review revision not found.")
    response.headers["Cache-Control"] = "no-store"
    view = await service.detail(session, user, opening, row, predicate)
    view["historical"] = historical
    if historical:
        for name in (
            "can_recommend",
            "can_decide",
            "can_lock",
            "can_release",
            "can_revise",
        ):
            view[name] = False
        view["lock_blockers"] = []
    return view


@router.put("/{opportunity_id}/selection/recommendation")
async def recommend(
    opportunity_id: int,
    body: Recommendation,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    opening, row, predicate = await load(session, user, opportunity_id, "update", True)
    row = await draft(session, user, opening, row, body.expected_version)
    entry = await entry_for(session, opening, row, predicate, body.student_id)
    previous = service.describe(entry)
    entry.recommendation, entry.recommendation_reason = body.decision, body.reason
    entry.recommended_by, entry.recommended_at = user.id, dt.datetime.now(service.UTC)
    # A new recommendation after a lead decision requires that decision to be reviewed again.
    entry.decision = entry.decision_reason = entry.student_feedback = (
        entry.next_step
    ) = None
    entry.decided_by = entry.decided_at = entry.evidence_snapshot = None
    row.version += 1
    service.audit(
        session,
        user,
        opening,
        row,
        "opportunity_recommended",
        body.reason,
        {
            "student_id": body.student_id,
            "previous": previous,
            "recommendation": body.decision,
        },
    )
    await session.commit()
    return service.metadata(row, opening)


@router.put("/{opportunity_id}/selection/decision")
async def decide(
    opportunity_id: int,
    body: Decision,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    # Publish is an explicit elevated grant. Merely being a placement member grants no decision authority.
    opening, row, predicate = await load(session, user, opportunity_id, "publish", True)
    row = await draft(session, user, opening, row, body.expected_version)
    entry = await entry_for(session, opening, row, predicate, body.student_id)
    evidence = (await service.evidence(session, opening, [body.student_id]))[
        body.student_id
    ]
    if evidence["state"] != "ready":
        raise fastapi.HTTPException(
            409,
            "Fresh formal evidence is required. Arrange another round before deciding.",
        )
    previous = service.describe(entry)
    entry.decision, entry.decision_reason = body.decision, body.reason
    entry.student_feedback, entry.next_step = body.student_feedback, body.next_step
    entry.decided_by, entry.decided_at = user.id, dt.datetime.now(service.UTC)
    entry.evidence_snapshot = evidence
    row.version += 1
    service.audit(
        session,
        user,
        opening,
        row,
        "opportunity_decided",
        body.reason,
        {
            "student_id": body.student_id,
            "previous": previous,
            "decision": body.decision,
        },
    )
    await session.commit()
    return service.metadata(row, opening)


@router.post("/{opportunity_id}/selection/lock")
async def lock(
    opportunity_id: int,
    body: Change,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    opening, row, predicate = await load(session, user, opportunity_id, "publish", True)
    service.verify_version(row, body.expected_version)
    service.require_draft(row, opening)
    if row is None:
        raise fastapi.HTTPException(409, "Record reviewed decisions first.")
    view = await service.detail(session, user, opening, row, predicate)
    if view["lock_blockers"]:
        raise fastapi.HTTPException(409, view["lock_blockers"])
    row.snapshot = {
        "title": opening.title,
        "organisation": opening.organisation,
        "rules": row.rules,
        "items": view["items"],
    }
    row.status, row.locked_by, row.locked_at = (
        "locked",
        user.id,
        dt.datetime.now(service.UTC),
    )
    row.version += 1
    service.audit(
        session, user, opening, row, "opportunity_selection_locked", body.reason
    )
    await session.commit()
    return service.metadata(row, opening)


@router.post("/{opportunity_id}/selection/release")
async def release(
    opportunity_id: int,
    body: Release,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    opening, row, predicate = await load(session, user, opportunity_id, "publish", True)
    previous = (
        await session.execute(
            sa.select(OpportunitySelection).where(
                OpportunitySelection.release_key == str(body.request_key)
            )
        )
    ).scalar_one_or_none()
    if previous:
        if (
            previous.opportunity_id != opening.id
            or previous.released_by != user.id
            or previous.release_reason != body.reason
        ):
            raise fastapi.HTTPException(409, "Release key belongs to another request.")
        return service.metadata(previous, opening)
    service.verify_version(row, body.expected_version)
    if not row or row.status != "locked" or opening.status == "archived":
        raise fastapi.HTTPException(
            409, "Only a locked review on a non-archived opportunity can be released."
        )
    allowed = {p.id for p in await service.candidates(session, opening, predicate)}
    locked = {i["student_id"] for i in row.snapshot["items"]}
    if not locked or not locked.issubset(allowed):
        raise fastapi.HTTPException(
            409,
            "Candidate membership or your scope changed. Resolve access before release.",
        )
    if row.rules != service.rules(opening):
        raise fastapi.HTTPException(
            409, "Rules changed. Create and review a new revision before release."
        )
    row.status, row.released_by, row.released_at = (
        "released",
        user.id,
        dt.datetime.now(service.UTC),
    )
    row.release_key, row.release_reason = str(body.request_key), body.reason
    row.version += 1
    service.audit(
        session, user, opening, row, "opportunity_outcomes_released", body.reason
    )
    await session.commit()
    return service.metadata(row, opening)


@router.post("/{opportunity_id}/selection/revision")
async def revise(
    opportunity_id: int,
    body: Change,
    user=fastapi.Depends(get_current_admin_user),
    session=fastapi.Depends(get_async_session),
):
    opening, row, _ = await load(session, user, opportunity_id, "publish", True)
    service.verify_version(row, body.expected_version)
    if not row or opening.status != "open":
        raise fastapi.HTTPException(
            409, "An open opportunity with an existing review is required."
        )
    new = OpportunitySelection(
        opportunity_id=opening.id,
        revision=row.revision + 1,
        version=row.version + 1,
        status="draft",
        rules=service.rules(opening),
        created_by=user.id,
    )
    session.add(new)
    await session.flush()
    service.audit(
        session,
        user,
        opening,
        new,
        "opportunity_review_revised",
        body.reason,
        {"previous_selection_id": row.id},
    )
    await session.commit()
    return service.metadata(new, opening)
