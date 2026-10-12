"""The question-generation lock must really exclude concurrent generation.

`test_question_snapshot.py` proves every writer *calls* the lock and that the
response builder cannot mix one request's text with another's IDs. Neither of
those checks that the lock actually serializes - a typo in the SQL, or the
non-transactional `pg_advisory_lock` in place of `pg_advisory_xact_lock`, would
pass both while leaving production exactly as racy as before.

So this exercises the real Postgres behaviour on two real connections: a second
request for the same interview must wait, a request for a different interview
must not, and a generation that fails must release the lock rather than wedging
that interview forever.

Advisory locks are keyed on plain integers and need no rows, so this runs
against any empty database. Set SCHEMA_TEST_DATABASE_URL to enable; CI always
provides it.
"""

import asyncio
import os
import random

import pytest
import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine

from src.services.question_snapshot import (
    QUESTION_GENERATION_LOCK_NAMESPACE,
    lock_question_generation,
)

_RAW_URL = os.environ.get("SCHEMA_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="Set SCHEMA_TEST_DATABASE_URL to a throwaway PostgreSQL database to run",
)


def _async_url(url: str) -> str:
    """CI hands us a psycopg2 URL; the application and this lock are async."""
    return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.fixture
async def engine():
    # NullPool: these tests depend on holding two distinct connections at once
    # and on a connection's locks dropping when its transaction ends, so pooled
    # connection reuse would make the results ambiguous.
    engine = create_async_engine(_async_url(_RAW_URL), poolclass=sqlalchemy.pool.NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
def interview_id() -> int:
    """A key no concurrently running test shares.

    The suite runs under pytest-xdist, and advisory locks are cluster-wide, so a
    fixed id would have tests blocking on each other rather than on the code
    under test.
    """
    return random.randint(10_000_000, 99_999_999)


async def _try_lock(connection, interview_id: int) -> bool:
    """Non-blocking probe for the same lock the helper takes."""
    result = await connection.execute(
        sqlalchemy.text(
            "SELECT pg_try_advisory_xact_lock(:namespace, :interview_id)"
        ).bindparams(namespace=QUESTION_GENERATION_LOCK_NAMESPACE, interview_id=interview_id)
    )
    return bool(result.scalar())


async def test_second_request_for_the_same_interview_is_excluded(engine, interview_id):
    """While one request generates, another cannot start generating."""
    async with engine.connect() as holder:
        await holder.begin()
        await lock_question_generation(holder, interview_id=interview_id)

        async with engine.connect() as contender:
            await contender.begin()
            assert await _try_lock(contender, interview_id) is False, (
                "A second request acquired the generation lock while the first held it. "
                "Both would read no existing questions and both would insert a set."
            )


async def test_a_different_interview_is_not_blocked(engine, interview_id):
    """Serialization is per interview, not global - students must not queue behind each other."""
    async with engine.connect() as holder:
        await holder.begin()
        await lock_question_generation(holder, interview_id=interview_id)

        async with engine.connect() as other_student:
            await other_student.begin()
            assert await _try_lock(other_student, interview_id + 1) is True


async def test_lock_is_released_on_commit(engine, interview_id):
    """The lock must end with the transaction that persisted the questions."""
    async with engine.connect() as holder:
        transaction = await holder.begin()
        await lock_question_generation(holder, interview_id=interview_id)
        await transaction.commit()

        async with engine.connect() as contender:
            await contender.begin()
            assert await _try_lock(contender, interview_id) is True


async def test_lock_is_released_on_rollback(engine, interview_id):
    """A failed generation must not wedge the interview forever.

    If this used the session-scoped `pg_advisory_lock`, a request that raised
    mid-generation would hold the lock until its connection was recycled, and
    every retry for that interview would hang. That is a worse failure than the
    race it replaces, because the student cannot get out of it.
    """
    async with engine.connect() as holder:
        transaction = await holder.begin()
        await lock_question_generation(holder, interview_id=interview_id)
        await transaction.rollback()

        async with engine.connect() as contender:
            await contender.begin()
            assert await _try_lock(contender, interview_id) is True


async def test_waiting_request_proceeds_once_the_first_commits(engine, interview_id):
    """The helper blocks rather than returning, then proceeds when released.

    This is the behaviour the endpoints rely on: the second request waits, and
    when it finally reads the question table it sees the first request's rows
    and returns those instead of generating its own.
    """
    async with engine.connect() as holder:
        holder_transaction = await holder.begin()
        await lock_question_generation(holder, interview_id=interview_id)

        async with engine.connect() as waiter:
            await waiter.begin()
            attempt = asyncio.create_task(
                lock_question_generation(waiter, interview_id=interview_id)
            )

            # Must still be waiting: if the helper returned here, two requests
            # would be generating for the same interview simultaneously.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(attempt), timeout=1.0)

            await holder_transaction.commit()

            # Now it should get through promptly.
            await asyncio.wait_for(attempt, timeout=10.0)


async def test_lock_uses_the_reserved_namespace(engine, interview_id):
    """The namespace keeps interview ids from colliding with other lock callers.

    A bare `pg_advisory_xact_lock(interview_id)` would occupy the single-key
    space, where interview 42 and any future caller's id 42 are the same lock.
    Two-key locks report the namespace as `classid`, so this reads it back.
    """
    async with engine.connect() as holder:
        await holder.begin()
        await lock_question_generation(holder, interview_id=interview_id)

        result = await holder.execute(
            sqlalchemy.text(
                "SELECT classid, objid FROM pg_locks "
                "WHERE locktype = 'advisory' AND objsubid = 2 AND pid = pg_backend_pid()"
            )
        )
        held = result.all()

    assert (QUESTION_GENERATION_LOCK_NAMESPACE, interview_id) in [
        (row.classid, row.objid) for row in held
    ], f"Expected a two-key advisory lock in namespace {QUESTION_GENERATION_LOCK_NAMESPACE}, got {held}"
