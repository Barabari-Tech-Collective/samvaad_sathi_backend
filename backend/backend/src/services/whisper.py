from fileinput import filename
import os
import time
import logging
import tempfile
from typing import Tuple

import openai
from openai import AsyncOpenAI
from src.config.manager import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None

def _get_client() -> AsyncOpenAI | None:
    global _client
    if _client is not None:
        return _client
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        return None
    _client = AsyncOpenAI(
        api_key=api_key,
        timeout=60.0,
        max_retries=2,
    )
    return _client


async def transcribe_audio_with_whisper(
    audio_bytes: bytes,
    filename: str,
    language: str = "en"
) -> Tuple[dict | None, str | None, int | None, str]:
    """
    Transcribe audio using OpenAI Whisper API.
    
    Args:
        audio_bytes: Raw audio file bytes
        filename: Original filename for the API call
        language: Language code for transcription (ISO 639-1)
        
    Returns:
        Tuple of (transcription_dict, error_message, latency_ms, model_name)
    """
    model_name = "whisper-1"
    api_key = settings.OPENAI_API_KEY
    
    if not api_key:
        return None, "OpenAI API key not configured", None, model_name
    
    if not audio_bytes:
        return None, "Empty audio file", None, model_name

    start_time = time.perf_counter()

    logger.info(
    "Whisper transcription started | File=%s | Size=%d bytes | Language=%s | Model=%s",
    filename,
    len(audio_bytes),
    language,
    model_name,
   )
    
    try:
        client = _get_client()
        logger.debug("OpenAI Whisper client initialized")
        if client is None:
            return None, "OpenAI API key not configured", None, model_name

        # Create temporary file for Whisper API (it requires a file, not bytes)
        with tempfile.NamedTemporaryFile(suffix=_get_file_extension(filename), delete=False) as temp_file:
            temp_file.write(audio_bytes)
            temp_file_path = temp_file.name

        try:
            # Call Whisper API with word-level timestamps
            with open(temp_file_path, "rb") as audio_file:
                api_start = time.perf_counter()
                logger.info(
                "OpenAI Whisper API request started | File=%s",
                filename,
                )
                transcript = await client.audio.transcriptions.create(
                    model=model_name,
                    file=audio_file,
                    language=language,
                    response_format="verbose_json",  # Required for word-level timestamps
                    timestamp_granularities=["word"]  # Enable word-level timestamps
                )
                logger.info(
                "OpenAI Whisper API completed | File=%s | Duration=%.2fs",
                filename,
                time.perf_counter() - api_start,
                )

            end_time = time.perf_counter()
            latency_ms = int((end_time - start_time) * 1000)
            # Convert response to dictionary format
            transcription_dict = {
                "task": getattr(transcript, "task", "transcribe"),
                "language": getattr(transcript, "language", language),
                "duration": getattr(transcript, "duration", None),
                "text": getattr(transcript, "text", ""),
                "words": []
            }
            # Extract word-level timestamps if available
            if hasattr(transcript, "words") and getattr(transcript, "words"):
                transcription_dict["words"] = [
                    {
                        "word": word.word,
                        "start": word.start,
                        "end": word.end
                    }
                    for word in transcript.words
                ]


            logger.info(
              "Whisper transcription successful | File=%s | TotalLatency=%d ms | TranscriptLength=%d chars | Words=%d",
              filename,
              latency_ms,
              len(transcription_dict["text"]),
              len(transcription_dict["words"]),
            )
            return transcription_dict, None, latency_ms, model_name

        finally:
            # Ensure temp file is cleaned up
            try:
                os.unlink(temp_file_path)
                logger.debug(
                "Temporary audio file cleaned up | File=%s",
                filename,
                )
            except Exception:
                pass
    
    except openai.AuthenticationError:
        end_time = time.perf_counter()
        latency_ms = int((end_time - start_time) * 1000)
        logger.error(
        "Whisper authentication failed | File=%s",
        filename,
        )
        return None, "Invalid OpenAI API key", latency_ms, model_name
    
    except openai.RateLimitError:
        end_time = time.perf_counter()
        latency_ms = int((end_time - start_time) * 1000)
        logger.error(
        "Whisper rate limit exceeded | File=%s",
        filename,
        )
        logger.warning(
        "OpenAI Whisper rate limit exceeded | File=%s | Latency=%d ms",
        filename,
        latency_ms,
        )
        return None, "OpenAI API rate limit exceeded", latency_ms, model_name
    
    except openai.BadRequestError as e:
        end_time = time.perf_counter()
        latency_ms = int((end_time - start_time) * 1000)
        logger.error(
        "Whisper API error | File=%s | Error=%s",
        filename,
        str(e),
        )
        return None, f"Whisper API error: {str(e)}", latency_ms, model_name
    
    except Exception as e:
        end_time = time.perf_counter()
        latency_ms = int((end_time - start_time) * 1000)
        logger.exception(
        "Unexpected Whisper transcription failure | File=%s",
        filename,
        )
        return None, f"Transcription failed: {str(e)}", latency_ms, model_name


def _get_file_extension(filename: str) -> str:
    """Get file extension from filename, defaulting to .mp3"""
    if not filename:
        return ".mp3"
    
    ext = os.path.splitext(filename)[1].lower()
    if ext in [".mp3", ".wav", ".m4a", ".flac"]:
        return ext
    return ".mp3"


def validate_transcription_language(language: str) -> str:
    """
    Validate and normalize language code for Whisper API.
    
    Args:
        language: Language code to validate
        
    Returns:
        Validated language code or default "en"
    """
    # Common language codes supported by Whisper
    supported_languages = {
        "en", "es", "fr", "de", "it", "pt", "ru", "ja", "ko", "zh", "ar", "hi", "nl",
        "sv", "no", "da", "fi", "pl", "tr", "cs", "hu", "ro", "bg", "hr", "sk", "sl",
        "et", "lv", "lt", "mt", "ga", "cy", "eu", "ca", "gl", "is", "mk", "sq", "bs",
        "sr", "me", "hr", "bg", "uk", "be", "kk", "ky", "tg", "uz", "mn", "hy", "az",
        "ka", "he", "ur", "fa", "ps", "sd", "gu", "pa", "bn", "ta", "te", "kn", "ml",
        "si", "th", "lo", "my", "km", "vi", "id", "ms", "tl", "sw", "am", "so", "zu",
        "af", "yo", "ig", "ha", "mg", "mi", "oc", "br", "fo", "ht", "la", "ln", "ne",
        "sa", "sn", "tk", "tt", "wo", "xh"
    }
    
    if language and language.lower() in supported_languages:
        return language.lower()
    
    return "en"  # Default to English


def extract_word_count(transcription: dict | None) -> int | None:
    """
    Extract word count from transcription data.
    
    Args:
        transcription: Whisper transcription dictionary
        
    Returns:
        Number of words, or None if cannot determine
    """
    if not transcription:
        return None
    
    # Try to count from words array first (most accurate)
    if "words" in transcription and isinstance(transcription["words"], list):
        return len(transcription["words"])
    
    # Fallback to counting words in text
    if "text" in transcription and isinstance(transcription["text"], str):
        words = transcription["text"].split()
        return len(words)
    
    return None


def strip_word_level_data(transcription: dict | None) -> dict | None:
    """
    Return a copy of transcription without verbose word-level arrays for client responses.
    Keeps high-level fields like task, language, duration, and text. DB persistence remains unchanged.
    """
    if not transcription:
        return transcription
    if not isinstance(transcription, dict):
        return None
    sanitized = dict(transcription)
    # Remove word-level details if present
    if "words" in sanitized:
        sanitized.pop("words", None)
    # In case future providers add other verbose arrays
    if "segments" in sanitized and isinstance(sanitized["segments"], list):
        # Keep segments metadata minimal if needed in the future; for now, drop entirely
        sanitized.pop("segments", None)
    return sanitized