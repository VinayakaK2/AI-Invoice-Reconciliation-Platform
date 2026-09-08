"""app/modules/invoice/presentation/upload_utils.py

Secure streaming file reader preventing memory exhaustion and denial-of-service (DoS).
Enforces pre-flight Content-Length inspection and chunk-accumulating size boundaries.
"""

from typing import Optional
from fastapi import HTTPException, Request, UploadFile, status
from app.config import settings

CHUNK_SIZE = 64 * 1024  # 64 KB buffer chunk


async def read_upload_file_safely(
    file: UploadFile,
    request: Optional[Request] = None,
    max_bytes: int = settings.MAX_UPLOAD_SIZE_BYTES,
) -> bytes:
    """Stream and buffer uploaded file while strictly enforcing size boundaries.

    Aborts stream and raises HTTP 413 immediately if Content-Length header or
    cumulative chunk accumulation exceeds max_bytes.
    """
    # 1. Early pre-flight header inspection if request is provided
    if request is not None:
        content_length_header = request.headers.get("content-length")
        if content_length_header:
            try:
                declared_len = int(content_length_header)
                if declared_len > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Upload rejected: Content-Length ({declared_len} bytes) exceeds maximum limit of {max_bytes} bytes.",
                    )
            except ValueError:
                pass  # Invalid header; fall back to chunked reading guard

    # 2. Chunk-accumulating read guard
    buffer = bytearray()
    total_bytes_read = 0

    while True:
        chunk = await file.read(CHUNK_SIZE)
        if not chunk:
            break
        total_bytes_read += len(chunk)
        if total_bytes_read > max_bytes:
            await file.close()
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Upload rejected: payload exceeds maximum permitted size of {max_bytes} bytes.",
            )
        buffer.extend(chunk)

    if len(buffer) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    return bytes(buffer)
