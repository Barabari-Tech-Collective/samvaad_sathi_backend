"""A database failure while checking someone's team is a temporary error, never "no team".

It used to answer "no memberships", so a signed-in staff member got 403 "Administrator access
required" (or a menu with nothing in it) whenever the database hiccuped, with nothing in the logs.
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import fastapi
import pytest
from sqlalchemy.exc import OperationalError

from src.api.dependencies.admin import (
    get_current_admin_user,
    get_current_creator_user,
    get_user_active_memberships,
)


def person(user_id=7):
    return SimpleNamespace(id=user_id, is_admin=False, is_super_admin=False, email="staff@example.org")


def failing_session():
    session = MagicMock()
    session.execute = AsyncMock(side_effect=OperationalError("SELECT 1", {}, Exception("too many connections")))
    return session


def empty_session():
    session = MagicMock()
    result = MagicMock()
    result.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_a_database_error_is_a_503_that_clients_retry_and_it_is_logged(caplog):
    with caplog.at_level(logging.ERROR), pytest.raises(fastapi.HTTPException) as err:
        await get_user_active_memberships(7, failing_session())
    assert err.value.status_code == 503
    assert "try again" in err.value.detail
    assert "Team membership lookup failed for user_id=7" in caplog.text


@pytest.mark.asyncio
async def test_staff_are_not_told_they_lack_admin_access_when_the_database_hiccups():
    with pytest.raises(fastapi.HTTPException) as admin:
        await get_current_admin_user(current_user=person(), session=failing_session())
    with pytest.raises(fastapi.HTTPException) as creator:
        await get_current_creator_user(current_user=person(), session=failing_session())
    assert admin.value.status_code == 503
    assert creator.value.status_code == 503


@pytest.mark.asyncio
async def test_someone_with_genuinely_no_team_is_still_refused():
    with pytest.raises(fastapi.HTTPException) as admin:
        await get_current_admin_user(current_user=person(), session=empty_session())
    with pytest.raises(fastapi.HTTPException) as creator:
        await get_current_creator_user(current_user=person(), session=empty_session())
    assert admin.value.status_code == 403
    assert creator.value.status_code == 403


@pytest.mark.asyncio
async def test_no_user_id_or_no_session_is_still_an_empty_answer():
    assert await get_user_active_memberships(0, empty_session()) == []
    assert await get_user_active_memberships(7, None) == []
