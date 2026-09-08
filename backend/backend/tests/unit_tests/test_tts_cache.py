import pytest

from src.api.routes import tts as tts_module


class _FakeRedisClient:
    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ex: int) -> None:
        self.store[key] = value
        self.ttls[key] = ex


class _RaisingRedisClient:
    async def get(self, key: str):
        raise ConnectionError("Redis unreachable")

    async def set(self, key: str, value: bytes, ex: int):
        raise ConnectionError("Redis unreachable")


def test_tts_cache_key_is_stable_and_distinguishes_inputs():
    key_a = tts_module._tts_cache_key("hello", "voice-1")
    key_a_again = tts_module._tts_cache_key("hello", "voice-1")
    key_diff_text = tts_module._tts_cache_key("goodbye", "voice-1")
    key_diff_voice = tts_module._tts_cache_key("hello", "voice-2")

    assert key_a == key_a_again
    assert key_a != key_diff_text
    assert key_a != key_diff_voice


@pytest.mark.asyncio
async def test_cache_round_trip(monkeypatch):
    fake_client = _FakeRedisClient()
    monkeypatch.setattr(tts_module.async_redis, "client", fake_client)

    key = tts_module._tts_cache_key("hello", None)
    assert await tts_module._get_cached_audio(key) is None

    await tts_module._set_cached_audio(key, b"mp3-bytes")
    assert await tts_module._get_cached_audio(key) == b"mp3-bytes"


@pytest.mark.asyncio
async def test_cache_read_and_write_fail_open_on_redis_error(monkeypatch):
    monkeypatch.setattr(tts_module.async_redis, "client", _RaisingRedisClient())

    key = tts_module._tts_cache_key("hello", None)
    # Neither call should raise - a broken cache must degrade to "always miss".
    assert await tts_module._get_cached_audio(key) is None
    await tts_module._set_cached_audio(key, b"mp3-bytes")
