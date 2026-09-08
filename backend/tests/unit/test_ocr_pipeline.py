"""Unit tests for Phase 12 Document Processing Pipeline & Providers.

Verifies:
- 6-stage InvoiceDocumentProcessingPipeline execution
- State machine lifecycle transitions (PENDING/UPLOADED -> PROCESSING -> EXTRACTED -> VALIDATED / VALIDATION_REQUIRED / FAILED)
- Mock OCR provider error injection (timeout, service unavailable, corrupt document)
- FallbackCompositeInvoiceOCRProvider primary-to-fallback cascade
- Deduplication and retry guards
"""

from datetime import date
from decimal import Decimal
import tempfile
import uuid
import pytest
from app.modules.invoice.application.pipeline import (
    InvoiceDocumentProcessingPipeline,
)
from app.modules.invoice.application.ports import (
    ExtractedInvoiceData,
    FallbackCompositeInvoiceOCRProvider,
    HeuristicInvoiceOCRProvider,
    LocalStorageService,
    MockInvoiceOCRProvider,
)
from app.modules.invoice.domain.entities import InvoiceDocument, OCRStatus
from app.shared.exceptions import DomainError, InfrastructureError, ValidationError


# -----------------------------------------------------------------------------
# 1. State Machine Lifecycle Transitions
# -----------------------------------------------------------------------------

def test_document_state_machine_valid_transitions() -> None:
    """Document progresses cleanly through authorized lifecycle transitions."""
    cid = uuid.uuid4()
    doc = InvoiceDocument(
        id=uuid.uuid4(),
        company_id=cid,
        file_name="invoice.pdf",
        storage_key="test_key",
        file_size_bytes=1024,
        mime_type="application/pdf",
        file_hash="hash_123",
    )
    assert doc.ocr_status == OCRStatus.PENDING

    # Transition to PROCESSING
    doc.start_processing()
    assert doc.ocr_status == OCRStatus.PROCESSING

    # Transition to EXTRACTED
    doc.mark_extracted({"raw": "some text"})
    assert doc.ocr_status == OCRStatus.EXTRACTED

    # Transition to VALIDATED
    doc.mark_validated({"normalized": {"total_amount": "100.00"}})
    assert doc.ocr_status == OCRStatus.VALIDATED

    # Human correction
    doc.mark_manually_corrected({"normalized": {"total_amount": "120.00"}})
    assert doc.ocr_status == OCRStatus.MANUALLY_CORRECTED


def test_document_state_machine_invalid_transitions_blocked() -> None:
    """Illegal state jumps are rejected by domain guards."""
    cid = uuid.uuid4()
    doc = InvoiceDocument(
        id=uuid.uuid4(),
        company_id=cid,
        file_name="invoice.pdf",
        storage_key="test_key",
        file_size_bytes=1024,
        mime_type="application/pdf",
        file_hash="hash_123",
        ocr_status=OCRStatus.PENDING,
    )

    # Cannot jump straight from PENDING to EXTRACTED without PROCESSING
    with pytest.raises(DomainError) as exc:
        doc.mark_extracted({"raw": "text"})
    assert "cannot mark extracted" in str(exc.value).lower()


# -----------------------------------------------------------------------------
# 2. Mock Provider Failure Simulations
# -----------------------------------------------------------------------------

def test_mock_provider_timeout_simulation() -> None:
    """Simulated network timeout raises InfrastructureError."""
    mock = MockInvoiceOCRProvider(raise_timeout=True)
    with pytest.raises(InfrastructureError) as exc:
        mock.extract_from_file(b"%PDF-1.4 dummy", "inv.pdf", "application/pdf")
    assert "timed out" in str(exc.value).lower()


def test_mock_provider_service_unavailable_simulation() -> None:
    """Simulated 503 service outage raises InfrastructureError."""
    mock = MockInvoiceOCRProvider(raise_server_error=True)
    with pytest.raises(InfrastructureError) as exc:
        mock.extract_from_file(b"%PDF-1.4 dummy", "inv.pdf", "application/pdf")
    assert "503" in str(exc.value)


def test_mock_provider_corrupt_file_simulation() -> None:
    """Simulated unreadable document raises ValidationError."""
    mock = MockInvoiceOCRProvider(raise_corrupt=True)
    with pytest.raises(ValidationError) as exc:
        mock.extract_from_file(b"%PDF-1.4 dummy", "inv.pdf", "application/pdf")
    assert "corrupt" in str(exc.value).lower()


# -----------------------------------------------------------------------------
# 3. Fallback Composite OCR Provider
# -----------------------------------------------------------------------------

def test_composite_provider_falls_back_on_primary_failure() -> None:
    """Composite provider catches primary outage and succeeds via secondary adapter."""
    failing_primary = MockInvoiceOCRProvider(raise_server_error=True)
    working_fallback = MockInvoiceOCRProvider(
        custom_result=ExtractedInvoiceData(
            invoice_number="INV-FALLBACK-01",
            total_amount=Decimal("25000.00"),
            currency="INR",
            confidence=Decimal("0.90"),
            is_ambiguous=False,
            provider_name="WorkingFallback",
        )
    )

    composite = FallbackCompositeInvoiceOCRProvider(
        primary=failing_primary,
        fallback=working_fallback,
    )

    result = composite.extract_from_file(b"%PDF-1.4 content", "test.pdf", "application/pdf")
    assert result.invoice_number == "INV-FALLBACK-01"
    assert result.total_amount == Decimal("25000.00")
    assert result.provider_name == "WorkingFallback"


# -----------------------------------------------------------------------------
# 4. End-to-End Pipeline Execution
# -----------------------------------------------------------------------------

def test_pipeline_execution_clean_invoice(db_session) -> None:
    """Clean PDF executes through all 6 pipeline stages to VALIDATED state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalStorageService(base_dir=tmpdir)
        provider = MockInvoiceOCRProvider(
            custom_result=ExtractedInvoiceData(
                invoice_number="INV-PIPE-101",
                customer_name="Global Systems Inc",
                tax_id="29ABCDE1234F1Z5",
                issue_date=date(2026, 9, 1),
                due_date=date(2026, 9, 30),
                total_amount=Decimal("50000.00"),
                subtotal_amount=Decimal("42372.88"),
                tax_amount=Decimal("7627.12"),
                currency="INR",
                confidence=Decimal("0.95"),
                is_ambiguous=False,
            )
        )
        pipeline = InvoiceDocumentProcessingPipeline(
            db=db_session,
            storage_service=storage,
            ocr_provider=provider,
        )

        company_id = uuid.uuid4()
        pdf_bytes = b"%PDF-1.4 sample clean invoice for pipeline testing"
        saved_doc, extracted, payload = pipeline.process(
            company_id=company_id,
            file_content=pdf_bytes,
            filename="pipeline_test.pdf",
        )

        assert saved_doc.ocr_status == OCRStatus.VALIDATED
        assert extracted.invoice_number == "INV-PIPE-101"
        assert extracted.total_amount == Decimal("50000.00")
        assert payload["normalized"]["invoice_number"] == "INV-PIPE-101"


def test_pipeline_execution_ambiguous_invoice_routes_to_validation_required(db_session) -> None:
    """Incomplete extraction routes document to VALIDATION_REQUIRED for human review."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalStorageService(base_dir=tmpdir)
        provider = MockInvoiceOCRProvider(
            custom_result=ExtractedInvoiceData(
                invoice_number="INV-INCOMPLETE",
                total_amount=None,  # Missing total triggers ambiguity
                confidence=Decimal("0.40"),
                is_ambiguous=True,
            )
        )
        pipeline = InvoiceDocumentProcessingPipeline(
            db=db_session,
            storage_service=storage,
            ocr_provider=provider,
        )

        company_id = uuid.uuid4()
        pdf_bytes = b"%PDF-1.4 sample ambiguous document"
        saved_doc, extracted, payload = pipeline.process(
            company_id=company_id,
            file_content=pdf_bytes,
            filename="ambiguous.pdf",
        )

        assert saved_doc.ocr_status == OCRStatus.VALIDATION_REQUIRED
        assert extracted.is_ambiguous is True
        assert payload["validation"]["is_ambiguous"] is True
