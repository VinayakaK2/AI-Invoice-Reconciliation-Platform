"""Invoice domain entities, value objects, and lifecycle state machines.

Pure Python domain layer implementing deterministic financial state transitions,
balance consistency rules, and multi-tenant invariants for invoices and source documents.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID
from app.shared.domain.money import Money
from app.shared.exceptions import DomainError, FinancialInvariantError, ValidationError


class InvoiceStatus(str, Enum):
    """Lifecycle states for customer invoices."""
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class InvoiceSource(str, Enum):
    """Origin source of the invoice."""
    MANUAL = "MANUAL"
    CSV_IMPORT = "CSV_IMPORT"
    PDF_UPLOAD = "PDF_UPLOAD"


class OCRStatus(str, Enum):
    """Processing states for OCR and document extraction."""
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    EXTRACTED = "EXTRACTED"
    VALIDATION_REQUIRED = "VALIDATION_REQUIRED"
    VALIDATED = "VALIDATED"
    FAILED = "FAILED"
    MANUALLY_CORRECTED = "MANUALLY_CORRECTED"

    # Backward-compatible Phase 11 aliases
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"


@dataclass
class InvoiceDocument:
    """Document metadata entity representing an uploaded original invoice file."""
    id: UUID
    company_id: UUID
    file_name: str
    storage_key: str
    file_size_bytes: int
    mime_type: str
    file_hash: str
    invoice_id: Optional[UUID] = None
    ocr_status: OCRStatus = OCRStatus.PENDING
    extracted_data: Optional[Dict[str, Any]] = None
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def belongs_to(self, company_id: UUID) -> bool:
        """Verify document belongs to the authenticated tenant context."""
        return self.company_id == company_id

    def start_processing(self) -> None:
        """Transition from UPLOADED or FAILED to actively PROCESSING."""
        if self.ocr_status not in (
            OCRStatus.UPLOADED,
            OCRStatus.PENDING,
            OCRStatus.FAILED,
            OCRStatus.VALIDATION_REQUIRED,
        ):
            raise DomainError(
                f"Cannot start processing document currently in status '{self.ocr_status.value}'."
            )
        self.ocr_status = OCRStatus.PROCESSING
        self.updated_at = datetime.now(timezone.utc)

    def mark_extracted(self, raw_data: Dict[str, Any]) -> None:
        """Record raw extraction completion before validation."""
        if self.ocr_status != OCRStatus.PROCESSING:
            raise DomainError(
                f"Cannot mark extracted; document is in status '{self.ocr_status.value}', expected PROCESSING."
            )
        self.ocr_status = OCRStatus.EXTRACTED
        self.extracted_data = raw_data
        self.updated_at = datetime.now(timezone.utc)

    def mark_validated(self, payload: Dict[str, Any]) -> None:
        """Mark document as cleanly validated and ready for invoice association."""
        if self.ocr_status not in (
            OCRStatus.EXTRACTED,
            OCRStatus.VALIDATION_REQUIRED,
            OCRStatus.PROCESSING,
            OCRStatus.MANUALLY_CORRECTED,
            OCRStatus.VALIDATED,
        ):
            raise DomainError(
                f"Cannot validate document from status '{self.ocr_status.value}'."
            )
        self.ocr_status = OCRStatus.VALIDATED
        self.extracted_data = payload
        self.updated_at = datetime.now(timezone.utc)

    def mark_validation_required(self, payload: Dict[str, Any]) -> None:
        """Flag extraction as requiring human review or correction."""
        if self.ocr_status not in (OCRStatus.EXTRACTED, OCRStatus.PROCESSING):
            raise DomainError(
                f"Validation required can only be set from EXTRACTED or PROCESSING, got '{self.ocr_status.value}'."
            )
        self.ocr_status = OCRStatus.VALIDATION_REQUIRED
        self.extracted_data = payload
        self.updated_at = datetime.now(timezone.utc)

    def mark_ocr_completed(self, data: Dict[str, Any]) -> None:
        """Backward-compatible method: record completed extraction."""
        self.ocr_status = OCRStatus.COMPLETED
        self.extracted_data = data
        self.updated_at = datetime.now(timezone.utc)

    def mark_ocr_failed(self, error_message: str, is_retryable: bool = False) -> None:
        """Record failed extraction with retry metadata."""
        self.ocr_status = OCRStatus.FAILED
        self.extracted_data = {
            "error": error_message,
            "retryable": is_retryable,
            "retry_count": self.retry_count,
            "failed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.updated_at = datetime.now(timezone.utc)

    def mark_manually_corrected(self, data: Dict[str, Any]) -> None:
        """Record human accountant corrections."""
        self.ocr_status = OCRStatus.MANUALLY_CORRECTED
        self.extracted_data = data
        self.updated_at = datetime.now(timezone.utc)

    def can_retry(self) -> bool:
        """Check if failed document can be safely retried."""
        return self.ocr_status == OCRStatus.FAILED and self.retry_count < self.max_retries



@dataclass
class Invoice:
    """Core Invoice domain aggregate root.

    Maintains exact financial balances (total, paid, outstanding),
    enforces valid state transitions, and guarantees multi-tenant containment.
    """
    id: UUID
    company_id: UUID
    invoice_number: str
    issue_date: date
    due_date: date
    total_amount: Money
    paid_amount: Money
    outstanding_amount: Money
    currency: str = "INR"
    status: InvoiceStatus = InvoiceStatus.PENDING
    source: InvoiceSource = InvoiceSource.MANUAL
    customer_id: Optional[UUID] = None
    document_id: Optional[UUID] = None
    tax_amount: Optional[Money] = None
    notes: Optional[str] = None
    is_archived: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        """Validate core invariants on initialization."""
        self.validate_invariants()

    def validate_invariants(self) -> None:
        """Check financial and temporal business rules."""
        if self.due_date < self.issue_date:
            raise ValidationError(
                f"Due date ({self.due_date}) cannot be earlier than issue date ({self.issue_date})."
            )
        if not self.total_amount.is_positive():
            raise ValidationError(
                f"Invoice total amount must be positive, got {self.total_amount}."
            )
        if self.paid_amount.is_negative():
            raise ValidationError(
                f"Invoice paid amount cannot be negative, got {self.paid_amount}."
            )
        if self.outstanding_amount.is_negative():
            raise ValidationError(
                f"Invoice outstanding amount cannot be negative, got {self.outstanding_amount}."
            )
        expected_outstanding = self.total_amount - self.paid_amount
        if self.outstanding_amount != expected_outstanding:
            raise DomainError(
                f"Balance conservation violated: total ({self.total_amount}) - "
                f"paid ({self.paid_amount}) != outstanding ({self.outstanding_amount})."
            )
        if self.status != InvoiceStatus.DRAFT and self.customer_id is None:
            raise DomainError(
                f"Invoice in status '{self.status.value}' must be linked to a Customer."
            )

    def publish_draft(self, customer_id: UUID) -> None:
        """Transition a DRAFT invoice to operational PENDING state after customer verification."""
        if self.status != InvoiceStatus.DRAFT:
            raise DomainError(
                f"Only DRAFT invoices can be published, current status is '{self.status.value}'."
            )
        self.customer_id = customer_id
        self.status = InvoiceStatus.PENDING
        self.updated_at = datetime.now(timezone.utc)
        self.validate_invariants()

    def record_payment(self, payment_amount: Money) -> None:
        """Apply a payment allocation to the invoice, updating balances and status deterministically."""
        if self.status not in (InvoiceStatus.PENDING, InvoiceStatus.PARTIALLY_PAID):
            raise DomainError(
                f"Cannot allocate payment to invoice in status '{self.status.value}'."
            )
        if not payment_amount.is_positive():
            raise ValidationError(
                f"Allocation amount must be positive, got {payment_amount}."
            )
        if payment_amount.currency != self.currency:
            raise DomainError(
                f"Currency mismatch: payment is in {payment_amount.currency}, invoice is in {self.currency}."
            )
        if payment_amount > self.outstanding_amount:
            raise DomainError(
                f"Over-allocation error: payment amount ({payment_amount}) exceeds "
                f"outstanding balance ({self.outstanding_amount})."
            )

        self.paid_amount = self.paid_amount + payment_amount
        self.outstanding_amount = self.total_amount - self.paid_amount

        if self.outstanding_amount.is_zero():
            self.status = InvoiceStatus.PAID
        else:
            self.status = InvoiceStatus.PARTIALLY_PAID

        self.updated_at = datetime.now(timezone.utc)
        self.validate_invariants()

    def cancel(self, reason: Optional[str] = None) -> None:
        """Cancel an invoice, verifying no payments have been allocated."""
        if self.status == InvoiceStatus.PAID:
            raise DomainError("Paid invoices cannot be cancelled.")
        if self.status == InvoiceStatus.CANCELLED:
            raise DomainError("Invoice is already cancelled.")
        if not self.paid_amount.is_zero():
            raise DomainError(
                f"Cannot cancel invoice with existing payments (paid: {self.paid_amount}). "
                "Unallocate or reverse payments first."
            )

        self.status = InvoiceStatus.CANCELLED
        if reason:
            self.notes = f"{self.notes} | Cancelled: {reason}" if self.notes else f"Cancelled: {reason}"
        self.updated_at = datetime.now(timezone.utc)

    def update_metadata(self, due_date: Optional[date] = None, notes: Optional[str] = None) -> None:
        """Update allowed operational metadata on an active invoice."""
        if self.status == InvoiceStatus.CANCELLED:
            raise DomainError("Cannot update cancelled invoices.")
        if due_date:
            if due_date < self.issue_date:
                raise ValidationError(
                    f"Due date ({due_date}) cannot be earlier than issue date ({self.issue_date})."
                )
            self.due_date = due_date
        if notes is not None:
            self.notes = notes
        self.updated_at = datetime.now(timezone.utc)

    def archive(self) -> None:
        """Archive invoice, preserving its historical state while hiding from operational views."""
        if self.status == InvoiceStatus.DRAFT:
            raise DomainError("Draft invoices cannot be archived. Please delete or confirm them.")
        self.is_archived = True
        self.updated_at = datetime.now(timezone.utc)

    def unarchive(self) -> None:
        """Restore an archived invoice back to operational views."""
        self.is_archived = False
        self.updated_at = datetime.now(timezone.utc)

    def belongs_to(self, company_id: UUID) -> bool:
        """Verify tenant ownership."""
        return self.company_id == company_id
