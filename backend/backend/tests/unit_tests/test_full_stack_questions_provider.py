import pytest

from src.services import full_stack_interview_questions as fsq_module


@pytest.fixture(autouse=True)
def _reset_client_cache():
    fsq_module._client = None
    yield
    fsq_module._client = None


def test_get_client_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr(fsq_module.settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(fsq_module.settings, "OPENAI_API_KEY", "")

    assert fsq_module._get_client() is None


def test_get_client_uses_openai_by_default(monkeypatch):
    monkeypatch.setattr(fsq_module.settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(fsq_module.settings, "OPENAI_API_KEY", "sk-openai-key")

    client = fsq_module._get_client()

    assert client is not None
    assert "deepseek" not in str(client.base_url)


def test_get_client_uses_deepseek_base_url_when_selected(monkeypatch):
    monkeypatch.setattr(fsq_module.settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(fsq_module.settings, "DEEPSEEK_API_KEY", "sk-deepseek-key")
    monkeypatch.setattr(fsq_module.settings, "DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(fsq_module.settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    client = fsq_module._get_client()

    assert client is not None
    assert str(client.base_url) == "https://api.deepseek.com"
    # Keeps its own deliberately short timeout/retry settings, distinct from
    # llm.py's shared client - this path has a static-question fallback and
    # should fail fast into it, not inherit the longer general-purpose timeout.
    assert client.timeout == 29.0
    assert client.max_retries == 1
