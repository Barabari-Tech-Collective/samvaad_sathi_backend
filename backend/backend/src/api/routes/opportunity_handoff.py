"""Hand the final list to the hiring company and track what the company does with each person.

Starts from a locked selection. Sending is done by a person (email, portal, meeting); this
records the contact, the exact list, an exportable file, and each person's status afterwards."""

import csv
import datetime as dt
import io
from typing import Literal

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.models.db.opportunity import Opportunity
from src.models.db.opportunity_handoff import (
    COMPANY_STATUSES, OpportunityHandoff, OpportunityHandoffCandidate,
)
from src.models.db.opportunity_selection import OpportunitySelectionEntry
from src.models.db.practice_governance import PracticeDashboardAuditEvent
from src.models.db.user import User
from src.services import opportunities as access
from src.services import opportunity_selection as selection

router = fastapi.APIRouter(prefix="/v2/opportunities", tags=["opportunity-handoff"])
UTC = dt.timezone.utc
STATUS_TEXT = {
    "not_sent": "Not sent yet", "sent": "Sent to the company", "interviewing": "Company is interviewing",
    "offered": "Offer made", "joined": "Joined", "declined": "Declined by the learner",
    "not_selected": "Not selected by the company",
}


def _text(v):
    return v.strip() if isinstance(v, str) else v


class Create(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact_name: str = Field(min_length=2, max_length=160)
    contact_email: str | None = Field(default=None, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    note: str | None = Field(default=None, max_length=2000)
    _t = field_validator("contact_name", "contact_email", "note", mode="before")(classmethod(lambda c, v: _text(v) or None if isinstance(v, str) else v))


class Share(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    channel: Literal["email", "portal", "meeting", "other"]
    note: str | None = Field(default=None, max_length=2000)


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    company_status: Literal[COMPANY_STATUSES]
    note: str | None = Field(default=None, max_length=1000)


async def _context(session, user, opportunity_id, operation, lock=False):
    q = sa.select(Opportunity).where(Opportunity.id == opportunity_id)
    opening = (await session.execute(q)).scalar_one_or_none()
    if opening is None:
        raise fastapi.HTTPException(404, "Opportunity not found.")
    await access.authorize(user, opening.course_id, session, operation)
    query = (
        sa.select(OpportunityHandoff)
        .where(OpportunityHandoff.opportunity_id == opening.id)
        .order_by(OpportunityHandoff.id.desc())
        .limit(1)
    )
    if lock:
        query = query.with_for_update()
    handoff = (await session.execute(query)).scalar_one_or_none()
    return opening, handoff


def _audit(session, user, opening, handoff, action, extra=None):
    session.add(
        PracticeDashboardAuditEvent(
            actor_id=user.id, action=action, purpose="opportunity_handoff", target_type="opportunity",
            target_id=str(opening.id), reason="Company handoff.",
            event_metadata={"handoff_id": handoff.id if handoff else None, **(extra or {})},
        )
    )


async def _view(session, opening, handoff, can_update):
    rows = (
        await session.execute(
            sa.select(OpportunityHandoffCandidate, User.name, User.email, User.university)
            .join(User, User.id == OpportunityHandoffCandidate.student_id)
            .where(OpportunityHandoffCandidate.handoff_id == handoff.id)
            .order_by(User.name)
        )
    ).all()
    return {
        "opportunity_id": opening.id, "organisation": opening.organisation, "title": opening.title,
        "handoff": {
            "id": handoff.id, "status": handoff.status, "version": handoff.version,
            "contact_name": handoff.contact_name, "contact_email": handoff.contact_email,
            "channel": handoff.channel, "note": handoff.note, "shared_at": handoff.shared_at,
        },
        "candidates": [
            {"student_id": c.student_id, "name": n, "email": e, "college": u,
             "company_status": c.company_status, "status_text": STATUS_TEXT[c.company_status],
             "status_note": c.status_note, "updated_at": c.updated_at}
            for c, n, e, u in rows
        ],
        "statuses": [{"value": s, "label": STATUS_TEXT[s]} for s in COMPANY_STATUSES],
        "can_update": can_update,
    }


@router.get("/{opportunity_id}/handoff")
async def get_handoff(
    opportunity_id: int, response: fastapi.Response,
    user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session),
):
    opening, handoff = await _context(session, user, opportunity_id, "read")
    response.headers["Cache-Control"] = "no-store"
    sel = await selection.latest(session, opening.id)
    ready = sel is not None and sel.status in ("locked", "released")
    if handoff is None:
        return {"handoff": None, "selection_ready": ready, "organisation": opening.organisation}
    return await _view(session, opening, handoff, True) | {"selection_ready": ready}


@router.post("/{opportunity_id}/handoff", status_code=201)
async def create_handoff(
    opportunity_id: int, body: Create,
    user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session),
):
    opening, handoff = await _context(session, user, opportunity_id, "publish", lock=True)
    sel = await selection.latest(session, opening.id)
    if sel is None or sel.status not in ("locked", "released"):
        raise fastapi.HTTPException(409, "Lock the final selection before preparing the company list.")
    if handoff is not None and handoff.selection_id == sel.id:
        raise fastapi.HTTPException(409, "This list was already prepared.")
    included = (
        await session.execute(
            sa.select(OpportunitySelectionEntry.student_id).where(
                OpportunitySelectionEntry.selection_id == sel.id,
                OpportunitySelectionEntry.decision == "include",
            )
        )
    ).scalars().all()
    if not included:
        raise fastapi.HTTPException(409, "Nobody is included in the locked selection.")
    handoff = OpportunityHandoff(
        opportunity_id=opening.id, selection_id=sel.id, contact_name=body.contact_name,
        contact_email=body.contact_email, note=body.note, created_by=user.id,
    )
    session.add(handoff)
    await session.flush()
    session.add_all(
        OpportunityHandoffCandidate(handoff_id=handoff.id, student_id=sid) for sid in sorted(included)
    )
    _audit(session, user, opening, handoff, "opportunity_handoff_prepared", {"people": len(included)})
    await session.commit()
    return await _view(session, opening, handoff, True)


@router.get("/{opportunity_id}/handoff/export")
async def export_handoff(
    opportunity_id: int,
    user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session),
):
    opening, handoff = await _context(session, user, opportunity_id, "publish")
    if handoff is None:
        raise fastapi.HTTPException(404, "Prepare the company list first.")
    view = await _view(session, opening, handoff, True)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Name", "Email", "College", "Role", "Organisation"])
    for c in view["candidates"]:
        # Leading = + - @ would run as a formula in a spreadsheet; keep them as text.
        cells = [c["name"], c["email"], c["college"], opening.title, opening.organisation]
        writer.writerow([("'" + x) if isinstance(x, str) and x[:1] in "=+-@" else x for x in cells])
    _audit(session, user, opening, handoff, "opportunity_handoff_exported", {"people": len(view["candidates"])})
    await session.commit()
    return fastapi.Response(
        "﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="company-list-{opening.id}.csv"',
                 "Cache-Control": "no-store"},
    )


@router.post("/{opportunity_id}/handoff/shared")
async def mark_shared(
    opportunity_id: int, body: Share,
    user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session),
):
    opening, handoff = await _context(session, user, opportunity_id, "publish", lock=True)
    if handoff is None:
        raise fastapi.HTTPException(404, "Prepare the company list first.")
    if handoff.version != body.expected_version or handoff.status != "prepared":
        raise fastapi.HTTPException(409, "This list changed or was already marked as sent. Reload it.")
    handoff.status, handoff.channel, handoff.version = "shared", body.channel, handoff.version + 1
    handoff.shared_by, handoff.shared_at = user.id, dt.datetime.now(UTC)
    if body.note:
        handoff.note = body.note
    await session.execute(
        sa.update(OpportunityHandoffCandidate)
        .where(OpportunityHandoffCandidate.handoff_id == handoff.id,
               OpportunityHandoffCandidate.company_status == "not_sent")
        .values(company_status="sent", updated_by=user.id, updated_at=dt.datetime.now(UTC))
    )
    _audit(session, user, opening, handoff, "opportunity_handoff_shared", {"channel": body.channel})
    await session.commit()
    return await _view(session, opening, handoff, True)


@router.put("/{opportunity_id}/handoff/candidates/{student_id}")
async def set_status(
    opportunity_id: int, student_id: int, body: StatusChange,
    user=fastapi.Depends(get_current_admin_user), session=fastapi.Depends(get_async_session),
):
    opening, handoff = await _context(session, user, opportunity_id, "update", lock=True)
    if handoff is None:
        raise fastapi.HTTPException(404, "Prepare the company list first.")
    if handoff.version != body.expected_version:
        raise fastapi.HTTPException(409, "This list changed. Reload it.")
    if handoff.status != "shared":
        raise fastapi.HTTPException(409, "Mark the list as sent to the company first.")
    row = (
        await session.execute(
            sa.select(OpportunityHandoffCandidate).where(
                OpportunityHandoffCandidate.handoff_id == handoff.id,
                OpportunityHandoffCandidate.student_id == student_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "This person is not on the company list.")
    row.company_status, row.status_note = body.company_status, body.note
    row.updated_by, row.updated_at = user.id, dt.datetime.now(UTC)
    _audit(session, user, opening, handoff, "opportunity_handoff_status", {"student_id": student_id, "status": body.company_status})
    await session.commit()
    return await _view(session, opening, handoff, True)
