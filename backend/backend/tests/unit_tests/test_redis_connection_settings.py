"""Managed Redis needs a username and TLS; both clients must pass them through."""

import redis.asyncio as redis

from src.config.manager import settings
from src.repository.redis_client import AsyncRedis
from src.worker.settings import build_redis_settings


def _managed(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(settings, "REDIS_HOST", "example.upstash.io")
    monkeypatch.setattr(settings, "REDIS_USERNAME", "default")
    monkeypatch.setattr(settings, "REDIS_PASSWORD", "test-only")
    monkeypatch.setattr(settings, "REDIS_SSL_ENABLED", True)


def test_web_client_uses_username_and_tls(monkeypatch):
    _managed(monkeypatch)
    pool = AsyncRedis().client.connection_pool
    assert pool.connection_class is redis.SSLConnection
    assert pool.connection_kwargs["username"] == "default"
    assert pool.connection_kwargs["host"] == "example.upstash.io"


def test_worker_uses_username_and_tls(monkeypatch):
    _managed(monkeypatch)
    result = build_redis_settings()
    assert result.ssl is True
    assert result.username == "default"


def test_local_redis_stays_plain(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(settings, "REDIS_HOST", "localhost")
    monkeypatch.setattr(settings, "REDIS_USERNAME", "")
    monkeypatch.setattr(settings, "REDIS_SSL_ENABLED", False)
    pool = AsyncRedis().client.connection_pool
    assert pool.connection_class is not redis.SSLConnection
    assert pool.connection_kwargs.get("username") is None
