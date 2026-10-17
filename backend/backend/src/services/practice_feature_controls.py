"""Fail-safe runtime checks for Practice operational controls."""

import fastapi
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.practice_governance import PracticeFeatureControl


async def require_practice_feature(session: AsyncSession, key: str) -> None:
    """Allow an unset control, but stop work when an operator explicitly pauses it."""

    control = await session.get(PracticeFeatureControl, key)
    if control is not None and not control.enabled:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PRACTICE_FEATURE_PAUSED",
                "feature": key,
                "message": "This Practice feature is temporarily paused while the team resolves an issue. Saved work is safe; please try again later.",
            },
        )
