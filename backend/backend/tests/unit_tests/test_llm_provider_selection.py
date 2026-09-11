import pytest

from src.services import llm as llm_module


@pytest.fixture(autouse=True)
def _reset_client_cache():
    llm_module._client = None
    yield
    llm_module._client = None


def test_active_provider_defaults_to_openai(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "LLM_PROVIDER", "openai")
    assert llm_module.get_active_llm_provider() == "openai"


def test_active_provider_accepts_deepseek(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "LLM_PROVIDER", "deepseek")
    assert llm_module.get_active_llm_provider() == "deepseek"


def test_active_provider_falls_back_to_openai_on_unknown_value(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "LLM_PROVIDER", "some-typo")
    assert llm_module.get_active_llm_provider() == "openai"


def test_active_provider_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "LLM_PROVIDER", "DeepSeek")
    assert llm_module.get_active_llm_provider() == "deepseek"


def test_active_model_and_key_uses_openai_settings_by_default(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(llm_module.settings, "OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(llm_module.settings, "OPENAI_API_KEY", "sk-openai-key")

    model, api_key = llm_module.get_active_llm_model_and_key()

    assert model == "gpt-4o-mini"
    assert api_key == "sk-openai-key"


def test_active_model_and_key_uses_deepseek_settings_when_selected(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(llm_module.settings, "DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(llm_module.settings, "DEEPSEEK_API_KEY", "sk-deepseek-key")

    model, api_key = llm_module.get_active_llm_model_and_key()

    assert model == "deepseek-v4-flash"
    assert api_key == "sk-deepseek-key"


def test_get_client_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(llm_module.settings, "DEEPSEEK_API_KEY", "")

    client = llm_module.get_llm_client()

    assert client is None


def test_get_client_sets_deepseek_base_url(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(llm_module.settings, "DEEPSEEK_API_KEY", "sk-deepseek-key")
    monkeypatch.setattr(llm_module.settings, "DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(llm_module.settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    client = llm_module.get_llm_client()

    assert client is not None
    # The OpenAI SDK does NOT append /v1 to a custom base_url (verified
    # directly), so this must match DeepSeek's docs exactly as configured.
    assert str(client.base_url) == "https://api.deepseek.com"


def test_get_client_uses_default_openai_base_url(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(llm_module.settings, "OPENAI_API_KEY", "sk-openai-key")

    client = llm_module.get_llm_client()

    assert client is not None
    assert "deepseek" not in str(client.base_url)
