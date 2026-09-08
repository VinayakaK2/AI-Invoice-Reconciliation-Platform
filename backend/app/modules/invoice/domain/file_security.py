"""app/modules/invoice/domain/file_security.py

Deterministic file signature verification, magic byte detection, and MIME type enforcement.
Zero external C-library dependencies to guarantee cross-platform stability and security.
"""

from pathlib import Path
from typing import Dict, Set, Tuple
from app.shared.exceptions import ValidationError

# Allowed MIME types and their corresponding valid file extensions
ALLOWED_MIME_TYPES: Set[str] = {
    "application/pdf",
    "image/png",
    "image/jpeg",
}

MIME_TO_EXTENSIONS: Dict[str, Set[str]] = {
    "application/pdf": {".pdf"},
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
}

MIN_DOCUMENT_SIZE_BYTES = 8  # Minimal valid header size for supported formats


class FileSecurityValidator:
    """Validates binary signatures, file size bounds, and filename integrity."""

    @staticmethod
    def detect_true_mime(content: bytes) -> str:
        """Detect actual MIME type from leading magic bytes and file signatures.

        Raises ValidationError if signature does not match the approved whitelist
        or if executable/script content is detected.
        """
        if len(content) < MIN_DOCUMENT_SIZE_BYTES:
            raise ValidationError("File content is too small to be a valid document.")

        # 1. PDF Signature Check: '%PDF-' (0x25, 0x50, 0x44, 0x46, 0x2D) within first 1024 bytes
        header_slice = content[:1024]
        if b"%PDF-" in header_slice:
            pdf_idx = header_slice.find(b"%PDF-")
            # PDF header must be within the initial 128 bytes (permits optional BOM/comments)
            if pdf_idx <= 128:
                return "application/pdf"

        # 2. PNG Signature Check: 89 50 4E 47 0D 0A 1A 0A
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"

        # 3. JPEG Signature Check: FF D8 FF (Start of Image + marker)
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"

        # Block executable, shell, and script polyglot signatures
        lower_prefix = content[:512].lower()
        if (
            b"<html" in lower_prefix
            or b"<script" in lower_prefix
            or b"<?php" in lower_prefix
            or lower_prefix.startswith(b"mz")  # DOS/PE executable
            or lower_prefix.startswith(b"\x7felf")  # Linux ELF executable
            or lower_prefix.startswith(b"#!/")  # Shell script shebang
        ):
            raise ValidationError("Executable, script, or HTML content detected in uploaded file.")

        raise ValidationError(
            "Unsupported file signature. Uploaded file must be a valid PDF, PNG, or JPEG document."
        )

    @classmethod
    def validate_file_safety(
        cls,
        content: bytes,
        filename: str,
        max_size_bytes: int = 10 * 1024 * 1024,
    ) -> Tuple[str, str]:
        """Validate size bounds, magic byte signatures, and extension cross-matching.

        Returns:
            Tuple[detected_mime_type, normalized_extension]
        """
        # 1. Filename extension validation
        ext = Path(filename).suffix.lower()
        if not ext:
            raise ValidationError("File must have a valid extension (.pdf, .png, .jpg, .jpeg).")

        all_allowed_extensions = {e for exts in MIME_TO_EXTENSIONS.values() for e in exts}
        if ext not in all_allowed_extensions:
            raise ValidationError(
                f"File extension not allowed: '{filename}'. Only PDF and common image formats are supported."
            )

        # 2. Size bounds
        file_size = len(content)
        if file_size == 0:
            raise ValidationError("Uploaded file cannot be empty.")
        if file_size < MIN_DOCUMENT_SIZE_BYTES:
            raise ValidationError("File is corrupted or smaller than minimum valid size.")
        if file_size > max_size_bytes:
            raise ValidationError(
                f"File size ({file_size} bytes) exceeds maximum limit of {max_size_bytes} bytes."
            )

        # 3. Magic byte detection
        detected_mime = cls.detect_true_mime(content)

        # 4. Cross-match detected MIME with file extension
        valid_exts = MIME_TO_EXTENSIONS.get(detected_mime, set())
        if ext not in valid_exts:
            raise ValidationError(
                f"MIME type spoofing detected: file extension '{ext}' does not match "
                f"detected file content type '{detected_mime}' (expected: {sorted(list(valid_exts))})."
            )

        return detected_mime, ext
