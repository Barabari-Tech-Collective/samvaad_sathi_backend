from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from src.repository.crud.pacing_practice import PacingPracticeSessionCRUDRepository
from src.models.schemas.pacing_practice import PacingPracticeSessionCreateRequest


def result(row):
    return SimpleNamespace(scalar_one_or_none=lambda: row)


def setup_repo(rows):
    db = SimpleNamespace(execute=AsyncMock(side_effect=[result(row) for row in rows]), add=Mock(), commit=AsyncMock(), rollback=AsyncMock(), refresh=AsyncMock())
    return PacingPracticeSessionCRUDRepository(db), db


@pytest.mark.asyncio
async def test_replay_keeps_original_prompt_even_when_random_choice_changes():
    old = SimpleNamespace(level=1, id=4, prompt_text="original")
    repo, db = setup_repo([old])
    row = await repo.create_session(user_id=1, level=1, prompt_text="different", prompt_index=9, client_request_id=str(uuid4()))
    assert row is old and row.prompt_text == "original" and row._creation_replayed
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_receipt_different_level_rejected():
    repo, db = setup_repo([SimpleNamespace(level=2)])
    with pytest.raises(HTTPException) as exc:
        await repo.create_session(user_id=1, level=1, prompt_text="x", prompt_index=0, client_request_id=str(uuid4()))
    assert exc.value.status_code == 409
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_insert_loser_recovers_winning_receipt():
    old = SimpleNamespace(level=1, id=8)
    repo, db = setup_repo([None, old])
    db.commit.side_effect = IntegrityError("insert", {}, Exception())
    row = await repo.create_session(user_id=1, level=1, prompt_text="x", prompt_index=0, client_request_id=str(uuid4()))
    assert row is old
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_unrelated_integrity_error_is_not_disguised_as_success():
    repo, db = setup_repo([None, None])
    db.commit.side_effect = IntegrityError("insert", {}, Exception())
    with pytest.raises(IntegrityError):
        await repo.create_session(user_id=1, level=1, prompt_text="x", prompt_index=0, client_request_id=str(uuid4()))


@pytest.mark.asyncio
async def test_query_binds_account_and_key():
    repo, db = setup_repo([None])
    key = str(uuid4())
    await repo.create_session(user_id=42, level=1, prompt_text="x", prompt_index=0, client_request_id=key)
    params = db.execute.call_args.args[0].compile().params
    assert 42 in params.values() and key in params.values()
    assert db.add.call_args.args[0].client_request_id == key


def test_schema_accepts_uuid_and_rejects_arbitrary_request_keys():
    key = str(uuid4())
    assert str(PacingPracticeSessionCreateRequest(level=1, client_request_id=key).client_request_id) == key
    with pytest.raises(ValueError):
        PacingPracticeSessionCreateRequest(level=1, client_request_id="not-a-key")


@pytest.mark.asyncio
async def test_submission_read_reserves_row_without_waiting():
    from sqlalchemy.dialects import postgresql
    repo, db = setup_repo([SimpleNamespace(id=1)])
    await repo.get_by_id_and_user(session_id=1, user_id=42, for_update=True)
    sql = str(db.execute.call_args.kwargs["statement"].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE NOWAIT" in sql
    assert "user_id" in sql


@pytest.mark.asyncio
async def test_busy_submission_returns_conflict_and_rolls_back():
    from sqlalchemy.exc import DBAPIError
    class Busy(Exception):
        sqlstate = "55P03"
    repo, db = setup_repo([])
    db.execute.side_effect = DBAPIError("select", {}, Busy())
    with pytest.raises(HTTPException) as exc:
        await repo.get_by_id_and_user(session_id=1, user_id=42, for_update=True)
    assert exc.value.status_code == 409
    db.rollback.assert_awaited_once()
