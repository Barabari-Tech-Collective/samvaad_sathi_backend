"""Two first requests for one new account must not end in a 500 (300-student load test)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import sqlalchemy.exc

from src.repository.crud.user import UserCRUDRepository
from src.utilities.exceptions.database import EntityAlreadyExists


def test_losing_the_insert_race_reports_the_user_exists():
    session = MagicMock()
    session.execute = AsyncMock(return_value=SimpleNamespace(scalar=lambda: None))
    session.commit = AsyncMock(side_effect=sqlalchemy.exc.IntegrityError("insert", {}, Exception("ix_user_email")))
    session.rollback = AsyncMock()
    repo = UserCRUDRepository(async_session=session)
    with pytest.raises(EntityAlreadyExists):
        asyncio.run(repo.create_user(email="New@Example.org", password="", name="new"))
    session.rollback.assert_awaited_once()
