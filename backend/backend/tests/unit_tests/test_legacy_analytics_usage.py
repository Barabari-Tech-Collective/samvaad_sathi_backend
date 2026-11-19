"""ADR-3: legacy analytics calls are counted per route and marked deprecated."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import fastapi

from src.api.routes import analytics_v2
from src.services import legacy_analytics_usage as usage


def test_a_legacy_call_is_counted_by_route_and_marked_deprecated():
    client = SimpleNamespace(hincrby=AsyncMock(), expire=AsyncMock())
    request = SimpleNamespace(scope={"route": SimpleNamespace(path="/v2/analytics/dashboard/overview")},
                              url=SimpleNamespace(path="/api/v2/analytics/dashboard/overview"))
    response = fastapi.Response()
    with patch.object(usage, "async_redis", SimpleNamespace(client=client)):
        asyncio.run(usage.count_legacy_call(request, response))
    assert response.headers["Deprecation"] == "true"
    key, field, amount = client.hincrby.await_args.args
    assert key.startswith("legacy-analytics:") and field == "/v2/analytics/dashboard/overview" and amount == 1


def test_counting_never_breaks_the_report_when_redis_is_down():
    client = SimpleNamespace(hincrby=AsyncMock(side_effect=ConnectionError("down")), expire=AsyncMock())
    request = SimpleNamespace(scope={}, url=SimpleNamespace(path="/api/v2/analytics/x"))
    with patch.object(usage, "async_redis", SimpleNamespace(client=client)):
        asyncio.run(usage.count_legacy_call(request, fastapi.Response()))


def test_every_legacy_route_is_deprecated_and_counted():
    assert analytics_v2.router.deprecated is True
    names = [getattr(d.dependency, "__name__", "") for d in analytics_v2.router.dependencies]
    assert "count_legacy_call" in names
