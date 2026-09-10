"""Service for generating pronunciation audio using OpenAI TTS."""

import io
import logging
from openai import AsyncOpenAI
from src.config.manager import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI | None:
    """Get or create OpenAI client."""
    global _client
    if _client is not None:
        return _client
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        return None
    _client = AsyncOpenAI(
        api_key=api_key,
        timeout=float(getattr(settings, "OPENAI_TIMEOUT_SECONDS", 60.0)),
        max_retries=3,
    )
    return _client


async def generate_pronunciation_audio(
    word: str,
    slow: bool = False,
) -> tuple[bytes, str | None, int]:
    """
    Generate pronunciation audio for a word using OpenAI TTS.
    
    Args:
        word: The word to pronounce
        slow: Whether to generate slow-paced audio
    
    Returns:
        Tuple of (audio_bytes, error_message, latency_ms)
    """
    client = _get_client()
    if not client:
        logger.warning("OpenAI client not available for TTS")
        return b"", "OpenAI client not configured", 0
    
    try:
        import time
        start_time = time.time()
        
        # Construct instructions for pronunciation
        if slow:
            instructions = "Speak very slowly and clearly, emphasizing each syllable. Use a measured, deliberate pace suitable for pronunciation practice."
        else:
            instructions = "Speak clearly at a normal conversational pace, perfect for pronunciation practice."
        
        # Use gpt-4o-mini-tts model with optimized settings for pronunciation
        response = await client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="coral",  # Clear and neutral voice
            input=word,
            instructions=instructions,
            response_format="opus",  # Opus for optimal compression and quality
        )
        
        latency_ms = int((time.time() - start_time) * 1000)
        
        # Get audio bytes
        audio_bytes = response.read()
        
        logger.info(f"Generated pronunciation audio for '{word}' (slow={slow}), size={len(audio_bytes)} bytes, latency={latency_ms}ms")
        
        return audio_bytes, None, latency_ms
        
    except Exception as e:
        logger.error(f"Error generating pronunciation audio for '{word}': {e}")
        return b"", str(e), 0
