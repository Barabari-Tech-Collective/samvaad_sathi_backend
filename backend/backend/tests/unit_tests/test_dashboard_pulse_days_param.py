"""The Overview's period buttons send ?days=7 / ?days=30 as text in the query string."""

from unittest.mock import AsyncMock, MagicMock

import fastapi
import pytest
from fastapi.testclient import TestClient

from src.api.routes import analytics_v2


def _user():
    return MagicMock(id=1, is_admin=True, is_super_admin=True)


def _client(monkeypatch, allowed=frozenset(), signups=()) -> TestClient:
    """Route wired with stubs, so only parameter handling is exercised."""
    app = fastapi.FastAPI()
    app.include_router(analytics_v2.router)
    app.dependency_overrides[analytics_v2.get_current_user] = lambda: _user()
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(signups)
    session.execute = AsyncMock(return_value=result)
    app.dependency_overrides[analytics_v2.get_async_session] = lambda: session

    async def colleges(*_a, **_k):
        return None if allowed is None else set(allowed)

    monkeypatch.setattr(analytics_v2, "get_user_allowed_colleges", colleges)

    class _Stub:
        def __init__(self, *_a, **_k):
            pass

        async def _list_interviews_all(self, **_k):
            return []

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


@pytest.mark.parametrize("days", [1, 3, 7, 15, 30])
def test_pulse_accepts_supported_periods_from_the_query_string(monkeypatch, days):
    response = _client(monkeypatch).get(_pulse_path(), params={"days": days})
    assert response.status_code == 200, response.text
    assert response.json()["days"] == days


def test_pulse_defaults_to_seven_days(monkeypatch):
    response = _client(monkeypatch).get(_pulse_path())
    assert response.status_code == 200
    assert response.json()["days"] == 7


@pytest.mark.parametrize("days", ["2", "14", "0", "abc"])
def test_pulse_rejects_unsupported_periods(monkeypatch, days):
    response = _client(monkeypatch).get(_pulse_path(), params={"days": days})
    assert response.status_code == 422


def test_count_by_ist_day_buckets_by_indian_calendar_day():
    import datetime as dt

    utc = dt.timezone.utc
    counts = analytics_v2._count_by_ist_day(
        [
            # 19:00 UTC on 1 Nov is 00:30 IST on 2 Nov: the Indian day, not the UTC day.
            dt.datetime(2026, 11, 1, 19, 0, tzinfo=utc),
            dt.datetime(2026, 11, 2, 3, 0, tzinfo=utc),
            dt.datetime(2026, 11, 1, 5, 0, tzinfo=utc),
            # No zone: taken as UTC.
            dt.datetime(2026, 11, 1, 5, 30),
            None,
        ]
    )
    assert counts == {dt.date(2026, 11, 2): 2, dt.date(2026, 11, 1): 2}


def test_pulse_counts_sign_ups_for_the_period_and_the_one_before(monkeypatch):
    import datetime as dt

    today = dt.datetime.now(analytics_v2._IST).replace(hour=12, minute=0, second=0, microsecond=0)
    signups = [
        today,
        today,
        today - dt.timedelta(days=3),
        today - dt.timedelta(days=9),  # previous 7-day period
    ]
    client = _client(monkeypatch, allowed=None, signups=signups)
    body = client.get(_pulse_path(), params={"days": 7}).json()
    metric = body["metrics"]["sign_ups"]

    assert metric["value"] == 3
    assert metric["previous"] == 1
    assert metric["change"] == 2
    assert len(metric["series"]) == 7
    assert metric["series"][-1] == 2  # today
    assert metric["series"][-4] == 1  # three days ago
    # A day without sign-ups is a real zero, never a missing value.
    assert metric["series"][0] == 0


def test_pulse_sign_ups_are_zero_for_an_empty_college_scope(monkeypatch):
    body = _client(monkeypatch, allowed=set()).get(_pulse_path(), params={"days": 30}).json()
    metric = body["metrics"]["sign_ups"]
    assert metric["value"] == 0 and metric["previous"] == 0
    assert metric["series"] == [0] * 30
