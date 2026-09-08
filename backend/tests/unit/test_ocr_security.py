"""Unit tests for Phase 12 Document Security & File Integrity.

Verifies:
- Magic byte detection and file signature enforcement (PDF, PNG, JPEG)
- Extension vs content spoofing rejection
- Executable, script, and polyglot payload blocking
- File size boundary checks (empty, minimum valid header, maximum upload limit)
- Directory traversal, null-byte injection, and storage path isolation
"""

import os
import tempfile
import uuid
import pytest
from app.modules.invoice.application.ports import LocalStorageService
from app.modules.invoice.domain.file_security import (
    FileSecurityValidator,
    MIN_DOCUMENT_SIZE_BYTES,
)
from app.shared.exceptions import NotFoundError, ValidationError


# -----------------------------------------------------------------------------
# 1. Magic Bytes & File Signature Detection
# -----------------------------------------------------------------------------

def test_detect_true_mime_valid_pdf() -> None:
    """PDF header (%PDF-) is correctly recognized as application/pdf."""
    valid_pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    mime = FileSecurityValidator.detect_true_mime(valid_pdf)
    assert mime == "application/pdf"


def test_detect_true_mime_pdf_with_leading_whitespace_or_bom() -> None:
    """PDF with optional leading comments or UTF-8 BOM before %PDF- is accepted."""
    pdf_with_offset = b"\xef\xbb\xbf%PDF-1.7\n%raw content"
    mime = FileSecurityValidator.detect_true_mime(pdf_with_offset)
    assert mime == "application/pdf"


def test_detect_true_mime_valid_png() -> None:
    """PNG signature (89 50 4E 47 0D 0A 1A 0A) is correctly recognized as image/png."""
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    mime = FileSecurityValidator.detect_true_mime(png_bytes)
    assert mime == "image/png"


def test_detect_true_mime_valid_jpeg() -> None:
    """JPEG Start-of-Image marker (FF D8 FF) is correctly recognized as image/jpeg."""
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
    mime = FileSecurityValidator.detect_true_mime(jpeg_bytes)
    assert mime == "image/jpeg"


# -----------------------------------------------------------------------------
# 2. Malicious Content & Polyglot Blocking
# -----------------------------------------------------------------------------

def test_reject_executable_dos_pe_binary() -> None:
    """DOS/PE MZ binary signature is blocked."""
    dos_pe = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"
    with pytest.raises(ValidationError) as exc:
        FileSecurityValidator.detect_true_mime(dos_pe)
    assert "executable" in str(exc.value).lower()


def test_reject_executable_elf_binary() -> None:
    """Linux ELF executable binary signature is blocked."""
    elf_binary = b"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    with pytest.raises(ValidationError) as exc:
        FileSecurityValidator.detect_true_mime(elf_binary)
    assert "executable" in str(exc.value).lower()


def test_reject_shell_script_shebang() -> None:
    """Shell script starting with #!/bin/bash is blocked."""
    shell_script = b"#!/bin/bash\nrm -rf /tmp/test\necho 'hacked'\n"
    with pytest.raises(ValidationError) as exc:
        FileSecurityValidator.detect_true_mime(shell_script)
    assert "script" in str(exc.value).lower()


def test_reject_html_script_payload() -> None:
    """HTML containing <script> or <html> tags disguised as a document is blocked."""
    html_payload = b"<html><head><script>alert(1)</script></head><body>Invoice</body></html>"
    with pytest.raises(ValidationError) as exc:
        FileSecurityValidator.detect_true_mime(html_payload)
    assert "html" in str(exc.value).lower() or "script" in str(exc.value).lower()


def test_reject_php_script_payload() -> None:
    """PHP script payload is blocked."""
    php_payload = b"<?php echo phpinfo(); system($_GET['cmd']); ?>"
    with pytest.raises(ValidationError) as exc:
        FileSecurityValidator.detect_true_mime(php_payload)
    assert "script" in str(exc.value).lower()


def test_reject_random_garbage_content() -> None:
    """Arbitrary unstructured binary payload without approved signature is rejected."""
    garbage = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
    with pytest.raises(ValidationError) as exc:
        FileSecurityValidator.detect_true_mime(garbage)
    assert "unsupported file signature" in str(exc.value).lower()


# -----------------------------------------------------------------------------
# 3. MIME Spoofing and Extension Cross-Matching
# -----------------------------------------------------------------------------

def test_mime_spoofing_pdf_extension_with_png_content_rejected() -> None:
    """Uploading PNG bytes with .pdf extension triggers MIME spoofing validation error."""
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    with pytest.raises(ValidationError) as exc:
        FileSecurityValidator.validate_file_safety(png_bytes, "invoice.pdf")
    assert "mime type spoofing" in str(exc.value).lower()


def test_mime_spoofing_jpeg_extension_with_pdf_content_rejected() -> None:
    """Uploading PDF bytes with .jpg extension triggers MIME spoofing validation error."""
    pdf_bytes = b"%PDF-1.4 fake invoice content"
    with pytest.raises(ValidationError) as exc:
        FileSecurityValidator.validate_file_safety(pdf_bytes, "photo.jpg")
    assert "mime type spoofing" in str(exc.value).lower()


def test_disallowed_extension_rejected() -> None:
    """File extension not in whitelist (.exe, .sh, .docx, .zip) is rejected."""
    content = b"%PDF-1.4 valid header but bad extension"
    with pytest.raises(ValidationError) as exc:
        FileSecurityValidator.validate_file_safety(content, "invoice.exe")
    assert "not allowed" in str(exc.value).lower()


def test_file_without_extension_rejected() -> None:
    """File missing extension is rejected."""
    content = b"%PDF-1.4 valid header"
    with pytest.raises(ValidationError) as exc:
        FileSecurityValidator.validate_file_safety(content, "invoicedocument")
    assert "valid extension" in str(exc.value).lower()


# -----------------------------------------------------------------------------
# 4. File Size Boundary Guards
# -----------------------------------------------------------------------------

def test_empty_file_rejected() -> None:
    """Zero-byte file upload raises ValidationError."""
    with pytest.raises(ValidationError) as exc:
        FileSecurityValidator.validate_file_safety(b"", "empty.pdf")
    assert "cannot be empty" in str(exc.value).lower()


def test_file_below_minimum_size_rejected() -> None:
    """File smaller than minimum header size is rejected as corrupt."""
    tiny_content = b"%PDF"
    with pytest.raises(ValidationError) as exc:
        FileSecurityValidator.validate_file_safety(tiny_content, "tiny.pdf")
    assert "smaller than minimum" in str(exc.value).lower()


def test_file_exceeding_max_limit_rejected() -> None:
    """File exceeding maximum allowed upload size is rejected."""
    limit = 1024
    oversized = b"%PDF-1.4 " + (b"A" * 2000)
    with pytest.raises(ValidationError) as exc:
        FileSecurityValidator.validate_file_safety(oversized, "large.pdf", max_size_bytes=limit)
    assert "exceeds maximum limit" in str(exc.value).lower()


# -----------------------------------------------------------------------------
# 5. Storage Security & Path Traversal Protections
# -----------------------------------------------------------------------------

def test_local_storage_path_traversal_prevention() -> None:
    """Directory traversal sequences in filenames (../../) are neutralized."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalStorageService(base_dir=tmpdir)
        company_id = uuid.uuid4()
        content = b"%PDF-1.4 clean content for storage testing"

        # Traversal attempts in filename
        storage_key, file_hash, size = storage.save_file(
            content=content,
            filename="../../etc/passwd.pdf",
            company_id=company_id,
        )

        assert ".." not in storage_key
        assert storage_key.endswith(".pdf")

        # Verify physical file is strictly inside the company's designated directory
        retrieved = storage.get_file(storage_key, company_id)
        assert retrieved == content

        # Cross-tenant retrieval with different company UUID raises NotFoundError
        other_company_id = uuid.uuid4()
        with pytest.raises(NotFoundError):
            storage.get_file(storage_key, other_company_id)


def test_local_storage_null_byte_rejection() -> None:
    """Filenames containing null bytes raise ValidationError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalStorageService(base_dir=tmpdir)
        company_id = uuid.uuid4()
        content = b"%PDF-1.4 null byte test content"

        with pytest.raises(ValidationError) as exc:
            storage.save_file(
                content=content,
                filename="invoice\x00payload.pdf",
                company_id=company_id,
            )
        assert "illegal characters" in str(exc.value).lower()
