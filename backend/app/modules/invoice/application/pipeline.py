"""app/modules/invoice/application/pipeline.py

6-Stage Document Processing Pipeline for Invoice Document Ingestion.
Coordinates security intake validation, tenant-isolated storage persistence,
pluggable OCR extraction, normalization, and deterministic financial validation.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
from uuid import UUID
import uuid

from sqlalchemy.orm import Session

from app.config import settings
from app.modules.invoice.application.ports import (
    ExtractedInvoiceData,
    HeuristicInvoiceOCRProvider,
    InvoiceOCRProvider,
    LocalStorageService,
    StorageService,
)
from app.modules.invoice.domain.entities import InvoiceDocument, OCRStatus
from app.modules.invoice.domain.file_security import FileSecurityValidator
from app.modules.invoice.infrastructure.repositories import InvoiceDocumentRepository
from app.shared.exceptions import ValidationError


class InvoiceDocumentProcessingPipeline:
    """Orchestrates end-to-end processing of uploaded invoice documents."""

    def __init__(
        self,
        db: Session,
        storage_service: Optional[StorageService] = None,
        ocr_provider: Optional[InvoiceOCRProvider] = None,
    ) -> None:
        self.db = db
        self.document_repo = InvoiceDocumentRepository(db)
        self.storage = storage_service or LocalStorageService()
        self.ocr = ocr_provider or HeuristicInvoiceOCRProvider()

    def process(
        self,
        company_id: UUID,
        file_content: bytes,
        filename: str,
        mime_type: Optional[str] = None,
    ) -> Tuple[InvoiceDocument, ExtractedInvoiceData, Dict[str, Any]]:
        """Execute the 6-stage extraction and validation pipeline."""
        # ---------------------------------------------------------------------
        # Stage 1: Security & File Signature Intake Validation
        # ---------------------------------------------------------------------
        detected_mime, normalized_ext = FileSecurityValidator.validate_file_safety(
            content=file_content,
            filename=filename,
            max_size_bytes=settings.MAX_UPLOAD_SIZE_BYTES,
        )

        # ---------------------------------------------------------------------
        # Stage 2: Tenant Storage & Content Hashing
        # ---------------------------------------------------------------------
        storage_key, file_hash, file_size = self.storage.save_file(
            content=file_content,
            filename=filename,
            company_id=company_id,
        )

        # Check for existing document in tenant context
        existing_doc = self.document_repo.get_by_hash(file_hash, company_id)
        if existing_doc:
            # Document already processed; perform OCR on existing bytes or return
            extracted = self.ocr.extract_from_file(file_content, filename, detected_mime)
            return existing_doc, extracted, existing_doc.extracted_data or {}

        # Initialize document metadata record
        doc = InvoiceDocument(
            id=uuid.uuid4(),
            company_id=company_id,
            file_name=filename,
            storage_key=storage_key,
            file_size_bytes=file_size,
            mime_type=detected_mime,
            file_hash=file_hash,
            ocr_status=OCRStatus.PROCESSING,
        )
        saved_doc = self.document_repo.create(doc)

        # ---------------------------------------------------------------------
        # Stage 3 & 4: Raw Extraction & Normalization
        # ---------------------------------------------------------------------
        try:
            extracted_data = self.ocr.extract_from_file(file_content, filename, detected_mime)
            # -----------------------------------------------------------------
            # Stage 5: Deterministic Financial Validation Gate
            # -----------------------------------------------------------------
            if extracted_data.is_ambiguous or extracted_data.confidence < Decimal("0.85"):
                ocr_status = OCRStatus.VALIDATION_REQUIRED
            else:
                ocr_status = OCRStatus.VALIDATED
        except Exception as ex:
            extracted_data = ExtractedInvoiceData(
                confidence=Decimal("0.00"),
                is_ambiguous=True,
                extraction_notes=[f"OCR processing failed: {str(ex)}"],
            )
            ocr_status = OCRStatus.FAILED

        # ---------------------------------------------------------------------
        # Stage 6: Structured Result Assembly
        # ---------------------------------------------------------------------
        structured_payload: Dict[str, Any] = {
            "raw": {
                "text_snippet": extracted_data.raw_text[:500] if extracted_data.raw_text else None,
            },
            "normalized": {
                "invoice_number": extracted_data.invoice_number,
                "customer_name": extracted_data.customer_name,
                "tax_id": extracted_data.tax_id,
                "po_number": extracted_data.po_number,
                "payment_ref": extracted_data.payment_ref,
                "issue_date": extracted_data.issue_date.isoformat() if extracted_data.issue_date else None,
                "due_date": extracted_data.due_date.isoformat() if extracted_data.due_date else None,
                "total_amount": str(extracted_data.total_amount) if extracted_data.total_amount is not None else None,
                "subtotal_amount": str(extracted_data.subtotal_amount) if extracted_data.subtotal_amount is not None else None,
                "tax_amount": str(extracted_data.tax_amount) if extracted_data.tax_amount is not None else None,
                "currency": extracted_data.currency,
            },
            "validation": {
                "is_ambiguous": extracted_data.is_ambiguous,
                "notes": extracted_data.extraction_notes,
                "report": extracted_data.validation_report,
            },
            "confidence_metadata": {
                "overall_confidence": str(extracted_data.confidence),
                "field_confidence": {k: str(v) for k, v in extracted_data.field_confidence.items()},
                "provider_name": extracted_data.provider_name,
                "provider_version": extracted_data.provider_version,
                "latency_ms": extracted_data.latency_ms,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            },
        }

        # Update document record with final status and payload
        saved_doc.ocr_status = ocr_status
        saved_doc.extracted_data = structured_payload
        saved_doc.updated_at = datetime.now(timezone.utc)
        self.document_repo.update(saved_doc)

        return saved_doc, extracted_data, structured_payload
