"""Unit tests for Invoice Domain Entities, LocalStorageService, and HeuristicOCRProvider.

Tests invariants, state machine transitions, monetary precision, storage security,
path traversal safeguards, and OCR ambiguity detection.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import shutil
import tempfile
import uuid
import pytest
from app.modules.invoice.application.ports import (
    ExtractedInvoiceData,
    HeuristicInvoiceOCRProvider,
    LocalStorageService,
)
from app.modules.invoice.domain.entities import (
    Invoice,
    InvoiceDocument,
    InvoiceSource,
    InvoiceStatus,
    OCRStatus,
)
from app.shared.domain.money import Money
from app.shared.exceptions import (
    DomainError,
    NotFoundError,
    ValidationError,
)


def test_invoice_entity_initialization_valid() -> None:
    """Invoice entity initializes with matching balances and valid dates."""
    cid = uuid.uuid4()
    cust_id = uuid.uuid4()
    total = Money(Decimal("1000.00"), "INR")

    inv = Invoice(
        id=uuid.uuid4(),
        company_id=cid,
        customer_id=cust_id,
        invoice_number="INV-2026-001",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 30),
        total_amount=total,
        paid_amount=Money.zero("INR"),
        outstanding_amount=total,
        status=InvoiceStatus.PENDING,
    )

    assert inv.invoice_number == "INV-2026-001"
    assert inv.outstanding_amount == total
    assert inv.belongs_to(cid) is True
    assert inv.belongs_to(uuid.uuid4()) is False


def test_invoice_due_date_before_issue_date_rejected() -> None:
    """Invoice rejects due dates that are chronologically before issue dates."""
    total = Money(Decimal("500.00"), "INR")
    with pytest.raises(ValidationError) as exc_info:
        Invoice(
            id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            invoice_number="INV-INVALID-DATE",
            issue_date=date(2026, 9, 10),
            due_date=date(2026, 9, 5),  # 5 days before issue date
            total_amount=total,
            paid_amount=Money.zero("INR"),
            outstanding_amount=total,
            status=InvoiceStatus.PENDING,
        )
    assert "Due date" in str(exc_info.value)


def test_invoice_balance_conservation_invariant() -> None:
    """Invoice rejects balance discrepancies (paid + outstanding != total)."""
    total = Money(Decimal("1000.00"), "INR")
    with pytest.raises(DomainError) as exc_info:
        Invoice(
            id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            invoice_number="INV-CORRUPT-BALANCE",
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 9, 15),
            total_amount=total,
            paid_amount=Money.zero("INR"),
            outstanding_amount=Money(Decimal("800.00"), "INR"),  # Total 1000 != 0 + 800
            status=InvoiceStatus.PENDING,
        )
    assert "Balance conservation violated" in str(exc_info.value)


def test_invoice_draft_lifecycle_and_publish() -> None:
    """Draft invoice can exist without customer_id initially, but must have customer when published."""
    cid = uuid.uuid4()
    cust_id = uuid.uuid4()
    total = Money(Decimal("2500.00"), "INR")

    draft = Invoice(
        id=uuid.uuid4(),
        company_id=cid,
        customer_id=None,
        invoice_number="INV-DRAFT-01",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        total_amount=total,
        paid_amount=Money.zero("INR"),
        outstanding_amount=total,
        status=InvoiceStatus.DRAFT,
    )
    assert draft.status == InvoiceStatus.DRAFT
    assert draft.customer_id is None

    # Publishing transitions to PENDING
    draft.publish_draft(customer_id=cust_id)
    assert draft.status == InvoiceStatus.PENDING
    assert draft.customer_id == cust_id

    # Publishing an already pending invoice raises exception
    with pytest.raises(DomainError):
        draft.publish_draft(customer_id=cust_id)


def test_invoice_record_payment_deterministic_allocations() -> None:
    """Partial and complete payment allocations update balances and status deterministically."""
    total = Money(Decimal("1000.00"), "INR")
    inv = Invoice(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        invoice_number="INV-PAY-01",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        total_amount=total,
        paid_amount=Money.zero("INR"),
        outstanding_amount=total,
        status=InvoiceStatus.PENDING,
    )

    # Partial allocation
    inv.record_payment(Money(Decimal("400.00"), "INR"))
    assert inv.status == InvoiceStatus.PARTIALLY_PAID
    assert inv.paid_amount == Money(Decimal("400.00"), "INR")
    assert inv.outstanding_amount == Money(Decimal("600.00"), "INR")

    # Over-allocation error
    with pytest.raises(DomainError) as exc_info:
        inv.record_payment(Money(Decimal("700.00"), "INR"))  # Only 600 remaining
    assert "exceeds outstanding balance" in str(exc_info.value)

    # Complete allocation
    inv.record_payment(Money(Decimal("600.00"), "INR"))
    assert inv.status == InvoiceStatus.PAID
    assert inv.paid_amount == total
    assert inv.outstanding_amount.is_zero()

    # Attempting to allocate to already PAID invoice raises exception
    with pytest.raises(DomainError):
        inv.record_payment(Money(Decimal("100.00"), "INR"))


def test_invoice_currency_mismatch_rejected() -> None:
    """Payment with different currency cannot be allocated to invoice."""
    total = Money(Decimal("100.00"), "USD")
    inv = Invoice(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        invoice_number="INV-USD-01",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        total_amount=total,
        paid_amount=Money.zero("USD"),
        outstanding_amount=total,
        currency="USD",
        status=InvoiceStatus.PENDING,
    )

    with pytest.raises(DomainError) as exc_info:
        inv.record_payment(Money(Decimal("100.00"), "INR"))
    assert "Currency mismatch" in str(exc_info.value)


def test_invoice_cancellation_safeguards() -> None:
    """Invoice cannot be cancelled if payments have already been allocated."""
    total = Money(Decimal("1000.00"), "INR")
    inv = Invoice(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        invoice_number="INV-CANCEL-01",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        total_amount=total,
        paid_amount=Money.zero("INR"),
        outstanding_amount=total,
        status=InvoiceStatus.PENDING,
    )

    # Valid cancellation on unpaid invoice
    inv.cancel(reason="Customer requested order cancellation")
    assert inv.status == InvoiceStatus.CANCELLED
    assert "Customer requested order cancellation" in (inv.notes or "")

    # Cannot cancel already cancelled invoice
    with pytest.raises(DomainError):
        inv.cancel()

    # Cannot cancel invoice with payments
    partially_paid = Invoice(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        invoice_number="INV-PARTIAL-CANCEL",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        total_amount=total,
        paid_amount=Money(Decimal("200.00"), "INR"),
        outstanding_amount=Money(Decimal("800.00"), "INR"),
        status=InvoiceStatus.PARTIALLY_PAID,
    )
    with pytest.raises(DomainError) as exc_info:
        partially_paid.cancel()
    assert "Cannot cancel invoice with existing payments" in str(exc_info.value)


def test_local_storage_service_crud_and_security() -> None:
    """LocalStorageService saves files, isolates tenants, and prevents path traversal attacks."""
    temp_dir = tempfile.mkdtemp()
    try:
        storage = LocalStorageService(base_dir=temp_dir)
        cid1 = uuid.uuid4()
        cid2 = uuid.uuid4()
        sample_bytes = b"Sample Invoice PDF content for Acme Corp"

        # 1. Save file
        key, f_hash, size = storage.save_file(sample_bytes, "acme_invoice.pdf", cid1)
        assert size == len(sample_bytes)
        assert len(f_hash) == 64

        # 2. Retrieve file within same tenant
        retrieved = storage.get_file(key, cid1)
        assert retrieved == sample_bytes

        # 3. Tenant Isolation: Company 2 cannot get Company 1's file
        with pytest.raises(NotFoundError):
            storage.get_file(key, cid2)

        # 4. Path traversal prevention in filename
        key_traversal, _, _ = storage.save_file(b"test", "../../../etc/passwd", cid1)
        assert ".." not in key_traversal

        # 5. Delete file
        assert storage.delete_file(key, cid1) is True
        assert storage.delete_file("nonexistent_key", cid1) is False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_heuristic_ocr_provider_extraction() -> None:
    """HeuristicInvoiceOCRProvider extracts structured invoice fields and computes confidence."""
    provider = HeuristicInvoiceOCRProvider()
    content = (
        b"TAX INVOICE\n"
        b"Invoice Number: INV-2026-9901\n"
        b"Issue Date: 2026-09-01\n"
        b"Due Date: 2026-09-15\n"
        b"Bill To: Acme Industrial Supplies Ltd\n"
        b"GSTIN: 29ABCDE1234F1Z5\n"
        b"Total Amount: INR 45,000.00\n"
    )

    extracted = provider.extract_from_file(content, "sample_invoice.pdf", "application/pdf")
    assert extracted.invoice_number == "INV-2026-9901"
    assert extracted.customer_name == "Acme Industrial Supplies Ltd"
    assert extracted.tax_id == "29ABCDE1234F1Z5"
    assert extracted.issue_date == date(2026, 9, 1)
    assert extracted.due_date == date(2026, 9, 15)
    assert extracted.total_amount == Decimal("45000.00")
    assert extracted.currency == "INR"
    assert extracted.confidence >= Decimal("0.85")
    assert extracted.is_ambiguous is False


def test_heuristic_ocr_provider_ambiguity_flag() -> None:
    """OCR flags extraction as ambiguous when essential monetary fields are missing."""
    provider = HeuristicInvoiceOCRProvider()
    unclear_content = b"Draft memo without valid invoice number or amount"

    extracted = provider.extract_from_file(unclear_content, "draft.pdf", "application/pdf")
    assert extracted.invoice_number is None
    assert extracted.total_amount is None
    assert extracted.is_ambiguous is True
    assert extracted.confidence < Decimal("0.50")


def test_invoice_archive_and_unarchive_lifecycle() -> None:
    """Active invoice can be archived and restored; draft invoices cannot be archived."""
    total = Money(Decimal("1000.00"), "INR")
    inv = Invoice(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        invoice_number="INV-ARCHIVE-01",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        total_amount=total,
        paid_amount=Money.zero("INR"),
        outstanding_amount=total,
        status=InvoiceStatus.PENDING,
    )
    assert inv.is_archived is False

    # Archive
    inv.archive()
    assert inv.is_archived is True

    # Unarchive
    inv.unarchive()
    assert inv.is_archived is False

    # Draft invoice cannot be archived
    draft = Invoice(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        customer_id=None,
        invoice_number="INV-DRAFT-ARCHIVE",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        total_amount=total,
        paid_amount=Money.zero("INR"),
        outstanding_amount=total,
        status=InvoiceStatus.DRAFT,
    )
    with pytest.raises(DomainError) as exc_info:
        draft.archive()
    assert "Draft invoices cannot be archived" in str(exc_info.value)


def test_invoice_entity_invariants_negative_and_zero_values() -> None:
    """Invoice invariants reject zero/negative total, negative paid, and negative outstanding."""
    total = Money(Decimal("1000.00"), "INR")

    # Zero total
    with pytest.raises(ValidationError):
        Invoice(
            id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            invoice_number="INV-ZERO-TOT",
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 9, 15),
            total_amount=Money(Decimal("0.00"), "INR"),
            paid_amount=Money.zero("INR"),
            outstanding_amount=Money.zero("INR"),
            status=InvoiceStatus.PENDING,
        )

    # Negative paid amount
    with pytest.raises(ValidationError) as exc_paid:
        Invoice(
            id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            invoice_number="INV-NEG-PAID",
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 9, 15),
            total_amount=total,
            paid_amount=Money(Decimal("-10.00"), "INR"),
            outstanding_amount=Money(Decimal("1010.00"), "INR"),
            status=InvoiceStatus.PENDING,
        )
    assert "paid amount cannot be negative" in str(exc_paid.value)

    # Negative outstanding amount
    with pytest.raises(ValidationError) as exc_out:
        Invoice(
            id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            invoice_number="INV-NEG-OUT",
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 9, 15),
            total_amount=total,
            paid_amount=Money(Decimal("1010.00"), "INR"),
            outstanding_amount=Money(Decimal("-10.00"), "INR"),
            status=InvoiceStatus.PENDING,
        )
    assert "outstanding amount cannot be negative" in str(exc_out.value)

    # Record payment with non-positive amount
    valid_inv = Invoice(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        invoice_number="INV-REC-PAY-POS",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        total_amount=total,
        paid_amount=Money.zero("INR"),
        outstanding_amount=total,
        status=InvoiceStatus.PENDING,
    )
    with pytest.raises(ValidationError) as exc_rec_zero:
        valid_inv.record_payment(Money.zero("INR"))
    assert "Allocation amount must be positive" in str(exc_rec_zero.value)

    with pytest.raises(ValidationError) as exc_rec_neg:
        valid_inv.record_payment(Money(Decimal("-50.00"), "INR"))
    assert "Allocation amount must be positive" in str(exc_rec_neg.value)

    # Non-draft invoice without customer
    with pytest.raises(DomainError) as exc_info:
        Invoice(
            id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            customer_id=None,
            invoice_number="INV-NO-CUST",
            issue_date=date(2026, 9, 1),
            due_date=date(2026, 9, 15),
            total_amount=total,
            paid_amount=Money.zero("INR"),
            outstanding_amount=total,
            status=InvoiceStatus.PENDING,
        )
    assert "must be linked to a Customer" in str(exc_info.value)


def test_invoice_update_metadata_and_paid_cancellation_guards() -> None:
    """Paid invoices cannot be cancelled; cancelled invoices cannot be updated; invalid update dates rejected."""
    total = Money(Decimal("1000.00"), "INR")
    inv = Invoice(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        invoice_number="INV-UPDATE-01",
        issue_date=date(2026, 9, 10),
        due_date=date(2026, 9, 20),
        total_amount=total,
        paid_amount=Money.zero("INR"),
        outstanding_amount=total,
        status=InvoiceStatus.PENDING,
    )

    # Valid update
    inv.update_metadata(due_date=date(2026, 9, 25), notes="Updated note")
    assert inv.due_date == date(2026, 9, 25)
    assert inv.notes == "Updated note"

    # Reject due date < issue date
    with pytest.raises(ValidationError):
        inv.update_metadata(due_date=date(2026, 9, 5))

    # Cancel invoice
    inv.cancel()
    # Reject update on cancelled invoice
    with pytest.raises(DomainError):
        inv.update_metadata(notes="Attempted change")

    # Paid invoice cannot be cancelled
    paid_inv = Invoice(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        invoice_number="INV-PAID-01",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        total_amount=total,
        paid_amount=total,
        outstanding_amount=Money.zero("INR"),
        status=InvoiceStatus.PAID,
    )
    with pytest.raises(DomainError):
        paid_inv.cancel()


def test_invoice_document_entity_methods() -> None:
    """InvoiceDocument entity predicates and OCR status mutation methods work correctly."""
    cid = uuid.uuid4()
    doc = InvoiceDocument(
        id=uuid.uuid4(),
        company_id=cid,
        file_name="test.pdf",
        storage_key="test_key",
        file_size_bytes=1024,
        mime_type="application/pdf",
        file_hash="dummy_hash_123",
    )
    assert doc.belongs_to(cid) is True
    assert doc.belongs_to(uuid.uuid4()) is False
    assert doc.ocr_status == OCRStatus.PENDING

    doc.mark_ocr_completed({"invoice_number": "INV-100"})
    assert doc.ocr_status == OCRStatus.COMPLETED
    assert doc.extracted_data == {"invoice_number": "INV-100"}

    doc.mark_ocr_failed("Timeout parsing file")
    assert doc.ocr_status == OCRStatus.FAILED
    assert "Timeout parsing file" in str(doc.extracted_data)


def test_storage_max_file_size_and_ocr_currencies() -> None:
    """Storage rejects files exceeding max size; OCR handles USD, EUR and alternate date formats."""
    temp_dir = tempfile.mkdtemp()
    try:
        storage = LocalStorageService(base_dir=temp_dir)
        cid = uuid.uuid4()

        # Reject file > 10MB
        too_large = b"x" * (10 * 1024 * 1024 + 1)
        with pytest.raises(ValidationError) as exc_size:
            storage.save_file(too_large, "huge.pdf", cid)
        assert "exceeds maximum limit" in str(exc_size.value)

        # OCR with USD currency and DD-MM-YYYY dates
        ocr = HeuristicInvoiceOCRProvider()
        usd_text = (
            b"Invoice Number: INV-USD-001\n"
            b"Bill To: American Client Inc\n"
            b"Issue Date: 15-09-2026\n"
            b"Due Date: 30-09-2026\n"
            b"Grand Total: $ 4,500.00\n"
        )
        res_usd = ocr.extract_from_file(usd_text, "usd.pdf", "application/pdf")
        assert res_usd.invoice_number == "INV-USD-001"
        assert res_usd.currency == "USD"
        assert res_usd.issue_date == date(2026, 9, 15)
        assert res_usd.total_amount == Decimal("4500.00")

        # OCR with EUR currency
        eur_text = (
            b"Invoice Number: INV-EUR-002\n"
            b"Total Amount: EUR 2,750.50\n"
        )
        res_eur = ocr.extract_from_file(eur_text, "eur.pdf", "application/pdf")
        assert res_eur.currency == "EUR"
        assert res_eur.total_amount == Decimal("2750.50")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

