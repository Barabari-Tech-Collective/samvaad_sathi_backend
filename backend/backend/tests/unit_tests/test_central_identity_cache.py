"""The verified-identity cache must only ever reuse a success, and must expire."""
import pytest

from src.services import central_identity_access as cia

CLAIMS = {"sub": "a@example.com", "userUniqueId": "u-1"}


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    cia._identity_cache.clear()
    for name, value in {
        "CENTRAL_IDENTITY_CHECK_ENABLED": True,
        "AUTH_SERVICE_BASE_URL": "https://auth.test",
        "PRODUCT_IDENTITY_API_KEY": "k",
        "SAMPARK_PRODUCT_UNIQUE_ID": "p-1",
        "CENTRAL_IDENTITY_CACHE_SECONDS": 10,
    }.items():
        monkeypatch.setattr(cia.settings, name, value, raising=False)
    monkeypatch.setattr("src.securities.authorizations.sso_jwt._allowed_roles", lambda: {"STUDENT"})
    yield
    cia._identity_cache.clear()


def test_cache_returns_copies_and_expires(monkeypatch):
    cia._cache_put("k1", {"role": "STUDENT"})
    first = cia._cache_get("k1")
    first["role"] = "ADMIN"  # mutating a returned value must not poison the cache
    assert cia._cache_get("k1")["role"] == "STUDENT"
    now = cia.time.monotonic()
    monkeypatch.setattr(cia.time, "monotonic", lambda: now + 11)
    assert cia._cache_get("k1") is None


def test_cache_disabled_when_ttl_is_zero(monkeypatch):
    monkeypatch.setattr(cia.settings, "CENTRAL_IDENTITY_CACHE_SECONDS", 0)
    cia._cache_put("k1", {"role": "STUDENT"})
    assert cia._cache_get("k1") is None


def test_cache_is_bounded():
    for i in range(cia._CACHE_MAX_ENTRIES + 5):
        cia._cache_put(f"k{i}", {"role": "STUDENT"})
    assert len(cia._identity_cache) <= cia._CACHE_MAX_ENTRIES


def test_key_depends_on_token():
    assert cia._cache_key("u-1", "t1") != cia._cache_key("u-1", "t2")
