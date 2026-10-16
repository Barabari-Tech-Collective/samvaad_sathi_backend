"""Serialization and response assembly for an interview's question set.

An interview's questions are generated once and then fixed for its lifetime:
every later read - a refresh, a reconnect, the report - has to see exactly the
text the student was asked. Two things used to break that promise.

First, three endpoints insert into `interview_question` for the same interview
(the v1 generator, the v2 generator and the non-tech v2 generator) and only one
of them took a lock, so a retry or a mix of v1/v2 traffic could generate twice.

Second, and the reason students saw questions change: the response was built by
zipping freshly generated LLM output against the rows actually in the database,
by list index. When a concurrent request had already persisted its own set, the
reply carried this request's text under the other request's question IDs. The
student answered text that was never stored against those IDs, and a refresh -
which reads the rows - showed different questions.

Both holes close the same way, so both live here: acquire the lock through
`lock_question_generation`, and build the reply through
`question_items_from_persisted`, which can only see persisted rows.
"""

import logging
from typing import Any, Sequence

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.interview_question import InterviewQuestion

logger = logging.getLogger(__name__)

#: First key of the two-integer Postgres advisory lock space, reserved for
#: question generation. Advisory locks share one cluster-wide namespace, so a
#: bare `pg_advisory_xact_lock(interview_id)` would collide with any future
#: caller that happens to lock on an unrelated id of the same value. Namespacing
#: keeps interview ids from meaning anything on their own.
QUESTION_GENERATION_LOCK_NAMESPACE = 1


async def lock_question_generation(session: AsyncSession, *, interview_id: int) -> None:
    """Serialize question generation for one interview across all processes.

    Blocks until no other transaction is generating for this interview. The lock
    is transaction scoped, so it releases on the commit that persists the
    questions (or on rollback) without needing an explicit unlock - but that
    also means callers must not commit between taking the lock and the insert,
    or they hand the lock to a racing request mid-generation.

    Every endpoint that inserts into `interview_question` must call this, or the
    ones that do are serializing against nothing.
    """
    await session.execute(
        sqlalchemy.text("SELECT pg_advisory_xact_lock(:namespace, :interview_id)").bindparams(
            namespace=QUESTION_GENERATION_LOCK_NAMESPACE, interview_id=interview_id
        )
    )


def question_items_from_persisted(
    questions: Sequence[Any],
    *,
    difficulty: str | None = None,
    supplements: dict[int, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build response items from persisted rows, and only from persisted rows.

    Text, topic, category and ids all come from `questions`, so what a student
    is shown is by construction what a later read of the same rows returns.
    Callers pass no generated text at all - that is the point of the function.

    `difficulty` is the interview's, applied to every item: difficulty is not a
    column on `interview_question`, so a per-question value could not survive a
    refresh even if we had one to report.
    """
    supplements = supplements or {}
    return [
        {
            "interviewQuestionId": question.id,
            "text": question.text,
            "topic": question.topic,
            "difficulty": difficulty,
            "category": question.category,
            "isFollowUp": question.is_follow_up,
            "parentQuestionId": question.parent_question_id,
            "followUpStrategy": question.follow_up_strategy,
            "supplement": supplements.get(question.id),
            "audioUrl": question.audio_url,
        }
        for question in questions
    ]
