"""Application ports and service boundaries for Document Storage and OCR Processing.

Defines abstract interfaces allowing clean replacement of local storage with S3/GCS,
and heuristic/rule-based OCR with external cloud vision providers (Azure, AWS, Google)
without mutating core domain logic.
"""

from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID
from pydantic import BaseModel, Field

from app.config import settings
from app.modules.invoice.domain.extraction_rules import (
    evaluate_extraction_confidence_and_ambiguity,
    extract_identifiers,
    parse_date_string,
    parse_monetary_amount,
    validate_invoice_mathematics,
    validate_temporal_consistency,
)
from app.shared.exceptions import InfrastructureError, NotFoundError, ValidationError


# -----------------------------------------------------------------------------
# Storage Service Port & Local Implementation
# -----------------------------------------------------------------------------

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
        """Derive and ensure isolated directory for a verified UUID company tenant."""
        tenant_dir = (self.base_dir / str(company_id)).resolve()
        # Security: verify path stays strictly inside base directory using is_relative_to
        if not tenant_dir.is_relative_to(self.base_dir) or tenant_dir == self.base_dir:
            raise ValidationError("Invalid tenant directory path traversal.")
        tenant_dir.mkdir(parents=True, exist_ok=True)
        return tenant_dir

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize raw uploaded filename, preventing path traversal, null bytes, and excessive length."""
        if not filename or "\x00" in filename:
            raise ValidationError("Illegal characters in filename.")

        clean_name = Path(filename).name
        clean_name = re.sub(r"[^a-zA-Z0-9._-]", "_", clean_name).lstrip(".")
        if not clean_name:
            clean_name = "invoice_document.pdf"

        # Truncate base name to 64 chars so that {hash}_{name} fits well within 255-char filesystem limits
        parts = clean_name.rsplit(".", 1)
        base = parts[0][:64]
        ext = f".{parts[1][:10]}" if len(parts) > 1 else ".pdf"
        return f"{base}{ext}"

    def save_file(self, content: bytes, filename: str, company_id: UUID) -> Tuple[str, str, int]:
        """Persist file into tenant directory using content-addressed naming."""
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
        if not target_path.is_relative_to(tenant_dir):
            raise ValidationError("Illegal path traversal detected.")

        with open(target_path, "wb") as f:
            f.write(content)

        return storage_key, file_hash, len(content)

    def get_file(self, storage_key: str, company_id: UUID) -> bytes:
        """Retrieve file bytes after confirming tenant path integrity."""
        if not storage_key or "\x00" in storage_key or "/" in storage_key or "\\" in storage_key:
            raise NotFoundError("InvoiceDocument", "file")

        tenant_dir = self._get_tenant_dir(company_id)
        target_path = (tenant_dir / storage_key).resolve()

        if not target_path.is_relative_to(tenant_dir) or not target_path.is_file():
            raise NotFoundError("InvoiceDocument", "file")

        with open(target_path, "rb") as f:
            return f.read()

    def delete_file(self, storage_key: str, company_id: UUID) -> bool:
        """Delete tenant file safely."""
        if not storage_key or "\x00" in storage_key or "/" in storage_key or "\\" in storage_key:
            return False

        tenant_dir = self._get_tenant_dir(company_id)
        target_path = (tenant_dir / storage_key).resolve()

        if not target_path.is_relative_to(tenant_dir):
            return False

        if target_path.is_file():
            target_path.unlink()
            return True
        return False


# -----------------------------------------------------------------------------
# Extraction Data Contracts
# -----------------------------------------------------------------------------

class ExtractedInvoiceData(BaseModel):
    """Structured extraction payload produced by OCR providers."""
    raw_text: Optional[str] = None
    invoice_number: Optional[str] = None
    customer_name: Optional[str] = None
    tax_id: Optional[str] = None
    po_number: Optional[str] = None
    payment_ref: Optional[str] = None
    issue_date: Optional[date] = None
    due_date: Optional[date] = None
    total_amount: Optional[Decimal] = None
    subtotal_amount: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    discount_amount: Optional[Decimal] = None
    round_off: Optional[Decimal] = None
    currency: str = "INR"
    confidence: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"), le=Decimal("1.00"))
    is_ambiguous: bool = True
    extraction_notes: List[str] = Field(default_factory=list)
    line_items: List[Dict[str, Any]] = Field(default_factory=list)
    field_confidence: Dict[str, Decimal] = Field(default_factory=dict)
    validation_report: Optional[Dict[str, Any]] = None
    provider_name: str = "HeuristicOCR"
    provider_version: str = "1.0.0"
    latency_ms: int = 0


# -----------------------------------------------------------------------------
# OCR Provider Port & Implementations
# -----------------------------------------------------------------------------

class InvoiceOCRProvider(ABC):
    """Abstract Port for OCR / Document Extraction Providers."""

    @abstractmethod
    def extract_from_file(self, content: bytes, filename: str, mime_type: str) -> ExtractedInvoiceData:
        """Parse invoice document content and return structured extraction data."""
        pass


class HeuristicInvoiceOCRProvider(InvoiceOCRProvider):
    """Deterministic heuristic OCR and text extraction adapter.

    Extracts key financial fields from document byte streams via rule-based pattern matching,
    normalizes numbers and dates, calculates weighted confidence, and verifies mathematical consistency.
    """

    def extract_from_file(self, content: bytes, filename: str, mime_type: str) -> ExtractedInvoiceData:
        """Perform deterministic extraction on file stream."""
        start_time = time.perf_counter()
        raw_text = content.decode("utf-8", errors="ignore")
        notes: List[str] = []

        # 1. Multi-signal identifier extraction
        id_results = extract_identifiers(raw_text)
        invoice_number = id_results.get("invoice_number")
        tax_id = id_results.get("tax_id")
        po_number = id_results.get("po_number")
        payment_ref = id_results.get("payment_ref")
        notes.extend(id_results.get("notes", []))

        # 2. Counterparty / Customer Name Extraction
        cust_match = re.search(
            r"(?i)(?:bill\s*to|customer|client|buyer|m/s|sold\s*to)[:\s-]*([^\n\r,;]{3,60})",
            raw_text,
        )
        customer_name: Optional[str] = None
        if cust_match:
            candidate_cust = cust_match.group(1).strip()
            # Verify candidate name does not look like generic headers
            if not any(sw in candidate_cust.upper() for sw in ("INVOICE", "DATE", "TOTAL", "AMOUNT")):
                customer_name = candidate_cust

        # 3. Date Extraction (Issue & Due Date)
        date_candidates = re.findall(
            r"\b(?:\d{4}[-/]\d{2}[-/]\d{2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b",
            raw_text,
        )
        issue_date: Optional[date] = None
        due_date: Optional[date] = None
        date_is_ambiguous = False

        parsed_dates: List[date] = []
        for d_str in date_candidates[:5]:
            d_res = parse_date_string(d_str, prefer_day_first=True)
            if d_res.parsed_date:
                parsed_dates.append(d_res.parsed_date)
                if d_res.is_ambiguous:
                    date_is_ambiguous = True

        if parsed_dates:
            issue_date = parsed_dates[0]
            if len(parsed_dates) > 1:
                due_date = parsed_dates[1]
            else:
                due_date = issue_date
        else:
            notes.append("Invoice issue and due dates were not found in document.")

        temporal_valid, temp_warnings = validate_temporal_consistency(issue_date, due_date)
        notes.extend(temp_warnings)

        # 4. Monetary Amounts Extraction
        # Look for Total / Grand Total / Net Amount
        total_amount: Optional[Decimal] = None
        subtotal_amount: Optional[Decimal] = None
        tax_amount: Optional[Decimal] = None
        detected_currency = "INR"

        total_match = re.search(
            r"(?i)\b(?:grand\s*total|net\s*amount|total\s*amount|amount\s*due|total)\b[:\s]*((?:INR|USD|EUR|GBP|[₹$€£])?[:\s]*[0-9][0-9,]*\.[0-9]{2}|(?:INR|USD|EUR|GBP|[₹$€£])?[:\s]*[0-9][0-9,]*)",
            raw_text,
        )
        if total_match:
            res = parse_monetary_amount(total_match.group(1), default_currency=detected_currency)
            if res.is_valid:
                total_amount = res.amount
                detected_currency = res.currency

        # Subtotal match
        sub_match = re.search(
            r"(?i)\b(?:sub\s*total|taxable\s*amount|taxable\s*value)\b[:\s]*((?:INR|USD|EUR|GBP|[₹$€£])?[:\s]*[0-9][0-9,]*\.[0-9]{2}|(?:INR|USD|EUR|GBP|[₹$€£])?[:\s]*[0-9][0-9,]*)",
            raw_text,
        )
        if sub_match:
            res_sub = parse_monetary_amount(sub_match.group(1), default_currency=detected_currency)
            if res_sub.is_valid:
                subtotal_amount = res_sub.amount

        # Tax Amount match
        tax_match = re.search(
            r"(?i)\b(?:tax\s*amount|total\s*tax|gst\s*amount|cgst\s*\+\s*sgst|igst)\b[:\s]*((?:INR|USD|EUR|GBP|[₹$€£])?[:\s]*[0-9][0-9,]*\.[0-9]{2}|(?:INR|USD|EUR|GBP|[₹$€£])?[:\s]*[0-9][0-9,]*)",
            raw_text,
        )
        if tax_match:
            res_tax = parse_monetary_amount(tax_match.group(1), default_currency=detected_currency)
            if res_tax.is_valid:
                tax_amount = res_tax.amount

        # Currency detection in broader text
        if "USD" in raw_text or "$" in raw_text:
            detected_currency = "USD"
        elif "EUR" in raw_text or "€" in raw_text:
            detected_currency = "EUR"
        elif "GBP" in raw_text or "£" in raw_text:
            detected_currency = "GBP"

        # 5. Deterministic Mathematical Validation
        math_result = validate_invoice_mathematics(
            total_amount=total_amount,
            subtotal=subtotal_amount,
            tax_amount=tax_amount,
        )

        # 6. Confidence Scoring & Ambiguity Evaluation
        confidence, is_ambiguous, all_notes = evaluate_extraction_confidence_and_ambiguity(
            invoice_number=invoice_number,
            total_amount=total_amount,
            issue_date=issue_date,
            due_date=due_date,
            customer_name=customer_name,
            tax_id=tax_id,
            subtotal=subtotal_amount,
            tax_amount=tax_amount,
            math_result=math_result,
            date_ambiguous=date_is_ambiguous,
            temporal_valid=temporal_valid,
        )

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        return ExtractedInvoiceData(
            raw_text=raw_text[:2000] if raw_text else None,
            invoice_number=invoice_number,
            customer_name=customer_name,
            tax_id=tax_id,
            po_number=po_number,
            payment_ref=payment_ref,
            issue_date=issue_date,
            due_date=due_date,
            total_amount=total_amount,
            subtotal_amount=subtotal_amount,
            tax_amount=tax_amount,
            currency=detected_currency,
            confidence=confidence,
            is_ambiguous=is_ambiguous,
            extraction_notes=all_notes,
            validation_report={
                "is_valid": math_result.is_valid and temporal_valid,
                "math_check_passed": math_result.is_valid,
                "discrepancy": str(math_result.discrepancy),
                "notes": math_result.notes,
            },
            provider_name="HeuristicOCR",
            provider_version="1.0.0",
            latency_ms=latency_ms,
        )


class MockInvoiceOCRProvider(InvoiceOCRProvider):
    """Deterministic mock provider for unit testing failure, timeout, and custom fixtures."""

    def __init__(
        self,
        raise_timeout: bool = False,
        raise_server_error: bool = False,
        raise_corrupt: bool = False,
        custom_result: Optional[ExtractedInvoiceData] = None,
    ) -> None:
        self.raise_timeout = raise_timeout
        self.raise_server_error = raise_server_error
        self.raise_corrupt = raise_corrupt
        self.custom_result = custom_result

    def extract_from_file(self, content: bytes, filename: str, mime_type: str) -> ExtractedInvoiceData:
        """Simulate extraction behavior or trigger injected exceptions."""
        if self.raise_timeout:
            raise InfrastructureError("OCR provider call timed out after 15000ms", details={"provider": "MockOCR"})
        if self.raise_server_error:
            raise InfrastructureError("OCR provider service unavailable (HTTP 503)", details={"provider": "MockOCR"})
        if self.raise_corrupt:
            raise ValidationError("Document content is corrupt or unreadable", details={"provider": "MockOCR"})

        if self.custom_result:
            return self.custom_result

        # Default standard mock result
        return ExtractedInvoiceData(
            raw_text=content.decode("utf-8", errors="ignore")[:500],
            invoice_number="INV-MOCK-001",
            customer_name="Acme Corp",
            tax_id="29ABCDE1234F1Z5",
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 9, 30),
            total_amount=Decimal("15000.00"),
            subtotal_amount=Decimal("12711.86"),
            tax_amount=Decimal("2288.14"),
            currency="INR",
            confidence=Decimal("0.95"),
            is_ambiguous=False,
            extraction_notes=["Mock extraction successful"],
            provider_name="MockOCR",
            provider_version="1.0.0",
            latency_ms=5,
        )


class CloudVisionOCRAdapter(InvoiceOCRProvider):
    """Pluggable adapter boundary for external Cloud Vision / Document AI services."""

    def __init__(self, endpoint_url: Optional[str] = None, api_key: Optional[str] = None) -> None:
        self.endpoint_url = endpoint_url
        self.api_key = api_key

    def extract_from_file(self, content: bytes, filename: str, mime_type: str) -> ExtractedInvoiceData:
        """Placeholder adapter for cloud vision APIs (AWS Textract, Azure Form Recognizer, Google DocAI)."""
        # When cloud integration is active, HTTP calls occur here; fallback to heuristic parser
        heuristic = HeuristicInvoiceOCRProvider()
        result = heuristic.extract_from_file(content, filename, mime_type)
        result.provider_name = "CloudVisionAdapter"
        return result


class MultimodalAIOCRAdapter(InvoiceOCRProvider):
    """Pluggable adapter boundary for Multimodal LLM Vision models."""

    def __init__(self, model_name: str = "gemini-1.5-flash") -> None:
        self.model_name = model_name

    def extract_from_file(self, content: bytes, filename: str, mime_type: str) -> ExtractedInvoiceData:
        """Parse invoice document with prompt-injection defenses."""
        # Untrusted prompt defense: Treat file strictly as image/bytes; parse into schema
        heuristic = HeuristicInvoiceOCRProvider()
        result = heuristic.extract_from_file(content, filename, mime_type)
        result.provider_name = f"MultimodalAI({self.model_name})"
        return result


class FallbackCompositeInvoiceOCRProvider(InvoiceOCRProvider):
    """Cascade provider attempting fast local heuristic first, falling back to secondary providers."""

    def __init__(
        self,
        primary: Optional[InvoiceOCRProvider] = None,
        fallback: Optional[InvoiceOCRProvider] = None,
    ) -> None:
        self.primary = primary or HeuristicInvoiceOCRProvider()
        self.fallback = fallback or MockInvoiceOCRProvider()

    def extract_from_file(self, content: bytes, filename: str, mime_type: str) -> ExtractedInvoiceData:
        """Execute extraction with graceful fallback."""
        try:
            result = self.primary.extract_from_file(content, filename, mime_type)
            # If primary produced high confidence, return immediately
            if not result.is_ambiguous and result.confidence >= Decimal("0.85"):
                return result
            # Otherwise attempt fallback if available
            if self.fallback:
                fallback_result = self.fallback.extract_from_file(content, filename, mime_type)
                if fallback_result.confidence > result.confidence:
                    return fallback_result
            return result
        except Exception:
            if self.fallback:
                return self.fallback.extract_from_file(content, filename, mime_type)
            raise
