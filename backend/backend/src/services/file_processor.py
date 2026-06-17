import pathlib
import fastapi
from fastapi import UploadFile
from typing import Tuple

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB default


async def validate_file(file: UploadFile, max_size: int = MAX_FILE_SIZE_BYTES) -> Tuple[str, int]:
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

    # Read entirely into memory to determine size — no file is written to disk.
    content = await file.read()
    file_size = len(content)

    if file_size > max_size:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {max_size // (1024 * 1024)}MB limit",
        )

    # Bytes are intentionally discarded — stateless processing only.
    return extension, file_size
