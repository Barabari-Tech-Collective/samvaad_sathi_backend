"""Refresh-token rotation: the grace window and the cleanup that pays for it.

Rotation supersedes the old refresh token by bringing its expiry forward rather than
deleting it, so that requests already in flight with that token still succeed instead
of the first rotation winning and the rest 401-ing their way into a logout.

Two things follow from that choice and are covered here: the shortening must never
run backwards and hand a nearly-dead token extra life, and because nothing deletes
rows any more, something has to - otherwise the table only ever grows and
get_session_by_token pays for it on every single refresh.
"""

import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.models.db.session import Session
from src.repository.crud.session import SessionCRUDRepository


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _repo(rowcount: int = 0) -> SessionCRUDRepository:
    result = MagicMock()
    result.rowcount = rowcount

    async_session = MagicMock()
    async_session.add = MagicMock()
    async_session.commit = AsyncMock()
    async_session.execute = AsyncMock(return_value=result)

    return SessionCRUDRepository(async_session)


@pytest.mark.asyncio
async def test_expire_session_soon_shortens_a_long_lived_session():
    repo = _repo()
    session = Session(user_id=1, token="t", expiry=_now() + datetime.timedelta(days=7))

    await repo.expire_session_soon(session=session, grace_seconds=15)

    remaining = (session.expiry - _now()).total_seconds()
    assert 0 < remaining <= 15
    repo.async_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_expire_session_soon_never_extends_a_nearly_expired_session():
    """A token already closer to expiry than the grace window keeps its own expiry.

    Without this the grace window becomes a renewal: a token three seconds from death
    would be handed another fifteen on every refresh.
    """
    repo = _repo()
    original = _now() + datetime.timedelta(seconds=3)
    session = Session(user_id=1, token="t", expiry=original)

    await repo.expire_session_soon(session=session, grace_seconds=15)

    assert session.expiry == original
    repo.async_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_expire_session_soon_leaves_an_already_expired_session_alone():
    repo = _repo()
    original = _now() - datetime.timedelta(hours=1)
    session = Session(user_id=1, token="t", expiry=original)

    await repo.expire_session_soon(session=session, grace_seconds=15)

    assert session.expiry == original
    repo.async_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_expired_sessions_is_scoped_to_the_user_and_to_expired_rows():
    """Guards the WHERE clause, not just that a statement was issued.

    Dropping either condition is quiet and bad: without the user filter this deletes
    other people's sessions, and without the expiry filter it deletes live ones and
    logs the user out mid-session.
    """
    repo = _repo(rowcount=2)

    deleted = await repo.delete_expired_sessions(user_id=7)

    assert deleted == 2
    statement = repo.async_session.execute.await_args.kwargs["statement"]
    compiled = str(statement.compile(compile_kwargs={"literal_binds": False})).lower()
    assert compiled.startswith("delete from session")
    assert "user_id" in compiled
    assert "expiry <" in compiled


@pytest.mark.asyncio
async def test_delete_expired_sessions_reports_zero_when_nothing_matched():
    repo = _repo(rowcount=0)
    assert await repo.delete_expired_sessions(user_id=7) == 0
