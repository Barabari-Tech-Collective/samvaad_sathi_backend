"""Dashboard aggregates are shared only between people with the same scope, and a
stale answer is served at once while one background task refreshes it."""

from unittest.mock import patch

import asyncio
import json
import time

import pytest

from src.api.routes import analytics_v2


class _Redis:
    def __init__(self):
        self.store: dict[str, bytes] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        assert ex == analytics_v2._AGGREGATE_KEEP_SECONDS
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


@pytest.mark.asyncio
async def test_a_role_guard_runs_even_when_the_answer_is_cached():
    class Denied(Exception):
        pass

    def guard(user):
        if user != "ops":
            raise Denied

    @analytics_v2._aggregate_cache(guard=guard)
    async def handler(current_user=None, session=None):
        return {"rows": 1}

    async def allowed(_user, _session):
        return None

    with patch.object(analytics_v2.async_redis, "client", _Redis()), patch.object(
        analytics_v2, "get_user_allowed_colleges", allowed
    ):
        assert await handler(current_user="ops", session=None) == {"rows": 1}
        with pytest.raises(Denied):
            await handler(current_user="viewer", session=None)


@pytest.mark.asyncio
async def test_a_stale_answer_is_served_at_once_and_one_refresh_goes_to_the_worker():
    calls: list[str] = []
    queued: list[dict] = []

    @analytics_v2._aggregate_cache
    async def handler(current_user=None, session=None):
        calls.append("computed")
        return {"value": "fresh"}

    fake = _Redis()

    async def allowed(_user, _session):
        return None

    async def enqueue(name, **kwargs):
        queued.append({"name": name, **kwargs})
        return "job"

    class _User:
        id = 7

    user = _User()
    raw = json.dumps([handler.__name__, {}, "*"], default=str, sort_keys=True)
    import hashlib

    key = "analytics:v2:" + hashlib.sha256(raw.encode()).hexdigest()
    fake.store[key] = json.dumps({"t": time.time() - 120, "v": {"value": "stale"}}).encode()

    with patch.object(analytics_v2.async_redis, "client", fake), patch.object(
        analytics_v2, "get_user_allowed_colleges", allowed
    ), patch("src.worker.queue.enqueue_job", enqueue):
        # Ten people open the page: all get the stale answer without waiting and
        # nobody computes on the web server.
        answers = await asyncio.gather(*(handler(current_user=user, session=None) for _ in range(10)))
        for _ in range(5):
            await asyncio.sleep(0)

    assert answers == [{"value": "stale"}] * 10
    assert calls == []
    assert queued and all(q["name"] == "refresh_dashboard_aggregate_task" for q in queued)
    assert {q["_job_id"] for q in queued} == {f"dash:{key}"}  # duplicates collapse in arq
