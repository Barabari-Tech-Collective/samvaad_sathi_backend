import pytest

from src.services import elevenlabs_tts


class _FakeAsyncAudioIterator:
    """Duck-types AsyncElevenLabs' text_to_speech.convert() return value."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


class _FakeTextToSpeech:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    def convert(self, **kwargs):
        return _FakeAsyncAudioIterator(self._chunks)


class _FakeAsyncElevenLabs:
    def __init__(self, api_key: str, chunks: list[bytes] = (b"abc", b"def")):
        self.text_to_speech = _FakeTextToSpeech(list(chunks))


@pytest.mark.asyncio
async def test_generate_tts_audio_joins_async_chunks(monkeypatch):
    monkeypatch.setattr(elevenlabs_tts.settings, "ELEVENLABS_API_KEY", "fake-key")
    monkeypatch.setattr(elevenlabs_tts.settings, "ELEVENLABS_VOICE_ID", "fake-voice")

    import elevenlabs.client
    monkeypatch.setattr(
        elevenlabs.client,
        "AsyncElevenLabs",
        lambda api_key: _FakeAsyncElevenLabs(api_key, chunks=[b"hello ", b"world"]),
    )

    audio_bytes, error, latency_ms = await elevenlabs_tts.generate_tts_audio(text="hi")

    assert error is None
    assert audio_bytes == b"hello world"
    assert latency_ms >= 0


@pytest.mark.asyncio
async def test_generate_tts_audio_missing_api_key():
    monkeypatch_value = elevenlabs_tts.settings.ELEVENLABS_API_KEY
    elevenlabs_tts.settings.ELEVENLABS_API_KEY = ""
    try:
        audio_bytes, error, latency_ms = await elevenlabs_tts.generate_tts_audio(text="hi")
        assert audio_bytes == b""
        assert error == "ElevenLabs API key not configured"
    finally:
        elevenlabs_tts.settings.ELEVENLABS_API_KEY = monkeypatch_value
