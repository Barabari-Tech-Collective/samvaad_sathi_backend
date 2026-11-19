"""Dashboard aggregates are shared for 60s only between people with the same scope."""

from unittest.mock import patch

import pytest

from src.api.routes import analytics_v2


class _Redis:
    def __init__(self):
        self.store: dict[str, bytes] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        assert ex == 60
        self.store[key] = value.encode() if isinstance(value, str) else value


@pytest.mark.asyncio
async def test_same_scope_shares_and_a_different_scope_does_not():
    calls: list[str] = []

    @analytics_v2._aggregate_cache
    async def handler(college=None, current_user=None, session=None):
        calls.append(current_user)
        return {"value": len(calls)}

    fake = _Redis()
    scopes = {"a": {"College A"}, "b": {"College A"}, "c": {"College C"}}

    async def allowed(user, _session):
        return scopes[user]

    with patch.object(analytics_v2.async_redis, "client", fake), patch.object(
        analytics_v2, "get_user_allowed_colleges", allowed
    ):
        first = await handler(current_user="a", session=None)
        shared = await handler(current_user="b", session=None)
        other = await handler(current_user="c", session=None)

    assert first == shared == {"value": 1}
    assert other == {"value": 2}
    assert calls == ["a", "c"]


@pytest.mark.asyncio
async def test_redis_down_still_answers():
    class _Down:
        async def get(self, _key):
            raise ConnectionError("down")

        async def set(self, *_a, **_k):
            raise ConnectionError("down")

    @analytics_v2._aggregate_cache
    async def handler(current_user=None, session=None):
        return {"ok": True}

    async def allowed(_user, _session):
        return None

    with patch.object(analytics_v2.async_redis, "client", _Down()), patch.object(
        analytics_v2, "get_user_allowed_colleges", allowed
    ):
        assert await handler(current_user="x", session=None) == {"ok": True}
