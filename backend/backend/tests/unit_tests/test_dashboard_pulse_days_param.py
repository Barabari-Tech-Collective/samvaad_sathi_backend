"""The Overview's period buttons send ?days=7 / ?days=30 as text in the query string."""

from unittest.mock import MagicMock

import fastapi
import pytest
from fastapi.testclient import TestClient

from src.api.routes import analytics_v2


def _user():
    return MagicMock(id=1, is_admin=True, is_super_admin=True)


def _client(monkeypatch) -> TestClient:
    """Route wired with stubs, so only parameter handling is exercised."""
    app = fastapi.FastAPI()
    app.include_router(analytics_v2.router)
    app.dependency_overrides[analytics_v2.get_current_user] = lambda: _user()
    app.dependency_overrides[analytics_v2.get_async_session] = lambda: MagicMock()

    async def no_colleges(*_a, **_k):
        return set()  # empty allow-list -> no interviews fetched

    monkeypatch.setattr(analytics_v2, "get_user_allowed_colleges", no_colleges)

    class _Stub:
        def __init__(self, *_a, **_k):
            pass

        async def _reports_by_interview(self, _ids):
            return {}

        async def _summary_reports_by_interview(self, _ids):
            return {}

    monkeypatch.setattr(analytics_v2, "AnalyticsService", _Stub)
    return TestClient(app)


def _pulse_path() -> str:
    return next(
        r.path for r in analytics_v2.router.routes if r.path.endswith("/dashboard/pulse")
    )


@pytest.mark.parametrize("days", [7, 30])
def test_pulse_accepts_supported_periods_from_the_query_string(monkeypatch, days):
    response = _client(monkeypatch).get(_pulse_path(), params={"days": days})
    assert response.status_code == 200, response.text
    assert response.json()["days"] == days


def test_pulse_defaults_to_seven_days(monkeypatch):
    response = _client(monkeypatch).get(_pulse_path())
    assert response.status_code == 200
    assert response.json()["days"] == 7


@pytest.mark.parametrize("days", ["1", "14", "0", "abc"])
def test_pulse_rejects_unsupported_periods(monkeypatch, days):
    response = _client(monkeypatch).get(_pulse_path(), params={"days": days})
    assert response.status_code == 422
