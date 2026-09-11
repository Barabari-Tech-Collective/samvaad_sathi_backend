import io
import wave

import fastapi
import pytest

from src.services.audio_processor import MIN_DURATION_SECONDS, validate_audio_file


class _FakeUploadFile:
    """Minimal async duck-type stand-in for fastapi.UploadFile in tests."""

    def __init__(self, data: bytes, filename: str, content_type: str):
        self._buf = io.BytesIO(data)
        self.filename = filename
        self.content_type = content_type

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


def _make_wav_bytes(num_frames: int, sample_rate: int = 16000) -> bytes:
    """Build a minimal, valid mono 16-bit WAV file with the given frame count."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * num_frames)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_validate_audio_file_rejects_near_silent_short_clip():
    """Regression guard for the 'structure-practice advances with no real
    answer' bug: a ~0.25s clip must be rejected before it ever reaches
    Whisper/word-count validation."""
    wav_bytes = _make_wav_bytes(num_frames=4000, sample_rate=16000)  # ~0.25s
    fake_file = _FakeUploadFile(wav_bytes, "answer.wav", "audio/wav")

    with pytest.raises(fastapi.HTTPException) as exc_info:
        await validate_audio_file(fake_file)

    assert exc_info.value.status_code == 422
    assert "short" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_validate_audio_file_accepts_a_real_length_clip():
    """A clip well above MIN_DURATION_SECONDS should pass validation and
    report its estimated duration in the returned metadata."""
    wav_bytes = _make_wav_bytes(num_frames=160000, sample_rate=16000)  # ~10s of audio
    fake_file = _FakeUploadFile(wav_bytes, "answer.wav", "audio/wav")

    audio_bytes, metadata = await validate_audio_file(fake_file)

    assert audio_bytes == wav_bytes
    assert metadata["estimated_duration_seconds"] is not None
    assert metadata["estimated_duration_seconds"] >= MIN_DURATION_SECONDS
