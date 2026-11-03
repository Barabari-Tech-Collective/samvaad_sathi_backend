"""Unit tests for programme learner roster import (ADR-2026-11-03)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import fastapi
import pytest
from fastapi.testclient import TestClient

from src.api.dependencies.admin import get_current_admin_user
from src.api.dependencies.session import get_async_session
from src.api.routes import program_learners
from src.services import programme_roster as service


class MockResult:
    def __init__(self, item=None):
        self._item = item

    def scalars(self):
        return self

    def first(self):
        return self._item


@pytest.mark.asyncio
async def test_import_learners_new_invitations():
    session = AsyncMock()
    session.add = MagicMock()
    # No existing user, no existing roster member
    session.execute.return_value = MockResult(None)

    rows = [
        {"name": "Aisha Khan", "email": "aisha@example.com", "sourceRow": 2},
        {"name": "Rahul Sharma", "email": "rahul@example.com", "sourceRow": 3},
    ]

    res = await service.import_programme_learners(
        session=session,
        user=SimpleNamespace(id=42),
        rows=rows,
    )

    assert res["accepted"] == 2
    assert res["invited"] == 2
    assert res["invitationResent"] == 0
    assert res["alreadyReady"] == 0
    assert res["manualReview"] == 0
    assert len(res["results"]) == 2
    assert res["results"][0] == {"row": 2, "status": "invited"}
    assert res["results"][1] == {"row": 3, "status": "invited"}
    assert session.commit.called


@pytest.mark.asyncio
async def test_import_learners_reinvite_and_existing_active_user():
    session = AsyncMock()
    session.add = MagicMock()

    existing_user = SimpleNamespace(id=99, email="active@example.com")
    existing_roster_member = SimpleNamespace(
        id=10,
        email="reinvite@example.com",
        invite_status="invited",
        claimed_user_id=None,
        claimed_at=None,
    )

    call_count = 0

    def mock_execute_side_effect(stmt):
        nonlocal call_count
        call_count += 1
        # Row 1: active@example.com -> existing_user is found
        if call_count == 1:
            return MockResult(existing_user)
        if call_count == 2:
            return MockResult(None)
        # Row 2: reinvite@example.com -> no active user, but existing roster member found
        if call_count == 3:
            return MockResult(None)
        if call_count == 4:
            return MockResult(existing_roster_member)
        return MockResult(None)

    session.execute.side_effect = mock_execute_side_effect

    rows = [
        {"name": "Active Learner", "email": "active@example.com", "sourceRow": 2},
        {"name": "Reinvited Learner", "email": "reinvite@example.com", "sourceRow": 3},
    ]

    res = await service.import_programme_learners(
        session=session,
        user=SimpleNamespace(id=1),
        rows=rows,
    )

    assert res["accepted"] == 2
    assert res["invited"] == 0
    assert res["alreadyReady"] == 1
    assert res["invitationResent"] == 1
    assert res["manualReview"] == 0
    assert res["results"][0]["status"] == "already_ready"
    assert res["results"][1]["status"] == "invitation_resent"


@pytest.mark.asyncio
async def test_import_learners_invalid_email_goes_to_manual_review():
    session = AsyncMock()

    rows = [
        {"name": "Bad Email", "email": "not-an-email", "sourceRow": 2},
        {"name": "", "email": "valid@example.com", "sourceRow": 3},
    ]

    res = await service.import_programme_learners(
        session=session,
        user=SimpleNamespace(id=1),
        rows=rows,
    )

    assert res["accepted"] == 0
    assert res["manualReview"] == 2
    assert res["results"][0] == {"row": 2, "status": "manual_review"}
    assert res["results"][1] == {"row": 3, "status": "manual_review"}


def test_api_route_import_contract_with_wrapped_payload():
    """Verify that POST /v2/program-learners/import accepts { data: { rows: [...] } }

    and returns { isSuccess: true, isError: false, errorMessage: null, data: {...} }.
    """
    app = fastapi.FastAPI()
    app.include_router(program_learners.router)

    app.dependency_overrides[get_current_admin_user] = lambda: SimpleNamespace(id=1, email="admin@example.com")

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute.return_value = MockResult(None)
    app.dependency_overrides[get_async_session] = lambda: mock_session

    client = TestClient(app)

    payload = {
        "data": {
            "rows": [
                {"name": "Aisha Khan", "email": "aisha@example.com"},
                {"name": "Rahul Sharma", "email": "rahul@example.com"},
            ]
        }
    }

    resp = client.post("/v2/program-learners/import", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["isSuccess"] is True
    assert data["isError"] is False
    assert data["errorMessage"] is None
    assert "data" in data
    assert data["data"]["accepted"] == 2
    assert data["data"]["invited"] == 2
    assert len(data["data"]["results"]) == 2


def test_api_route_import_contract_with_direct_rows():
    """Verify that POST /v2/program-learners/import also accepts top-level { rows: [...] }."""
    app = fastapi.FastAPI()
    app.include_router(program_learners.router)

    app.dependency_overrides[get_current_admin_user] = lambda: SimpleNamespace(id=1, email="admin@example.com")

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute.return_value = MockResult(None)
    app.dependency_overrides[get_async_session] = lambda: mock_session

    client = TestClient(app)

    payload = {
        "rows": [
            {"name": "Pooja Patel", "email": "pooja@example.com"},
        ]
    }

    resp = client.post("/v2/program-learners/import", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["isSuccess"] is True
    assert data["data"]["accepted"] == 1
    assert data["data"]["invited"] == 1
