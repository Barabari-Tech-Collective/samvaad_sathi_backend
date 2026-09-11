import asyncio
import audioop
import io
import os
import hashlib
import statistics
import tempfile
import wave
from pathlib import Path
from typing import Tuple

import fastapi
from fastapi import UploadFile


SUPPORTED_AUDIO_TYPES = {
    "audio/mpeg",      # .mp3
    "audio/wav",       # .wav  
    "audio/x-wav",     # .wav (alternative MIME type)
    "audio/mp4",       # .m4a
    "audio/flac",      # .flac
    "audio/x-flac",    # .flac (alternative MIME type)
}

SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac"}

# Extension to MIME type mapping for fallback detection
EXTENSION_TO_MIME = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4", 
    ".flac": "audio/flac"
}

MAX_AUDIO_SIZE_BYTES = 25 * 1024 * 1024  # 25MB (Whisper API limit)
MAX_DURATION_SECONDS = 600  # 10 minutes
MIN_DURATION_SECONDS = 3  # below this, a recording is very unlikely to contain a real answer


async def validate_audio_file(file: UploadFile) -> Tuple[bytes, dict]:
    """
    Validate uploaded audio file and return file bytes with metadata.
    
    Args:
        file: FastAPI UploadFile object
        
    Returns:
        Tuple of (file_bytes, metadata_dict)
        
    Raises:
        HTTPException: If validation fails
    """
    # Check content type - infer from extension if missing or non-standard
    content_type = file.content_type or ""
    filename = file.filename or ""
    file_ext = Path(filename).suffix.lower()
    
    # Infer MIME type from extension if header is missing/non-standard
    if content_type not in SUPPORTED_AUDIO_TYPES and file_ext:
        ext_to_mime = {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav", 
            ".m4a": "audio/mp4",
            ".flac": "audio/flac"
        }
        content_type = ext_to_mime.get(file_ext, content_type)
    
    if content_type not in SUPPORTED_AUDIO_TYPES:
        if file_ext not in SUPPORTED_EXTENSIONS:
            supported_list = sorted(SUPPORTED_AUDIO_TYPES)  # Consistent ordering
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported audio format: {content_type}. Supported formats: {', '.join(supported_list)}"
            )

    # Read file with size validation
    total_size = 0
    buffer = bytearray()
    chunk_size = 64 * 1024  # 64KB chunks
    
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > MAX_AUDIO_SIZE_BYTES:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Audio file exceeds {MAX_AUDIO_SIZE_BYTES // (1024*1024)}MB limit"
            )
        buffer.extend(chunk)

    audio_bytes = bytes(buffer)
    
    # Basic audio file validation (check for valid audio headers)
    if not _is_valid_audio_file(audio_bytes, content_type):
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid or corrupted audio file"
        )

    # Estimate duration and validate against maximum
    estimated_duration = get_audio_duration_estimate(audio_bytes, content_type)
    if estimated_duration and estimated_duration > MAX_DURATION_SECONDS:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Audio duration ({estimated_duration:.1f}s) exceeds maximum allowed ({MAX_DURATION_SECONDS}s)"
        )
    if estimated_duration and estimated_duration < MIN_DURATION_SECONDS:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Audio duration ({estimated_duration:.1f}s) is too short to contain a real answer (minimum {MIN_DURATION_SECONDS}s)"
        )

    metadata = {
        "filename": file.filename or "unknown.audio",
        "content_type": content_type,
        "size": total_size,
        "estimated_duration_seconds": estimated_duration,
    }

    return audio_bytes, metadata


def _is_valid_audio_file(audio_bytes: bytes, content_type: str) -> bool:
    """
    Basic audio file validation by checking file headers.
    
    Args:
        audio_bytes: Raw audio file bytes
        content_type: MIME type of the file
        
    Returns:
        True if file appears to be valid audio, False otherwise
    """
    if len(audio_bytes) < 12:  # Need at least 12 bytes for WAV validation
        return False
    
    # Check common audio file signatures
    header = audio_bytes[:4]
    
    if content_type == "audio/mpeg":
        # MP3 files: ID3 tag or MPEG audio frame (including MPEG 2/2.5 variants)
        return (header.startswith(b"ID3") or 
                header.startswith(b"\xff\xfb") or  # MPEG-1 Layer 3
                header.startswith(b"\xff\xfa") or  # MPEG-1 Layer 3 (alt)
                header.startswith(b"\xff\xf3") or  # MPEG-2 Layer 3
                header.startswith(b"\xff\xf2") or  # MPEG-2 Layer 3 (alt)
                header.startswith(b"\xff\xe3") or  # MPEG-2.5 Layer 3
                header.startswith(b"\xff\xe2"))    # MPEG-2.5 Layer 3 (alt)
    
    elif content_type in ("audio/wav", "audio/x-wav", "audio/wave"):
        # WAV files must have both "RIFF" header and "WAVE" format
        return header.startswith(b"RIFF") and len(audio_bytes) >= 12 and audio_bytes[8:12] == b"WAVE"
    
    elif content_type == "audio/mp4":
        # M4A files have various signatures
        return header.startswith(b"ftyp") or (len(audio_bytes) >= 8 and audio_bytes[4:8].startswith(b"ftyp"))
    
    elif content_type in ("audio/flac", "audio/x-flac"):
        # FLAC files start with "fLaC"
        return header.startswith(b"fLaC")
    
    # If we can't validate the specific format, assume it's valid
    return True


async def save_audio_file(audio_bytes: bytes, filename: str, user_id: int, question_attempt_id: int) -> Tuple[str, str]:
    """
    Create a temporary audio file for processing and return both path and cleanup reference.
    
    Args:
        audio_bytes: Raw audio file bytes
        filename: Original filename
        user_id: ID of the user uploading
        question_attempt_id: ID of the question attempt
        
    Returns:
        Tuple of (temp_file_path, reference_name) for processing and database storage
        
    Raises:
        Exception: If file save operation fails
    """
    # Generate unique reference filename using hash
    file_hash = hashlib.sha256(audio_bytes + str(question_attempt_id).encode()).hexdigest()[:16]
    file_ext = Path(filename).suffix.lower() or ".audio"
    reference_name = f"qa_{question_attempt_id}_{file_hash}{file_ext}"
    
    try:
        # Use asyncio to run the blocking file I/O in a thread
        def _create_temp_file():
            temp_file = tempfile.NamedTemporaryFile(
                suffix=file_ext,
                prefix=f"audio_qa_{question_attempt_id}_",
                delete=False  # We'll handle cleanup manually
            )
            
            with temp_file:
                temp_file.write(audio_bytes)
                return temp_file.name
        
        temp_path = await asyncio.get_event_loop().run_in_executor(None, _create_temp_file)
        return temp_path, reference_name
    
    except Exception as e:
        raise Exception(f"Failed to create temporary audio file: {str(e)}")


async def cleanup_temp_audio_file(temp_path: str) -> None:
    """
    Clean up temporary audio file after processing.
    
    Args:
        temp_path: Path to the temporary file to clean up
    """
    try:
        def _cleanup_file():
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        
        await asyncio.get_event_loop().run_in_executor(None, _cleanup_file)
    except Exception:
        # Ignore cleanup errors - temp files will be cleaned by OS eventually
        pass


def get_audio_duration_estimate(audio_bytes: bytes, content_type: str) -> float | None:
    """
    Estimate audio duration from file metadata (basic implementation).
    
    Args:
        audio_bytes: Raw audio file bytes
        content_type: MIME type
        
    Returns:
        Estimated duration in seconds, or None if cannot determine
    """
    # This is a simplified estimation - in production you might want to use
    # libraries like mutagen or ffprobe for accurate duration detection
    try:
        if content_type == "audio/wav" and len(audio_bytes) > 44:
            # Basic WAV duration calculation (assumes standard format)
            # Sample rate is at bytes 24-27, bits per sample at 34-35
            sample_rate = int.from_bytes(audio_bytes[24:28], byteorder='little')
            if sample_rate > 0:
                data_size = len(audio_bytes) - 44  # Subtract header
                duration = data_size / (sample_rate * 2 * 2)  # Assume 16-bit stereo
                return max(0.1, min(duration, MAX_DURATION_SECONDS))
        
        # For other formats, rough estimate based on file size and typical bitrates
        if content_type == "audio/mpeg":
            # Rough estimate: 128kbps average for MP3
            duration = (len(audio_bytes) * 8) / (128 * 1000)
            return max(0.1, min(duration, MAX_DURATION_SECONDS))
        
        return None
    except Exception:
        return None


def extract_audio_energy_features(audio_bytes: bytes, content_type: str) -> dict:
    """Extract simple prosody features for energy analysis.

    Returns
    -------
    dict with keys:
      - pitch_variation: float (0-1 scaled variance proxy)
      - volume_variation: float (0-1 scaled variance proxy)
      - source: str ("waveform" | "fallback")
    """
    wav_types = {"audio/wav", "audio/x-wav", "audio/wave"}
    if content_type not in wav_types:
        return {
            "pitch_variation": 0.0,
            "volume_variation": 0.0,
            "source": "fallback",
        }

    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            sample_rate = wf.getframerate()
            sample_width = wf.getsampwidth()
            frame_count = wf.getnframes()
            if sample_rate <= 0 or sample_width <= 0 or frame_count <= 0:
                raise ValueError("Invalid WAV metadata")

            window_ms = 50
            frames_per_window = max(1, int(sample_rate * (window_ms / 1000.0)))
            rms_values: list[float] = []
            zcr_values: list[float] = []

            while True:
                chunk = wf.readframes(frames_per_window)
                if not chunk:
                    break
                if len(chunk) < sample_width:
                    continue

                rms = float(audioop.rms(chunk, sample_width))
                crosses = float(audioop.cross(chunk, sample_width))
                zcr = crosses / max(1.0, len(chunk) / sample_width)
                rms_values.append(rms)
                zcr_values.append(zcr)

            if len(rms_values) < 2 or len(zcr_values) < 2:
                raise ValueError("Insufficient audio windows")

            volume_variation = statistics.pstdev(rms_values) / max(1.0, statistics.fmean(rms_values))
            pitch_variation = statistics.pstdev(zcr_values) / max(1.0, statistics.fmean(zcr_values))

            return {
                "pitch_variation": round(min(1.0, max(0.0, pitch_variation)), 4),
                "volume_variation": round(min(1.0, max(0.0, volume_variation)), 4),
                "source": "waveform",
            }
    except Exception:
        return {
            "pitch_variation": 0.0,
            "volume_variation": 0.0,
            "source": "fallback",
        }
