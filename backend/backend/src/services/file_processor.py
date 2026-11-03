import pathlib
import fastapi
from fastapi import UploadFile
from typing import Tuple

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB default
_VALIDATION_CHUNK_SIZE = 1024 * 1024


async def validate_file(
    file: UploadFile, max_size: int = MAX_FILE_SIZE_BYTES
) -> Tuple[str, int]:
    """
    Validates file extension and size.
    Reads the file into memory only during this call — no bytes are written to disk.
    Returns (file_extension, file_size).
    """
    filename = file.filename or ""
    extension = pathlib.Path(filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {extension}. Allowed types: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Count in bounded chunks and stop as soon as the limit is exceeded. This
    # prevents an oversized request (for example a 100MB PDF) from being copied
    # into application memory merely to discover that it is too large.
    await file.seek(0)
    file_size = 0
    while True:
        remaining_before_rejection = max_size - file_size + 1
        chunk = await file.read(min(_VALIDATION_CHUNK_SIZE, remaining_before_rejection))
        if not chunk:
            break
        file_size += len(chunk)
        if file_size > max_size:
            raise fastapi.HTTPException(
                status_code=fastapi.status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds {max_size // (1024 * 1024)}MB limit",
            )

    # Downstream extractors expect to read the validated file from the start.
    await file.seek(0)
    return extension, file_size
