"""Application ports and service boundaries for Document Storage and OCR Processing.

Defines abstract interfaces allowing clean replacement of local storage with S3/GCS,
and heuristic/rule-based OCR with external cloud vision providers (Azure, AWS, Google)
without mutating core domain logic.
"""

from abc import ABC, abstractmethod
from datetime import date, datetime
from decimal import Decimal
import hashlib
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID
from pydantic import BaseModel, Field
from app.config import settings
from app.shared.exceptions import NotFoundError, ValidationError


class StorageService(ABC):
    """Abstract Port for object/file storage."""

    @abstractmethod
    def save_file(self, content: bytes, filename: str, company_id: UUID) -> Tuple[str, str, int]:
        """Save file bytes and return (storage_key, sha256_hash, file_size_bytes)."""
        pass

    @abstractmethod
    def get_file(self, storage_key: str, company_id: UUID) -> bytes:
        """Retrieve file bytes for the specified tenant, verifying ownership."""
        pass

    @abstractmethod
    def delete_file(self, storage_key: str, company_id: UUID) -> bool:
        """Delete file associated with the tenant."""
        pass


class LocalStorageService(StorageService):
    """Local filesystem storage implementation with strict tenant isolation and path validation."""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        self.base_dir = Path(base_dir or settings.STORAGE_DIR).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_tenant_dir(self, company_id: UUID) -> Path:
        """Derive and ensure isolated directory for a company tenant."""
        tenant_dir = (self.base_dir / str(company_id)).resolve()
        # Security: verify path stays within base directory
        if not str(tenant_dir).startswith(str(self.base_dir)):
            raise ValidationError("Invalid tenant directory path traversal.")
        tenant_dir.mkdir(parents=True, exist_ok=True)
        return tenant_dir

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize raw uploaded filename to prevent directory traversal and special chars."""
        clean_name = Path(filename).name
        # Keep only alphanumeric, hyphens, underscores, and dots
        clean_name = re.sub(r"[^a-zA-Z0-9._-]", "_", clean_name)
        return clean_name or "invoice_document.pdf"

    def save_file(self, content: bytes, filename: str, company_id: UUID) -> Tuple[str, str, int]:
        """Persist file into tenant directory using content hash for collision prevention."""
        if not content:
            raise ValidationError("Uploaded file cannot be empty.")
        if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
            raise ValidationError(
                f"File size ({len(content)} bytes) exceeds maximum limit of {settings.MAX_UPLOAD_SIZE_BYTES} bytes."
            )

        file_hash = hashlib.sha256(content).hexdigest()
        clean_name = self._sanitize_filename(filename)
        storage_key = f"{file_hash}_{clean_name}"

        tenant_dir = self._get_tenant_dir(company_id)
        target_path = (tenant_dir / storage_key).resolve()

        # Guard against path traversal
        if not str(target_path).startswith(str(tenant_dir)):
            raise ValidationError("Illegal path traversal detected.")

        with open(target_path, "wb") as f:
            f.write(content)

        return storage_key, file_hash, len(content)

    def get_file(self, storage_key: str, company_id: UUID) -> bytes:
        """Retrieve file bytes after confirming tenant path integrity."""
        tenant_dir = self._get_tenant_dir(company_id)
        target_path = (tenant_dir / storage_key).resolve()

        if not str(target_path).startswith(str(tenant_dir)) or not target_path.is_file():
            raise NotFoundError("InvoiceDocument", storage_key)

        with open(target_path, "rb") as f:
            return f.read()

    def delete_file(self, storage_key: str, company_id: UUID) -> bool:
        """Delete tenant file safely."""
        tenant_dir = self._get_tenant_dir(company_id)
        target_path = (tenant_dir / storage_key).resolve()

        if not str(target_path).startswith(str(tenant_dir)):
            return False

        if target_path.is_file():
            target_path.unlink()
            return True
        return False


class ExtractedInvoiceData(BaseModel):
    """Structured extraction payload produced by OCR providers."""
    raw_text: Optional[str] = None
    invoice_number: Optional[str] = None
    customer_name: Optional[str] = None
    tax_id: Optional[str] = None
    issue_date: Optional[date] = None
    due_date: Optional[date] = None
    total_amount: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    currency: str = "INR"
    confidence: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"), le=Decimal("1.00"))
    is_ambiguous: bool = True
    extraction_notes: List[str] = Field(default_factory=list)


class InvoiceOCRProvider(ABC):
    """Abstract Port for OCR / Document Extraction Providers."""

    @abstractmethod
    def extract_from_file(self, content: bytes, filename: str, mime_type: str) -> ExtractedInvoiceData:
        """Parse invoice document content and return structured extraction data."""
        pass


class HeuristicInvoiceOCRProvider(InvoiceOCRProvider):
    """Deterministic heuristic OCR and text extraction adapter.

    Extracts key financial fields from document byte streams via pattern matching,
    calculates extraction confidence, and flags ambiguous extractions for human review.
    """

    def extract_from_file(self, content: bytes, filename: str, mime_type: str) -> ExtractedInvoiceData:
        """Perform deterministic extraction on file stream."""
        # Try decoding printable text from the stream
        raw_text = content.decode("utf-8", errors="ignore")
        notes: List[str] = []
        confidence_points = Decimal("0.00")

        # 1. Invoice Number Extraction
        # Look for explicit patterns like "Invoice Number: INV-123", "Invoice #: 123", "Bill No: 123", "INV: 123"
        inv_match = re.search(
            r"(?i)\b(?:invoice\s*(?:no\.?|num\.?|number|#)?|bill\s*(?:no\.?|num\.?|#)?|inv)\s*[:#]\s*([A-Z0-9\-_/]{3,30})\b",
            raw_text,
        )
        if not inv_match:
            inv_match = re.search(r"\b(INV-[A-Z0-9\-_/]{3,30})\b", raw_text, re.IGNORECASE)

        invoice_number: Optional[str] = None
        if inv_match:
            cand = inv_match.group(1).strip()
            if cand.upper() not in {"NUMBER", "DATE", "AMOUNT", "TOTAL", "CLIENT", "CUSTOMER", "DRAFT"}:
                invoice_number = cand
                confidence_points += Decimal("0.30")

        if not invoice_number:
            notes.append("Invoice number could not be detected with high confidence.")

        # 2. Tax ID / GSTIN Extraction
        # Standard Indian GSTIN: 15 alphanumeric (2 state + 10 PAN + 1 entity + 1 'Z' + 1 checksum)
        gst_match = re.search(
            r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b",
            raw_text,
        )
        tax_id: Optional[str] = None
        if gst_match:
            tax_id = gst_match.group(1).strip()
            confidence_points += Decimal("0.15")

        # 3. Customer / Counterparty Name Extraction
        cust_match = re.search(
            r"(?i)(?:bill\s*to|customer|client|buyer|m/s)[:\s-]*([^\n\r,;]{3,50})",
            raw_text,
        )
        customer_name: Optional[str] = None
        if cust_match:
            customer_name = cust_match.group(1).strip()
            confidence_points += Decimal("0.15")
        elif tax_id:
            notes.append("Customer name absent, but valid Tax ID / GSTIN was extracted.")
        else:
            notes.append("Customer name could not be identified.")

        # 4. Date Extraction (Issue & Due Date)
        date_matches = re.findall(
            r"\b(\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4})\b",
            raw_text,
        )
        issue_date: Optional[date] = None
        due_date: Optional[date] = None

        parsed_dates: List[date] = []
        for d_str in date_matches[:4]:
            d_clean = d_str.replace("/", "-")
            parts = d_clean.split("-")
            try:
                if len(parts[0]) == 4:  # YYYY-MM-DD
                    parsed_dates.append(date(int(parts[0]), int(parts[1]), int(parts[2])))
                elif len(parts[2]) == 4:  # DD-MM-YYYY
                    parsed_dates.append(date(int(parts[2]), int(parts[1]), int(parts[0])))
            except (ValueError, IndexError):
                continue

        if parsed_dates:
            issue_date = parsed_dates[0]
            confidence_points += Decimal("0.15")
            if len(parsed_dates) > 1:
                due_date = parsed_dates[1]
                confidence_points += Decimal("0.10")
            else:
                due_date = issue_date  # Default due date to issue date if not specified
        else:
            notes.append("Invoice issue and due dates were not found in document.")

        # 5. Total Amount & Tax Amount Extraction
        amount_match = re.search(
            r"(?i)\b(?:total\s*amount|grand\s*total|net\s*amount|amount\s*due|total)\b[:\s]*(?:INR|USD|EUR|[₹$€£])?[:\s]*([0-9,]+\.[0-9]{2}|[0-9,]+)",
            raw_text,
        )
        total_amount: Optional[Decimal] = None
        if amount_match:
            amt_str = amount_match.group(1).replace(",", "")
            try:
                total_amount = Decimal(amt_str)
                if total_amount > 0:
                    confidence_points += Decimal("0.20")
            except Exception:
                total_amount = None

        if total_amount is None:
            notes.append("Invoice total monetary amount could not be reliably extracted.")

        # 6. Currency Extraction
        currency = "INR"
        if "USD" in raw_text or "$" in raw_text:
            currency = "USD"
        elif "EUR" in raw_text or "€" in raw_text:
            currency = "EUR"

        # Cap confidence between 0.00 and 1.00
        confidence = min(Decimal("1.00"), confidence_points)
        # Deem extraction ambiguous if crucial fields are missing or confidence < 0.85
        is_ambiguous = (
            confidence < Decimal("0.85")
            or invoice_number is None
            or total_amount is None
            or issue_date is None
        )

        return ExtractedInvoiceData(
            raw_text=raw_text[:2000] if raw_text else None,
            invoice_number=invoice_number,
            customer_name=customer_name,
            tax_id=tax_id,
            issue_date=issue_date,
            due_date=due_date,
            total_amount=total_amount,
            tax_amount=None,
            currency=currency,
            confidence=confidence,
            is_ambiguous=is_ambiguous,
            extraction_notes=notes,
        )
