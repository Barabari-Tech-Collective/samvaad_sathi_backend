"""Who may see accommodation details, and what everyone else may see (architecture pack F13)."""

import sqlalchemy as sa

from src.models.db.accommodation import CONDITIONS, AccommodationRequest
from src.services.feature_access import permits


def owner(user, operation="read") -> bool:
    """Restricted capability: an explicit grant, not merely being an administrator."""
    return isinstance(user.feature_permissions, dict) and permits(
        user.feature_permissions, "accommodations", operation
    )


async def confirmed_conditions(session, student_id: int) -> list[dict]:
    """Conditions the student confirmed, as plain labels. Safe to show an interviewer or a
    reviewer who needs to know; the request text is never included."""
    rows = (
        await session.execute(
            sa.select(AccommodationRequest.conditions).where(
                AccommodationRequest.student_id == student_id,
                AccommodationRequest.status == "approved",
                AccommodationRequest.confirmed_at.is_not(None),
            )
        )
    ).scalars().all()
    codes = []
    for conditions in rows:
        for code in conditions or []:
            if code in CONDITIONS and code not in codes:
                codes.append(code)
    return [{"code": c, "label": CONDITIONS[c]} for c in codes]
