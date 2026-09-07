"""Application ports and service boundaries for statement storage and parsing.

Defines abstract interfaces allowing clean replacement of local statement storage
with cloud storage (S3/GCS/Azure Blob), and statement parsers with specialized bank
adapters or external financial data providers without mutating domain logic.
"""

from abc import ABC, abstractmethod
import hashlib
from pathlib import Path
import re
from typing import List, Optional, Tuple
from uuid import UUID

from app.config import settings
from app.modules.payment.domain.entities import CSVRowError, ParsedBankTransaction
from app.shared.exceptions import NotFoundError, ValidationError


class StorageService(ABC):
    """Abstract Port for object/file storage of uploaded bank statements."""

    @abstractmethod
    def save_file(self, content: bytes, filename: str, company_id: UUID) -> Tuple[str, str, int]:
        """Save file bytes and return (storage_key, sha256_hash, file_size_bytes)."""
        pass

    @abstractmethod
    def get_file(self, storage_key: str, company_id: UUID) -> bytes:
        """Retrieve statement file bytes for the specified tenant, verifying ownership."""
        pass

    @abstractmethod
    def delete_file(self, storage_key: str, company_id: UUID) -> bool:
        """Delete statement file associated with the tenant."""
        pass


class LocalStorageService(StorageService):
    """Local filesystem storage implementation with strict tenant isolation and path validation."""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        self.base_dir = Path(base_dir or settings.STATEMENT_STORAGE_DIR).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_tenant_dir(self, company_id: UUID) -> Path:
        """Derive and ensure isolated directory for a company tenant."""
        tenant_dir = (self.base_dir / str(company_id)).resolve()
        # Path traversal guard: verify path stays within base directory
        if not str(tenant_dir).startswith(str(self.base_dir)):
            raise ValidationError("Invalid tenant directory path traversal.")
        tenant_dir.mkdir(parents=True, exist_ok=True)
        return tenant_dir

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize raw uploaded filename to prevent directory traversal and special chars."""
        clean_name = Path(filename).name
        if "\x00" in clean_name:
            raise ValidationError("Illegal null byte in filename.")
        clean_name = re.sub(r"[^a-zA-Z0-9._-]", "_", clean_name)
        return clean_name or "statement.csv"

    def save_file(self, content: bytes, filename: str, company_id: UUID) -> Tuple[str, str, int]:
        """Persist file to tenant-isolated disk storage using content-addressed hash."""
        if not content:
            raise ValidationError("Uploaded file content cannot be empty.")

        if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
            raise ValidationError(
                f"File size ({len(content)} bytes) exceeds maximum limit of {settings.MAX_UPLOAD_SIZE_BYTES} bytes."
            )

        tenant_dir = self._get_tenant_dir(company_id)
        sha256_hash = hashlib.sha256(content).hexdigest()
        clean_name = self._sanitize_filename(filename)
        storage_key = f"{sha256_hash}_{clean_name}"

        target_file = (tenant_dir / storage_key).resolve()
        if not str(target_file).startswith(str(tenant_dir)):
            raise ValidationError("Illegal path traversal detected in statement storage key.")

        with open(target_file, "wb") as f:
            f.write(content)

        return storage_key, sha256_hash, len(content)

    def get_file(self, storage_key: str, company_id: UUID) -> bytes:
        """Retrieve statement file bytes ensuring strict tenant boundary."""
        tenant_dir = self._get_tenant_dir(company_id)
        target_file = (tenant_dir / storage_key).resolve()

        if not str(target_file).startswith(str(tenant_dir)):
            raise ValidationError("Illegal path traversal in storage key.")

        if not target_file.exists() or not target_file.is_file():
            raise NotFoundError("Bank statement file", storage_key)

        with open(target_file, "rb") as f:
            return f.read()

    def delete_file(self, storage_key: str, company_id: UUID) -> bool:
        """Safely delete file within tenant boundary."""
        tenant_dir = self._get_tenant_dir(company_id)
        target_file = (tenant_dir / storage_key).resolve()

        if not str(target_file).startswith(str(tenant_dir)):
            raise ValidationError("Illegal path traversal in storage key.")

        if target_file.exists() and target_file.is_file():
            target_file.unlink()
            return True
        return False


class BankStatementParser(ABC):
    """Abstract Port for parsing bank statement files."""

    @abstractmethod
    def parse(
        self,
        content: bytes,
        default_currency: str = "INR",
        bank_account_number: Optional[str] = None,
    ) -> Tuple[List[ParsedBankTransaction], List[CSVRowError]]:
        """Parse statement bytes into validated transactions and granular row errors."""
        pass
