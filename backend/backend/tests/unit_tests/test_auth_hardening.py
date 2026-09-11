"""Tests for brute-force protection and password policy.

Before these fixes: /login and /users had no rate limiting at all, the
rate limiter that did exist failed fully open when Redis was unreachable
(so an environment without Redis had zero protection), and the signup
password field was an unconstrained str that accepted a single character.
"""

import pydantic
import pytest

from src.api.dependencies import rate_limit as rl
from src.models.schemas.user import UserCreate, UserLogin


@pytest.fixture(autouse=True)
def _clear_local_counters():
    rl._LOCAL_HITS.clear()
    yield
    rl._LOCAL_HITS.clear()


# --- password policy -------------------------------------------------


def test_signup_rejects_short_password():
    with pytest.raises(pydantic.ValidationError):
        UserCreate(email="a@example.com", password="a", name="A")


def test_signup_accepts_compliant_password():
    user = UserCreate(email="a@example.com", password="correct-horse", name="A")
    assert user.password == "correct-horse"


def test_signup_rejects_absurdly_long_password():
    """Guards the deliberately-slow hasher against a CPU-exhaustion input."""
    with pytest.raises(pydantic.ValidationError):
        UserCreate(email="a@example.com", password="x" * 5000, name="A")


def test_login_still_accepts_legacy_short_passwords():
    """Pre-policy accounts must be able to sign in, not be locked out."""
    assert UserLogin(email="a@example.com", password="a").password == "a"


# --- in-process fallback limiter --------------------------------------


def test_local_limiter_allows_up_to_limit_then_blocks():
    key = "ratelimit:login:1.2.3.4"
    for _ in range(3):
        assert rl._local_rate_limited(key, limit=3, window_seconds=60) is False
    assert rl._local_rate_limited(key, limit=3, window_seconds=60) is True


def test_local_limiter_is_isolated_per_key():
    assert rl._local_rate_limited("a", limit=1, window_seconds=60) is False
    assert rl._local_rate_limited("a", limit=1, window_seconds=60) is True
    # A different client must not inherit the first client's budget.
    assert rl._local_rate_limited("b", limit=1, window_seconds=60) is False


def test_local_limiter_window_expires(monkeypatch):
    key = "ratelimit:login:1.2.3.4"
    clock = {"now": 1000.0}
    monkeypatch.setattr(rl.time, "monotonic", lambda: clock["now"])

    assert rl._local_rate_limited(key, limit=1, window_seconds=60) is False
    assert rl._local_rate_limited(key, limit=1, window_seconds=60) is True

    clock["now"] += 61  # slide past the window
    assert rl._local_rate_limited(key, limit=1, window_seconds=60) is False


@pytest.mark.asyncio
async def test_enforce_falls_back_to_local_when_redis_down(monkeypatch):
    """The key regression: Redis being unavailable must degrade protection,
    not remove it. This previously returned early and allowed everything."""

    class _DeadRedis:
        async def incr(self, key):
            raise ConnectionError("redis unreachable")

    monkeypatch.setattr(rl.async_redis, "client", _DeadRedis())

    # Within limit -> allowed.
    await rl._enforce("ratelimit:login:9.9.9.9", limit=2, window_seconds=60)
    await rl._enforce("ratelimit:login:9.9.9.9", limit=2, window_seconds=60)

    # Over limit -> 429 even though Redis is down.
    with pytest.raises(Exception) as exc:
        await rl._enforce("ratelimit:login:9.9.9.9", limit=2, window_seconds=60)
    assert getattr(exc.value, "status_code", None) == 429


@pytest.mark.asyncio
async def test_enforce_uses_redis_when_available(monkeypatch):
    calls = {"incr": 0, "expire": 0}

    class _LiveRedis:
        async def incr(self, key):
            calls["incr"] += 1
            return calls["incr"]

        async def expire(self, key, seconds):
            calls["expire"] += 1

    monkeypatch.setattr(rl.async_redis, "client", _LiveRedis())

    await rl._enforce("ratelimit:login:1.1.1.1", limit=1, window_seconds=60)
    assert calls["incr"] == 1
    assert calls["expire"] == 1  # TTL set on first hit only

    with pytest.raises(Exception) as exc:
        await rl._enforce("ratelimit:login:1.1.1.1", limit=1, window_seconds=60)
    assert getattr(exc.value, "status_code", None) == 429
    # Local fallback must not have been consulted while Redis was healthy.
    assert not rl._LOCAL_HITS
