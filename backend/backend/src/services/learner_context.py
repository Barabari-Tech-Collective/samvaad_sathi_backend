"""Confirmed learner context for generated questions (architecture pack F10)."""

import sqlalchemy as sa

from src.models.db.learner_context import LearnerContext

LIMIT = 10


async def confirmed_text(session, user_id: int) -> str:
    """The student's confirmed items as plain text for a question-generation prompt.

    It is the student's own writing, passed as data. The prompt already treats context as
    material to read, never as instructions."""
    rows = (
        await session.execute(
            sa.select(LearnerContext)
            .where(LearnerContext.user_id == user_id, LearnerContext.status == "confirmed")
            .order_by(LearnerContext.id)
            .limit(LIMIT)
        )
    ).scalars().all()
    if not rows:
        return ""
    lines = [f"- {r.kind.capitalize()}: {r.title}. {r.summary}" for r in rows]
    return "Confirmed by the student:\n" + "\n".join(lines)
