import fastapi
import pytest

from src.api.dependencies import rate_limit as rate_limit_module
from src.api.dependencies.rate_limit import rate_limiter


class _FakeUser:
    id = 42


class _FakeRedisClient:
    def __init__(self):
        self.counts: dict[str, int] = {}
        self.expiries: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.expiries.append((key, seconds))


class _RaisingRedisClient:
    async def incr(self, key: str) -> int:
        raise ConnectionError("Redis unreachable")


@pytest.mark.asyncio
async def test_rate_limiter_allows_under_limit(monkeypatch):
    fake_client = _FakeRedisClient()
    monkeypatch.setattr(rate_limit_module.async_redis, "client", fake_client)

    check = rate_limiter(key_prefix="tts", limit=3, window_seconds=60)
    for _ in range(3):
        await check(current_user=_FakeUser())  # should not raise


@pytest.mark.asyncio
async def test_rate_limiter_blocks_over_limit(monkeypatch):
    fake_client = _FakeRedisClient()
    monkeypatch.setattr(rate_limit_module.async_redis, "client", fake_client)

    check = rate_limiter(key_prefix="tts", limit=2, window_seconds=60)
    await check(current_user=_FakeUser())
    await check(current_user=_FakeUser())

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await check(current_user=_FakeUser())
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_rate_limiter_fails_open_on_redis_error(monkeypatch):
    monkeypatch.setattr(rate_limit_module.async_redis, "client", _RaisingRedisClient())

    check = rate_limiter(key_prefix="tts", limit=1, window_seconds=60)
    # Should not raise even though the (fake) limit of 1 would otherwise be
    # exceeded by repeated calls - Redis being down must never block requests.
    await check(current_user=_FakeUser())
    await check(current_user=_FakeUser())


@pytest.mark.asyncio
async def test_rate_limiter_sets_expiry_only_on_first_increment(monkeypatch):
    fake_client = _FakeRedisClient()
    monkeypatch.setattr(rate_limit_module.async_redis, "client", fake_client)

    check = rate_limiter(key_prefix="tts", limit=5, window_seconds=60)
    await check(current_user=_FakeUser())
    await check(current_user=_FakeUser())

    assert len(fake_client.expiries) == 1
    assert fake_client.expiries[0] == ("ratelimit:tts:42", 60)
