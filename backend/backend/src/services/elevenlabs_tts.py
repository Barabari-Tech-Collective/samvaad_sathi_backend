"""Service for generating text-to-speech audio using the ElevenLabs API."""

import io
import logging
import time

from src.config.manager import settings

logger = logging.getLogger(__name__)


async def generate_tts_audio(
    text: str,
    voice_id: str | None = None,
) -> tuple[bytes, str | None, int]:
    """
    Convert text to speech using the ElevenLabs API.

    Args:
        text:     The text to synthesise.
        voice_id: ElevenLabs voice ID to use. Falls back to ELEVENLABS_VOICE_ID setting.

    Returns:
        Tuple of (audio_bytes, error_message, latency_ms).
        ``audio_bytes`` is empty and ``error_message`` is set on failure.
    """
    api_key = settings.ELEVENLABS_API_KEY
    if not api_key:
        logger.warning("ELEVENLABS_API_KEY is not configured")
        return b"", "ElevenLabs API key not configured", 0

    try:
        from elevenlabs.client import AsyncElevenLabs  # lazy import
        from elevenlabs.types import VoiceSettings
    except ImportError:
        logger.error("elevenlabs package is not installed – run: pip install elevenlabs")
        return b"", "elevenlabs package not installed", 0

    resolved_voice_id = voice_id or settings.ELEVENLABS_VOICE_ID

    try:
        client = AsyncElevenLabs(api_key=api_key)

        start = time.time()
        # convert() returns an async generator of audio chunks. Using the sync
        # ElevenLabs client here would block the whole event loop for the
        # duration of the API call on every single TTS request.
        #
        # speed is pinned to 1.0 explicitly - without it, ElevenLabs falls
        # back to whatever default is saved against resolved_voice_id in the
        # account/dashboard, which produced noticeably slow (~0.5x) audio.
        audio_generator = client.text_to_speech.convert(
            text=text,
            voice_id=resolved_voice_id,
            model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
            voice_settings=VoiceSettings(speed=1.0, stability=0.5, similarity_boost=0.75),
        )
        chunks = [chunk async for chunk in audio_generator]
        audio_bytes = b"".join(chunks)
        latency_ms = int((time.time() - start) * 1000)

        logger.info(
            "ElevenLabs TTS: %d chars → %d bytes in %dms (voice=%s)",
            len(text),
            len(audio_bytes),
            latency_ms,
            resolved_voice_id,
        )
        return audio_bytes, None, latency_ms

    except Exception as exc:
        logger.error("ElevenLabs TTS error: %s", exc)
        return b"", str(exc), 0
