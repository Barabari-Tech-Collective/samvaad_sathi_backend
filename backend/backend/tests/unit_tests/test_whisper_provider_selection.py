import pytest

from src.services import whisper as whisper_module


@pytest.fixture(autouse=True)
def _reset_client_cache():
    whisper_module._client = None
    yield
    whisper_module._client = None


def test_active_stt_provider_defaults_to_openai(monkeypatch):
    monkeypatch.setattr(whisper_module.settings, "STT_PROVIDER", "openai")
    assert whisper_module._active_stt_provider() == "openai"


def test_active_stt_provider_accepts_groq(monkeypatch):
    monkeypatch.setattr(whisper_module.settings, "STT_PROVIDER", "groq")
    assert whisper_module._active_stt_provider() == "groq"


def test_active_stt_provider_falls_back_to_openai_on_unknown_value(monkeypatch):
    monkeypatch.setattr(whisper_module.settings, "STT_PROVIDER", "some-typo")
    assert whisper_module._active_stt_provider() == "openai"


def test_active_stt_model_and_key_uses_whisper_1_for_openai(monkeypatch):
    monkeypatch.setattr(whisper_module.settings, "STT_PROVIDER", "openai")
    monkeypatch.setattr(whisper_module.settings, "OPENAI_API_KEY", "sk-openai-key")

    model, api_key = whisper_module._active_stt_model_and_key()

    assert model == "whisper-1"
    assert api_key == "sk-openai-key"


def test_active_stt_model_and_key_uses_groq_settings_when_selected(monkeypatch):
    monkeypatch.setattr(whisper_module.settings, "STT_PROVIDER", "groq")
    monkeypatch.setattr(whisper_module.settings, "GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
    monkeypatch.setattr(whisper_module.settings, "GROQ_API_KEY", "sk-groq-key")

    model, api_key = whisper_module._active_stt_model_and_key()

    assert model == "whisper-large-v3-turbo"
    assert api_key == "sk-groq-key"


def test_get_client_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr(whisper_module.settings, "STT_PROVIDER", "groq")
    monkeypatch.setattr(whisper_module.settings, "GROQ_API_KEY", "")

    assert whisper_module._get_client() is None


def test_get_client_sets_groq_base_url(monkeypatch):
    monkeypatch.setattr(whisper_module.settings, "STT_PROVIDER", "groq")
    monkeypatch.setattr(whisper_module.settings, "GROQ_API_KEY", "sk-groq-key")
    monkeypatch.setattr(whisper_module.settings, "GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
    monkeypatch.setattr(whisper_module.settings, "GROQ_BASE_URL", "https://api.groq.com/openai/v1")

    client = whisper_module._get_client()

    assert client is not None
    assert str(client.base_url) == "https://api.groq.com/openai/v1/"


def test_get_client_uses_default_openai_base_url(monkeypatch):
    monkeypatch.setattr(whisper_module.settings, "STT_PROVIDER", "openai")
    monkeypatch.setattr(whisper_module.settings, "OPENAI_API_KEY", "sk-openai-key")

    client = whisper_module._get_client()

    assert client is not None
    assert "groq" not in str(client.base_url)
