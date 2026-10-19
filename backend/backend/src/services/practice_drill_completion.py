"""Record Practice drill completions without double-counting client retries."""

from __future__ import annotations

import sqlalchemy
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.practice_drill_completion import PracticeDrillCompletion
from src.models.schemas.interview import CompletePracticeDrillRequest


async def record_practice_drill_completion(
    session: AsyncSession,
    *,
    user_id: int,
    payload: CompletePracticeDrillRequest,
) -> tuple[PracticeDrillCompletion, bool]:
    """Create one completion or return the existing row for an idempotent retry."""

    if payload.assignment_id is not None:
        from src.services.coaching_assignments import complete_assignment
        return await complete_assignment(session, user_id=user_id, payload=payload)

    existing_stmt = sqlalchemy.select(PracticeDrillCompletion).where(
        PracticeDrillCompletion.user_id == user_id,
        PracticeDrillCompletion.client_completion_id == payload.client_completion_id,
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        return existing, False

    completion = PracticeDrillCompletion(
        user_id=user_id,
        module=payload.module,
        level=payload.level,
        client_completion_id=payload.client_completion_id,
        evidence_kind=payload.evidence_kind,
        evidence_units=payload.evidence_units,
    )
    session.add(completion)
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent retry may win the unique constraint between our read and
        # insert. Roll back only this request, then return the committed row.
        await session.rollback()
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing is None:
            raise
        return existing, False

    await session.refresh(completion)
    return completion, True
