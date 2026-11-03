"""The signed-in student's project and experience context (architecture pack F10).

Write, edit and confirm. Only confirmed items shape generated practice questions, and editing a
confirmed item returns it to draft. Questions already generated for an interview are never
rewritten by a later change."""

import datetime as dt
from typing import Literal

import fastapi
import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator

from src.api.dependencies.auth import get_current_user
from src.api.dependencies.session import get_async_session
from src.models.db.learner_context import LearnerContext
from src.services.learner_context import LIMIT

router = fastapi.APIRouter(prefix="/v2/profile-context", tags=["profile-context"])


def _trim(value):
    return value.strip() if isinstance(value, str) else value


class Fields(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    summary: str = Field(min_length=20, max_length=1500)

    _trim = field_validator("title", "summary", mode="before")(classmethod(lambda c, v: _trim(v)))


class Create(Fields):
    kind: Literal["project", "experience"]


class Edit(Fields):
    expected_version: int = Field(ge=1)


class Versioned(BaseModel):
    expected_version: int = Field(ge=1)


def view(r):
    return {
        "id": r.id, "kind": r.kind, "title": r.title, "summary": r.summary,
        "status": r.status, "version": r.version, "confirmed_at": r.confirmed_at,
    }


async def mine(session, user, item_id):
    row = (
        await session.execute(
            sa.select(LearnerContext)
            .where(LearnerContext.id == item_id, LearnerContext.user_id == user.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise fastapi.HTTPException(404, "Not found.")
    return row


@router.get("/me")
async def list_mine(
    response: fastapi.Response,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await session.execute(
            sa.select(LearnerContext).where(LearnerContext.user_id == user.id).order_by(LearnerContext.id)
        )
    ).scalars().all()
    return {
        "items": [view(r) for r in rows],
        "limit": LIMIT,
        "notice": "Only items you confirm are used to shape practice questions.",
    }


@router.post("/me", status_code=201)
async def create(
    body: Create,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    count = (
        await session.execute(
            sa.select(sa.func.count(LearnerContext.id)).where(LearnerContext.user_id == user.id)
        )
    ).scalar_one()
    if count >= LIMIT:
        raise fastapi.HTTPException(409, f"You can keep up to {LIMIT} items. Remove one first.")
    row = LearnerContext(user_id=user.id, **body.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return view(row)


@router.put("/me/{item_id}")
async def edit(
    item_id: int,
    body: Edit,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    row = await mine(session, user, item_id)
    if row.version != body.expected_version:
        raise fastapi.HTTPException(409, "This item changed. Refresh before saving.")
    row.title, row.summary = body.title, body.summary
    # What the questions use is what you last confirmed: an edit needs confirming again.
    row.status, row.confirmed_at, row.version = "draft", None, row.version + 1
    await session.commit()
    await session.refresh(row)
    return view(row)


@router.post("/me/{item_id}/confirm")
async def confirm(
    item_id: int,
    body: Versioned,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    row = await mine(session, user, item_id)
    if row.version != body.expected_version:
        raise fastapi.HTTPException(409, "This item changed. Refresh before confirming.")
    row.status, row.confirmed_at = "confirmed", dt.datetime.now(dt.timezone.utc)
    row.version += 1
    await session.commit()
    await session.refresh(row)
    return view(row)


@router.delete("/me/{item_id}", status_code=204)
async def remove(
    item_id: int,
    user=fastapi.Depends(get_current_user),
    session=fastapi.Depends(get_async_session),
):
    row = await mine(session, user, item_id)
    await session.delete(row)
    await session.commit()
    return fastapi.Response(status_code=204)
