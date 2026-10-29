import io
import wave

import fastapi
import pytest

from src.services.audio_processor import MIN_DURATION_SECONDS, validate_audio_file, get_audio_duration_estimate, MAX_DURATION_SECONDS


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


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", ["audio/wav", "audio/x-wav", "audio/wave"])
async def test_mono_recording_at_minimum_is_not_halved(mime):
    data = _make_wav_bytes(MIN_DURATION_SECONDS * 16000)
    _, metadata = await validate_audio_file(_FakeUploadFile(data, "answer.wav", mime))
    assert metadata["estimated_duration_seconds"] == MIN_DURATION_SECONDS


@pytest.mark.parametrize("channels,width", [(1,1),(1,3),(2,2),(2,3)])
def test_wav_duration_honors_channels_sample_width_and_riff_chunks(channels,width):
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(channels)
        recording.setsampwidth(width)
        recording.setframerate(8000)
        recording.writeframes(bytes(8000 * 4 * channels * width))
    raw = output.getvalue()
    # Unknown RIFF chunks can occur between WAVE and fmt/data.
    data = raw[:4] + (int.from_bytes(raw[4:8], "little")+12).to_bytes(4,"little") + raw[8:12] + b"JUNK\x04\x00\x00\x00test" + raw[12:]
    assert get_audio_duration_estimate(data,"audio/wav") == 4


@pytest.mark.asyncio
async def test_overlong_wav_is_rejected_without_clamping_to_the_limit():
    data = _make_wav_bytes((MAX_DURATION_SECONDS+1)*16000)
    with pytest.raises(fastapi.HTTPException) as caught:
        await validate_audio_file(_FakeUploadFile(data,"answer.wav","audio/wav"))
    assert caught.value.status_code == 422
    assert "maximum" in caught.value.detail


@pytest.mark.asyncio
async def test_empty_wav_cannot_bypass_the_minimum_with_zero_duration():
    with pytest.raises(fastapi.HTTPException) as caught:
        await validate_audio_file(_FakeUploadFile(_make_wav_bytes(0),"answer.wav","audio/wav"))
    assert caught.value.status_code == 422
