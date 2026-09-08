"""Invoice Application Use Cases.

Implements all application workflows: manual invoice entry, PDF upload and OCR extraction,
draft confirmation, bulk CSV import with row-level validation, detail retrieval,
search/filtering, lifecycle cancellation, and secure document streaming.
"""

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import io
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import uuid
from uuid import UUID
from sqlalchemy.orm import Session
from app.config import settings
from app.modules.customer.domain.entities import Customer
from app.modules.customer.infrastructure.repositories import CustomerRepository
from app.modules.invoice.application.ports import (
    ExtractedInvoiceData,
    HeuristicInvoiceOCRProvider,
    InvoiceOCRProvider,
    LocalStorageService,
    StorageService,
)
from app.modules.invoice.domain.entities import (
    Invoice,
    InvoiceDocument,
    InvoiceSource,
    InvoiceStatus,
    OCRStatus,
)
from app.modules.invoice.domain.file_security import FileSecurityValidator
from app.modules.invoice.infrastructure.repositories import (
    InvoiceDocumentRepository,
    InvoiceRepository,
)
from app.shared.domain.money import Money
from app.shared.exceptions import (
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)


@dataclass
class InvoiceDetailDTO:
    """Aggregated DTO for invoice detail view."""
    invoice: Invoice
    customer: Optional[Customer] = None
    document: Optional[InvoiceDocument] = None


@dataclass
class CSVRowError:
    """Detailed validation error for a single CSV import row."""
    row_number: int
    invoice_number: Optional[str]
    error_message: str


@dataclass
class CSVImportResult:
    """Summary and granular errors for a batch CSV import."""
    total_rows: int
    imported_count: int
    failed_count: int
    errors: List[CSVRowError]
    imported_invoices: List[Invoice]


class CreateInvoiceUseCase:
    """Handles manual invoice entry with tenant scoping, customer linking, and duplicate checks."""

    def __init__(self, db: Session) -> None:
        self.invoice_repo = InvoiceRepository(db)
        self.customer_repo = CustomerRepository(db)
        self.document_repo = InvoiceDocumentRepository(db)

    def execute(
        self,
        company_id: UUID,
        invoice_number: str,
        customer_id: UUID,
        issue_date: date,
        due_date: date,
        total_amount: Decimal,
        currency: str = "INR",
        tax_amount: Optional[Decimal] = None,
        notes: Optional[str] = None,
        document_id: Optional[UUID] = None,
    ) -> Invoice:
        """Create and persist a new verified PENDING invoice."""
        clean_number = invoice_number.strip()
        if not clean_number:
            raise ValidationError("Invoice number cannot be empty.")

        # 1. Customer Verification
        customer = self.customer_repo.get_by_id(customer_id, company_id)
        if not customer:
            raise NotFoundError("Customer", str(customer_id))
        if customer.is_archived:
            raise ValidationError(f"Customer '{customer.name}' is archived. Cannot create invoice.")

        # 2. Duplicate Detection
        existing = self.invoice_repo.get_by_number(clean_number, company_id)
        if existing:
            raise ConflictError(
                f"Invoice number '{clean_number}' already exists for this company."
            )

        # 3. Document Attachment Verification
        if document_id:
            doc = self.document_repo.get_by_id(document_id, company_id)
            if not doc:
                raise NotFoundError("InvoiceDocument", str(document_id))

        # 4. Invariant Checks & Domain Construction
        curr = currency.strip().upper() or "INR"
        tot = Money(total_amount, currency=curr)
        tax = Money(tax_amount, currency=curr) if tax_amount is not None else None

        invoice = Invoice(
            id=uuid.uuid4(),
            company_id=company_id,
            customer_id=customer_id,
            document_id=document_id,
            invoice_number=clean_number,
            issue_date=issue_date,
            due_date=due_date,
            total_amount=tot,
            paid_amount=Money.zero(curr),
            outstanding_amount=tot,
            tax_amount=tax,
            currency=curr,
            status=InvoiceStatus.PENDING,
            source=InvoiceSource.MANUAL,
            notes=notes,
        )

        created = self.invoice_repo.create(invoice)
        if document_id:
            self.document_repo.link_to_invoice(document_id, created.id, company_id)

        return created


class UploadInvoiceDocumentUseCase:
    """Handles PDF / document upload, storage persistence, and OCR extraction."""

    def __init__(
        self,
        db: Session,
        storage_service: Optional[StorageService] = None,
        ocr_provider: Optional[InvoiceOCRProvider] = None,
    ) -> None:
        self.invoice_repo = InvoiceRepository(db)
        self.document_repo = InvoiceDocumentRepository(db)
        self.customer_repo = CustomerRepository(db)
        self.storage = storage_service or LocalStorageService()
        self.ocr = ocr_provider or HeuristicInvoiceOCRProvider()

    def execute(
        self,
        company_id: UUID,
        file_content: bytes,
        filename: str,
        mime_type: str,
        auto_create_draft: bool = True,
    ) -> Tuple[InvoiceDocument, ExtractedInvoiceData, Optional[Invoice]]:
        """Upload source invoice, execute extraction, and generate draft invoice."""
        # 1. Validation of magic bytes, extension and size
        detected_mime, _ = FileSecurityValidator.validate_file_safety(
            content=file_content,
            filename=filename,
            max_size_bytes=settings.MAX_UPLOAD_SIZE_BYTES,
        )

        # 2. Persist to storage
        storage_key, file_hash, file_size = self.storage.save_file(file_content, filename, company_id)

        # Check existing document hash for duplicate upload
        existing_doc = self.document_repo.get_by_hash(file_hash, company_id)
        if existing_doc:
            # Document already processed; perform OCR on existing or return
            extracted = self.ocr.extract_from_file(file_content, filename, detected_mime)
            existing_inv = (
                self.invoice_repo.get_by_id(existing_doc.invoice_id, company_id)
                if existing_doc.invoice_id
                else None
            )
            return existing_doc, extracted, existing_inv

        # 3. Perform OCR Extraction
        try:
            extracted_data = self.ocr.extract_from_file(file_content, filename, detected_mime)
            if extracted_data.is_ambiguous or extracted_data.confidence < Decimal("0.85"):
                ocr_status = OCRStatus.VALIDATION_REQUIRED
            else:
                ocr_status = OCRStatus.COMPLETED
        except Exception as ex:
            extracted_data = ExtractedInvoiceData(
                confidence=Decimal("0.00"),
                is_ambiguous=True,
                extraction_notes=[f"Extraction failed: {str(ex)}"],
            )
            ocr_status = OCRStatus.FAILED

        # 4. Create Document Metadata Record
        doc = InvoiceDocument(
            id=uuid.uuid4(),
            company_id=company_id,
            file_name=filename,
            storage_key=storage_key,
            file_size_bytes=file_size,
            mime_type=mime_type,
            file_hash=file_hash,
            ocr_status=ocr_status,
            extracted_data=extracted_data.model_dump(mode="json"),
        )
        saved_doc = self.document_repo.create(doc)

        draft_invoice: Optional[Invoice] = None
        # 5. Optionally create DRAFT invoice if minimal fields extracted and auto_create_draft is True
        if auto_create_draft and extracted_data.invoice_number and extracted_data.total_amount:
            # Verify no existing invoice with this number
            existing_num = self.invoice_repo.get_by_number(extracted_data.invoice_number, company_id)
            if not existing_num:
                # Attempt customer resolution via tax_id or name
                resolved_customer_id: Optional[UUID] = None
                if extracted_data.tax_id:
                    matched_custs, _ = self.customer_repo.search_customers(
                        company_id=company_id, query_str=extracted_data.tax_id, limit=2
                    )
                    if len(matched_custs) == 1:
                        resolved_customer_id = matched_custs[0].id
                elif extracted_data.customer_name:
                    matched_custs, _ = self.customer_repo.search_customers(
                        company_id=company_id, query_str=extracted_data.customer_name, limit=2
                    )
                    if len(matched_custs) == 1:
                        resolved_customer_id = matched_custs[0].id

                issue = extracted_data.issue_date or date.today()
                due = extracted_data.due_date or issue
                if due < issue:
                    due = issue

                tot = Money(extracted_data.total_amount, currency=extracted_data.currency)

                draft = Invoice(
                    id=uuid.uuid4(),
                    company_id=company_id,
                    customer_id=resolved_customer_id,
                    document_id=saved_doc.id,
                    invoice_number=extracted_data.invoice_number,
                    issue_date=issue,
                    due_date=due,
                    total_amount=tot,
                    paid_amount=Money.zero(extracted_data.currency),
                    outstanding_amount=tot,
                    tax_amount=None,
                    currency=extracted_data.currency,
                    status=InvoiceStatus.DRAFT,
                    source=InvoiceSource.PDF_UPLOAD,
                    notes=f"Auto-extracted with confidence {extracted_data.confidence * 100:.0f}%",
                )
                draft_invoice = self.invoice_repo.create(draft)
                self.document_repo.link_to_invoice(saved_doc.id, draft_invoice.id, company_id)

        return saved_doc, extracted_data, draft_invoice


class ConfirmDraftInvoiceUseCase:
    """Accountant review workflow: confirms and promotes a DRAFT invoice to active PENDING state."""

    def __init__(self, db: Session) -> None:
        self.invoice_repo = InvoiceRepository(db)
        self.customer_repo = CustomerRepository(db)

    def execute(
        self,
        company_id: UUID,
        invoice_id: UUID,
        customer_id: UUID,
        invoice_number: Optional[str] = None,
        issue_date: Optional[date] = None,
        due_date: Optional[date] = None,
        total_amount: Optional[Decimal] = None,
        tax_amount: Optional[Decimal] = None,
        currency: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Invoice:
        """Validate verified draft fields and publish to PENDING status."""
        invoice = self.invoice_repo.get_by_id(invoice_id, company_id)
        if not invoice:
            raise NotFoundError("Invoice", str(invoice_id))

        if invoice.status != InvoiceStatus.DRAFT:
            raise DomainError(
                f"Invoice is in status '{invoice.status.value}', cannot confirm non-draft invoice."
            )

        customer = self.customer_repo.get_by_id(customer_id, company_id)
        if not customer:
            raise NotFoundError("Customer", str(customer_id))
        if customer.is_archived:
            raise ValidationError(f"Customer '{customer.name}' is archived.")

        # If invoice number changed, check duplicate
        if invoice_number and invoice_number.strip().lower() != invoice.invoice_number.lower():
            existing = self.invoice_repo.get_by_number(invoice_number.strip(), company_id)
            if existing:
                raise ConflictError(
                    f"Invoice number '{invoice_number.strip()}' already exists."
                )
            invoice.invoice_number = invoice_number.strip()

        if currency:
            new_curr = currency.strip().upper()
            if new_curr != invoice.currency:
                invoice.currency = new_curr
                invoice.total_amount = Money(invoice.total_amount.amount, currency=new_curr)
                invoice.paid_amount = Money(invoice.paid_amount.amount, currency=new_curr)
                invoice.outstanding_amount = Money(invoice.outstanding_amount.amount, currency=new_curr)
                if invoice.tax_amount:
                    invoice.tax_amount = Money(invoice.tax_amount.amount, currency=new_curr)

        if issue_date:
            invoice.issue_date = issue_date
        if due_date:
            invoice.due_date = due_date

        if total_amount is not None:
            tot = Money(total_amount, currency=invoice.currency)
            invoice.total_amount = tot
            invoice.outstanding_amount = tot
            invoice.paid_amount = Money.zero(invoice.currency)

        if tax_amount is not None:
            invoice.tax_amount = Money(tax_amount, currency=invoice.currency)

        if notes is not None:
            invoice.notes = notes

        # Publish draft to PENDING and validate all invariants
        invoice.publish_draft(customer_id)
        updated_inv = self.invoice_repo.update(invoice)
        if invoice.document_id:
            doc_repo = InvoiceDocumentRepository(self.invoice_repo.db)
            doc_repo.update_ocr_status(
                doc_id=invoice.document_id,
                company_id=company_id,
                ocr_status=OCRStatus.MANUALLY_CORRECTED,
            )
        return updated_inv


class ImportInvoicesCSVUseCase:
    """Parses and ingests invoices from CSV with row-level validation and batch reporting."""

    def __init__(self, db: Session) -> None:
        self.invoice_repo = InvoiceRepository(db)
        self.customer_repo = CustomerRepository(db)

    def execute(self, company_id: UUID, csv_text: str) -> CSVImportResult:
        """Process CSV batch, validate row-by-row, and return granular results."""
        if not csv_text or not csv_text.strip():
            raise ValidationError("CSV payload is empty.")

        f = io.StringIO(csv_text.strip())
        reader = csv.DictReader(f)

        if not reader.fieldnames:
            raise ValidationError("CSV contains no header row.")

        # Normalize fieldnames to lowercase
        headers = [h.strip().lower() for h in reader.fieldnames]
        required_headers = {"invoice_number", "issue_date", "due_date", "total_amount"}
        if not required_headers.issubset(set(headers)):
            missing = required_headers - set(headers)
            raise ValidationError(f"CSV missing mandatory columns: {list(missing)}")

        if "customer_id" not in headers and "customer_name" not in headers and "customer_tax_id" not in headers:
            raise ValidationError("CSV must include at least one of: 'customer_id', 'customer_name', 'customer_tax_id'.")

        imported_invoices: List[Invoice] = []
        errors: List[CSVRowError] = []
        seen_in_batch: set = set()
        row_idx = 1  # header is row 1

        for row in reader:
            row_idx += 1
            # Clean keys to lowercase
            clean_row = {k.strip().lower(): (v.strip() if v else "") for k, v in row.items() if k}
            inv_num = clean_row.get("invoice_number", "")

            if not inv_num:
                errors.append(CSVRowError(row_idx, None, "Missing invoice_number."))
                continue

            # Check intra-batch duplicate
            norm_num = inv_num.lower()
            if norm_num in seen_in_batch:
                errors.append(CSVRowError(row_idx, inv_num, f"Duplicate invoice number '{inv_num}' within CSV batch."))
                continue

            # Check database duplicate
            if self.invoice_repo.get_by_number(inv_num, company_id):
                errors.append(CSVRowError(row_idx, inv_num, f"Invoice number '{inv_num}' already exists in database."))
                continue

            # Resolve Customer
            cust_id_str = clean_row.get("customer_id", "")
            cust_name_str = clean_row.get("customer_name", "")
            cust_tax_str = clean_row.get("customer_tax_id", "")

            customer: Optional[Customer] = None
            if cust_id_str:
                try:
                    c_uuid = UUID(cust_id_str)
                    customer = self.customer_repo.get_by_id(c_uuid, company_id)
                except ValueError:
                    errors.append(CSVRowError(row_idx, inv_num, f"Invalid customer_id UUID format: '{cust_id_str}'."))
                    continue
            elif cust_name_str:
                customer = self.customer_repo.get_by_name(cust_name_str, company_id)
                if not customer:
                    matches, _ = self.customer_repo.search_customers(company_id, cust_name_str, limit=2)
                    if len(matches) == 1:
                        customer = matches[0]
            elif cust_tax_str:
                matches, _ = self.customer_repo.search_customers(company_id, cust_tax_str, limit=2)
                if len(matches) == 1:
                    customer = matches[0]

            if not customer:
                errors.append(
                    CSVRowError(row_idx, inv_num, f"Customer not found for reference: '{cust_id_str or cust_name_str or cust_tax_str}'.")
                )
                continue

            if customer.is_archived:
                errors.append(CSVRowError(row_idx, inv_num, f"Customer '{customer.name}' is archived."))
                continue

            # Parse Dates
            issue_str = clean_row.get("issue_date", "")
            due_str = clean_row.get("due_date", "")
            try:
                issue_date = date.fromisoformat(issue_str.replace("/", "-"))
            except ValueError:
                errors.append(CSVRowError(row_idx, inv_num, f"Invalid issue_date '{issue_str}', expected YYYY-MM-DD."))
                continue

            try:
                due_date = date.fromisoformat(due_str.replace("/", "-"))
            except ValueError:
                errors.append(CSVRowError(row_idx, inv_num, f"Invalid due_date '{due_str}', expected YYYY-MM-DD."))
                continue

            if due_date < issue_date:
                errors.append(CSVRowError(row_idx, inv_num, f"Due date ({due_date}) earlier than issue date ({issue_date})."))
                continue

            # Parse Amount
            amt_str = clean_row.get("total_amount", "").replace(",", "")
            try:
                tot_dec = Decimal(amt_str)
                if tot_dec <= 0:
                    raise ValueError()
            except Exception:
                errors.append(CSVRowError(row_idx, inv_num, f"Invalid total_amount '{amt_str}', must be positive number."))
                continue

            # Optional tax amount & currency
            curr = clean_row.get("currency", "INR").upper() or "INR"
            tax_str = clean_row.get("tax_amount", "").replace(",", "")
            tax_dec: Optional[Decimal] = None
            if tax_str:
                try:
                    tax_dec = Decimal(tax_str)
                except Exception:
                    tax_dec = None

            notes = clean_row.get("notes") or None

            # Construct and persist Invoice
            tot_money = Money(tot_dec, currency=curr)
            tax_money = Money(tax_dec, currency=curr) if tax_dec is not None else None

            inv = Invoice(
                id=uuid.uuid4(),
                company_id=company_id,
                customer_id=customer.id,
                invoice_number=inv_num,
                issue_date=issue_date,
                due_date=due_date,
                total_amount=tot_money,
                paid_amount=Money.zero(curr),
                outstanding_amount=tot_money,
                tax_amount=tax_money,
                currency=curr,
                status=InvoiceStatus.PENDING,
                source=InvoiceSource.CSV_IMPORT,
                notes=notes,
            )

            try:
                created_inv = self.invoice_repo.create(inv)
                seen_in_batch.add(norm_num)
                imported_invoices.append(created_inv)
            except Exception as ex:
                errors.append(CSVRowError(row_idx, inv_num, f"Database error importing invoice: {str(ex)}"))

        return CSVImportResult(
            total_rows=row_idx - 1,
            imported_count=len(imported_invoices),
            failed_count=len(errors),
            errors=errors,
            imported_invoices=imported_invoices,
        )


class GetInvoiceDetailUseCase:
    """Retrieves full invoice details with eager customer and document metadata."""

    def __init__(self, db: Session) -> None:
        self.invoice_repo = InvoiceRepository(db)
        self.customer_repo = CustomerRepository(db)
        self.document_repo = InvoiceDocumentRepository(db)

    def execute(self, invoice_id: UUID, company_id: UUID) -> InvoiceDetailDTO:
        """Fetch invoice detail with joined entities."""
        invoice = self.invoice_repo.get_by_id(invoice_id, company_id)
        if not invoice:
            raise NotFoundError("Invoice", str(invoice_id))

        customer: Optional[Customer] = None
        if invoice.customer_id:
            customer = self.customer_repo.get_by_id(invoice.customer_id, company_id)

        document: Optional[InvoiceDocument] = None
        if invoice.document_id:
            document = self.document_repo.get_by_id(invoice.document_id, company_id)

        return InvoiceDetailDTO(invoice=invoice, customer=customer, document=document)


class ListInvoicesUseCase:
    """Lists invoices with operational filtering and pagination."""

    def __init__(self, db: Session) -> None:
        self.repo = InvoiceRepository(db)

    def execute(
        self,
        company_id: UUID,
        customer_id: Optional[UUID] = None,
        status: Optional[InvoiceStatus] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        due_date_from: Optional[date] = None,
        due_date_to: Optional[date] = None,
        has_outstanding: Optional[bool] = None,
        is_archived: Optional[bool] = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Invoice], int]:
        """Execute filtered invoice query."""
        return self.repo.list_invoices(
            company_id=company_id,
            customer_id=customer_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
            due_date_from=due_date_from,
            due_date_to=due_date_to,
            has_outstanding=has_outstanding,
            is_archived=is_archived,
            limit=limit,
            offset=offset,
        )


class SearchInvoicesUseCase:
    """Searches invoices by invoice number or customer name/tax ID."""

    def __init__(self, db: Session) -> None:
        self.repo = InvoiceRepository(db)

    def execute(
        self,
        company_id: UUID,
        query_str: str,
        is_archived: Optional[bool] = False,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Invoice], int]:
        """Execute search."""
        return self.repo.search_invoices(
            company_id=company_id,
            query_str=query_str,
            is_archived=is_archived,
            limit=limit,
            offset=offset,
        )


class UpdateInvoiceUseCase:
    """Updates allowed operational metadata on an active invoice."""

    def __init__(self, db: Session) -> None:
        self.repo = InvoiceRepository(db)

    def execute(
        self,
        invoice_id: UUID,
        company_id: UUID,
        due_date: Optional[date] = None,
        notes: Optional[str] = None,
    ) -> Invoice:
        """Update metadata on an existing invoice."""
        invoice = self.repo.get_by_id(invoice_id, company_id)
        if not invoice:
            raise NotFoundError("Invoice", str(invoice_id))

        invoice.update_metadata(due_date=due_date, notes=notes)
        return self.repo.update(invoice)


class CancelInvoiceUseCase:
    """Safely cancels an invoice if no payment allocations exist."""

    def __init__(self, db: Session) -> None:
        self.repo = InvoiceRepository(db)

    def execute(self, invoice_id: UUID, company_id: UUID, reason: Optional[str] = None) -> Invoice:
        """Cancel the invoice verifying financial prerequisites."""
        invoice = self.repo.get_by_id(invoice_id, company_id)
        if not invoice:
            raise NotFoundError("Invoice", str(invoice_id))

        invoice.cancel(reason=reason)
        return self.repo.update(invoice)


class DeleteInvoiceUseCase:
    """Safely deletes an invoice if in DRAFT or unallocated PENDING state."""

    def __init__(self, db: Session) -> None:
        self.repo = InvoiceRepository(db)

    def execute(self, invoice_id: UUID, company_id: UUID) -> bool:
        """Delete invoice enforcing financial deletion guards."""
        invoice = self.repo.get_by_id(invoice_id, company_id)
        if not invoice:
            raise NotFoundError("Invoice", str(invoice_id))

        if not invoice.paid_amount.is_zero():
            raise DomainError(
                f"Cannot delete invoice with payments allocated (paid: {invoice.paid_amount})."
            )

        if invoice.status not in (InvoiceStatus.DRAFT, InvoiceStatus.PENDING, InvoiceStatus.CANCELLED):
            raise DomainError(
                f"Cannot delete invoice in status '{invoice.status.value}'."
            )

        return self.repo.delete(invoice_id, company_id)


class GetInvoiceDocumentFileUseCase:
    """Fetches original document binary bytes with strict tenant isolation."""

    def __init__(self, db: Session, storage_service: Optional[StorageService] = None) -> None:
        self.invoice_repo = InvoiceRepository(db)
        self.document_repo = InvoiceDocumentRepository(db)
        self.storage = storage_service or LocalStorageService()

    def execute(self, invoice_id: UUID, company_id: UUID) -> Tuple[bytes, str, str]:
        """Return (file_bytes, filename, mime_type)."""
        invoice = self.invoice_repo.get_by_id(invoice_id, company_id)
        if not invoice:
            raise NotFoundError("Invoice", str(invoice_id))

        if not invoice.document_id:
            raise NotFoundError("InvoiceDocument", f"No document attached to invoice {invoice_id}")

        doc = self.document_repo.get_by_id(invoice.document_id, company_id)
        if not doc:
            raise NotFoundError("InvoiceDocument", str(invoice.document_id))

        content = self.storage.get_file(doc.storage_key, company_id)
        return content, doc.file_name, doc.mime_type


class ArchiveInvoiceUseCase:
    """Archives an active or completed invoice, hiding it from default operational listings."""

    def __init__(self, db: Session) -> None:
        self.repo = InvoiceRepository(db)

    def execute(self, invoice_id: UUID, company_id: UUID) -> Invoice:
        """Archive the invoice."""
        invoice = self.repo.get_by_id(invoice_id, company_id)
        if not invoice:
            raise NotFoundError("Invoice", str(invoice_id))

        invoice.archive()
        return self.repo.update(invoice)


class UnarchiveInvoiceUseCase:
    """Restores an archived invoice back into active operational listings."""

    def __init__(self, db: Session) -> None:
        self.repo = InvoiceRepository(db)

    def execute(self, invoice_id: UUID, company_id: UUID) -> Invoice:
        """Unarchive the invoice."""
        invoice = self.repo.get_by_id(invoice_id, company_id)
        if not invoice:
            raise NotFoundError("Invoice", str(invoice_id))

        invoice.unarchive()
        return self.repo.update(invoice)


class GetInvoiceDocumentUseCase:
    """Fetch document metadata, extraction details, and optional linked invoice."""

    def __init__(self, db: Session) -> None:
        self.doc_repo = InvoiceDocumentRepository(db)
        self.inv_repo = InvoiceRepository(db)

    def execute(self, doc_id: UUID, company_id: UUID) -> Tuple[InvoiceDocument, Optional[Invoice]]:
        doc = self.doc_repo.get_by_id(doc_id, company_id)
        if not doc:
            raise NotFoundError("InvoiceDocument", str(doc_id))
        linked_invoice = (
            self.inv_repo.get_by_id(doc.invoice_id, company_id)
            if doc.invoice_id
            else None
        )
        return doc, linked_invoice


class ListInvoiceDocumentsUseCase:
    """List paginated uploaded invoice documents with filters."""

    def __init__(self, db: Session) -> None:
        self.doc_repo = InvoiceDocumentRepository(db)
        self.inv_repo = InvoiceRepository(db)

    def execute(
        self,
        company_id: UUID,
        ocr_status: Optional[str] = None,
        has_invoice: Optional[bool] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        docs, total = self.doc_repo.list_documents(
            company_id=company_id,
            ocr_status=ocr_status,
            has_invoice=has_invoice,
            limit=limit,
            offset=offset,
        )
        items = []
        for d in docs:
            inv = self.inv_repo.get_by_id(d.invoice_id, company_id) if d.invoice_id else None
            items.append({
                "document": d,
                "invoice": inv,
            })
        return items, total


class GetInvoiceDocumentFileStreamUseCase:
    """Fetch original source document file bytes directly by document ID with tenant validation."""

    def __init__(self, db: Session, storage_service: Optional[StorageService] = None) -> None:
        self.document_repo = InvoiceDocumentRepository(db)
        self.storage = storage_service or LocalStorageService()

    def execute(self, doc_id: UUID, company_id: UUID) -> Tuple[bytes, str, str]:
        doc = self.document_repo.get_by_id(doc_id, company_id)
        if not doc:
            raise NotFoundError("InvoiceDocument", str(doc_id))
        content = self.storage.get_file(doc.storage_key, company_id)
        return content, doc.file_name, doc.mime_type


class RetryInvoiceDocumentProcessingUseCase:
    """Re-execute OCR extraction on an existing stored document without creating duplicate records."""

    def __init__(
        self,
        db: Session,
        storage_service: Optional[StorageService] = None,
        ocr_provider: Optional[InvoiceOCRProvider] = None,
    ) -> None:
        self.db = db
        self.doc_repo = InvoiceDocumentRepository(db)
        self.inv_repo = InvoiceRepository(db)
        self.cust_repo = CustomerRepository(db)
        self.storage = storage_service or LocalStorageService()
        self.ocr = ocr_provider or HeuristicInvoiceOCRProvider()

    def execute(
        self,
        doc_id: UUID,
        company_id: UUID,
        auto_create_draft: bool = True,
    ) -> Tuple[InvoiceDocument, ExtractedInvoiceData, Optional[Invoice]]:
        doc = self.doc_repo.get_by_id(doc_id, company_id)
        if not doc:
            raise NotFoundError("InvoiceDocument", str(doc_id))

        content = self.storage.get_file(doc.storage_key, company_id)
        doc.start_processing()
        doc.retry_count += 1
        self.doc_repo.update(doc)

        try:
            extracted = self.ocr.extract_from_file(content, doc.file_name, doc.mime_type)
            if extracted.is_ambiguous or extracted.confidence < Decimal("0.85"):
                doc.mark_validation_required(extracted.model_dump(mode="json"))
            else:
                doc.mark_validated(extracted.model_dump(mode="json"))
        except Exception as ex:
            extracted = ExtractedInvoiceData(
                confidence=Decimal("0.00"),
                is_ambiguous=True,
                extraction_notes=[f"Retry failed: {str(ex)}"],
            )
            doc.mark_ocr_failed(str(ex), is_retryable=False)

        updated_doc = self.doc_repo.update(doc)

        # Check existing linked draft invoice or create one
        draft_inv: Optional[Invoice] = None
        if updated_doc.invoice_id:
            draft_inv = self.inv_repo.get_by_id(updated_doc.invoice_id, company_id)
            if draft_inv and draft_inv.status == InvoiceStatus.DRAFT:
                if extracted.total_amount:
                    tot = Money(extracted.total_amount, currency=extracted.currency)
                    draft_inv.total_amount = tot
                    draft_inv.outstanding_amount = tot
                    draft_inv.currency = extracted.currency
                if extracted.invoice_number:
                    draft_inv.invoice_number = extracted.invoice_number
                if extracted.issue_date:
                    draft_inv.issue_date = extracted.issue_date
                if extracted.due_date:
                    draft_inv.due_date = extracted.due_date
                draft_inv = self.inv_repo.update(draft_inv)
        elif auto_create_draft and extracted.invoice_number and extracted.total_amount:
            existing_num = self.inv_repo.get_by_number(extracted.invoice_number, company_id)
            if not existing_num:
                resolved_cust: Optional[UUID] = None
                if extracted.tax_id:
                    matches, _ = self.cust_repo.search_customers(company_id=company_id, query_str=extracted.tax_id, limit=2)
                    if len(matches) == 1:
                        resolved_cust = matches[0].id
                issue = extracted.issue_date or date.today()
                due = extracted.due_date or issue
                if due < issue:
                    due = issue
                tot = Money(extracted.total_amount, currency=extracted.currency)
                draft = Invoice(
                    id=uuid.uuid4(),
                    company_id=company_id,
                    customer_id=resolved_cust,
                    document_id=updated_doc.id,
                    invoice_number=extracted.invoice_number,
                    issue_date=issue,
                    due_date=due,
                    total_amount=tot,
                    paid_amount=Money.zero(extracted.currency),
                    outstanding_amount=tot,
                    currency=extracted.currency,
                    status=InvoiceStatus.DRAFT,
                    source=InvoiceSource.PDF_UPLOAD,
                    notes=f"Auto-extracted on retry with confidence {extracted.confidence * 100:.0f}%",
                )
                draft_inv = self.inv_repo.create(draft)
                self.doc_repo.link_to_invoice(updated_doc.id, draft_inv.id, company_id)

        return updated_doc, extracted, draft_inv


class CorrectInvoiceDocumentUseCase:
    """Save human accountant corrections to extracted document fields."""

    def __init__(self, db: Session) -> None:
        self.doc_repo = InvoiceDocumentRepository(db)
        self.inv_repo = InvoiceRepository(db)
        self.cust_repo = CustomerRepository(db)

    def execute(
        self,
        doc_id: UUID,
        company_id: UUID,
        invoice_number: Optional[str] = None,
        customer_id: Optional[UUID] = None,
        issue_date: Optional[date] = None,
        due_date: Optional[date] = None,
        total_amount: Optional[Decimal] = None,
        tax_amount: Optional[Decimal] = None,
        currency: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> InvoiceDocument:
        doc = self.doc_repo.get_by_id(doc_id, company_id)
        if not doc:
            raise NotFoundError("InvoiceDocument", str(doc_id))

        if customer_id:
            cust = self.cust_repo.get_by_id(customer_id, company_id)
            if not cust:
                raise NotFoundError("Customer", str(customer_id))
            if cust.is_archived:
                raise ValidationError(f"Customer '{cust.name}' is archived.")

        # Invariant checks
        if issue_date and due_date and due_date < issue_date:
            raise ValidationError(f"Due date ({due_date}) cannot precede issue date ({issue_date}).")
        if total_amount is not None and total_amount <= Decimal("0.00"):
            raise ValidationError("Total amount must be strictly positive.")

        # Update document extracted_data
        current_data = doc.extracted_data or {}
        normalized = current_data.get("normalized", {})
        if invoice_number is not None:
            normalized["invoice_number"] = invoice_number.strip()
        if customer_id is not None:
            normalized["customer_id"] = str(customer_id)
        if issue_date is not None:
            normalized["issue_date"] = issue_date.isoformat()
        if due_date is not None:
            normalized["due_date"] = due_date.isoformat()
        if total_amount is not None:
            normalized["total_amount"] = str(total_amount)
        if tax_amount is not None:
            normalized["tax_amount"] = str(tax_amount)
        if currency is not None:
            normalized["currency"] = currency.strip().upper()

        current_data["normalized"] = normalized
        current_data["is_manually_corrected"] = True
        doc.mark_manually_corrected(current_data)
        updated_doc = self.doc_repo.update(doc)

        # Sync linked draft invoice if exists
        if doc.invoice_id:
            inv = self.inv_repo.get_by_id(doc.invoice_id, company_id)
            if inv and inv.status == InvoiceStatus.DRAFT:
                if customer_id:
                    inv.customer_id = customer_id
                if invoice_number:
                    inv.invoice_number = invoice_number.strip()
                if issue_date:
                    inv.issue_date = issue_date
                if due_date:
                    inv.due_date = due_date
                if total_amount:
                    tot = Money(total_amount, currency=currency or inv.currency)
                    inv.total_amount = tot
                    inv.outstanding_amount = tot
                if currency:
                    inv.currency = currency.strip().upper()
                if notes:
                    inv.notes = notes
                self.inv_repo.update(inv)

        return updated_doc


class PromoteInvoiceDocumentUseCase:
    """Promote document into an active operational PENDING invoice."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.doc_repo = InvoiceDocumentRepository(db)
        self.inv_repo = InvoiceRepository(db)
        self.cust_repo = CustomerRepository(db)

    def execute(
        self,
        doc_id: UUID,
        company_id: UUID,
        customer_id: UUID,
        invoice_number: Optional[str] = None,
        issue_date: Optional[date] = None,
        due_date: Optional[date] = None,
        total_amount: Optional[Decimal] = None,
        tax_amount: Optional[Decimal] = None,
        currency: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Invoice:
        doc = self.doc_repo.get_by_id(doc_id, company_id)
        if not doc:
            raise NotFoundError("InvoiceDocument", str(doc_id))

        cust = self.cust_repo.get_by_id(customer_id, company_id)
        if not cust:
            raise NotFoundError("Customer", str(customer_id))
        if cust.is_archived:
            raise ValidationError(f"Customer '{cust.name}' is archived.")

        # If already linked to a DRAFT invoice, promote draft
        if doc.invoice_id:
            inv = self.inv_repo.get_by_id(doc.invoice_id, company_id)
            if inv:
                if inv.status == InvoiceStatus.DRAFT:
                    if invoice_number:
                        inv_num = invoice_number.strip()
                        if inv_num != inv.invoice_number:
                            existing = self.inv_repo.get_by_number(inv_num, company_id)
                            if existing and existing.id != inv.id:
                                raise ConflictError(f"Invoice number '{inv_num}' already exists.")
                            inv.invoice_number = inv_num
                    curr = (currency or inv.currency).strip().upper()
                    if total_amount is not None:
                        tot = Money(total_amount, currency=curr)
                        inv.total_amount = tot
                        inv.outstanding_amount = tot
                        inv.paid_amount = Money.zero(curr)
                        inv.currency = curr
                    if issue_date:
                        inv.issue_date = issue_date
                    if due_date:
                        inv.due_date = due_date
                    if tax_amount is not None:
                        inv.tax_amount = Money(tax_amount, currency=curr)
                    if notes:
                        inv.notes = notes

                    inv.publish_draft(customer_id)
                    promoted = self.inv_repo.update(inv)
                    doc.mark_validated(doc.extracted_data or {})
                    self.doc_repo.update(doc)
                    return promoted
                elif inv.status in (InvoiceStatus.PENDING, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.PAID):
                    return inv

        # If no invoice exists, create a new PENDING invoice
        extracted = doc.extracted_data or {}
        norm = extracted.get("normalized", {})
        inv_num = (invoice_number or norm.get("invoice_number") or f"INV-{uuid.uuid4().hex[:8].upper()}").strip()
        existing = self.inv_repo.get_by_number(inv_num, company_id)
        if existing:
            raise ConflictError(f"Invoice number '{inv_num}' already exists.")

        iss = issue_date or (date.fromisoformat(norm["issue_date"]) if norm.get("issue_date") else date.today())
        due = due_date or (date.fromisoformat(norm["due_date"]) if norm.get("due_date") else iss)
        if due < iss:
            due = iss

        tot_dec = total_amount or (Decimal(norm["total_amount"]) if norm.get("total_amount") else Decimal("1.00"))
        curr = (currency or norm.get("currency") or "INR").strip().upper()
        tot_money = Money(tot_dec, currency=curr)
        tax_money = Money(tax_amount, currency=curr) if tax_amount is not None else None

        new_invoice = Invoice(
            id=uuid.uuid4(),
            company_id=company_id,
            customer_id=customer_id,
            document_id=doc.id,
            invoice_number=inv_num,
            issue_date=iss,
            due_date=due,
            total_amount=tot_money,
            paid_amount=Money.zero(curr),
            outstanding_amount=tot_money,
            tax_amount=tax_money,
            currency=curr,
            status=InvoiceStatus.PENDING,
            source=InvoiceSource.PDF_UPLOAD,
            notes=notes,
        )
        created = self.inv_repo.create(new_invoice)
        doc.invoice_id = created.id
        doc.mark_validated(doc.extracted_data or {})
        self.doc_repo.update(doc)
        return created


class DeleteInvoiceDocumentUseCase:
    """Safely delete unlinked or draft document and purge physical storage file."""

    def __init__(self, db: Session, storage_service: Optional[StorageService] = None) -> None:
        self.db = db
        self.doc_repo = InvoiceDocumentRepository(db)
        self.inv_repo = InvoiceRepository(db)
        self.storage = storage_service or LocalStorageService()

    def execute(self, doc_id: UUID, company_id: UUID) -> bool:
        doc = self.doc_repo.get_by_id(doc_id, company_id)
        if not doc:
            raise NotFoundError("InvoiceDocument", str(doc_id))

        if doc.invoice_id:
            inv = self.inv_repo.get_by_id(doc.invoice_id, company_id)
            if inv:
                if not inv.paid_amount.is_zero():
                    raise ValidationError("Cannot delete document linked to an invoice with recorded payments.")
                if inv.status == InvoiceStatus.DRAFT:
                    self.inv_repo.delete(inv.id, company_id)
                elif inv.status in (InvoiceStatus.PENDING, InvoiceStatus.CANCELLED):
                    inv.document_id = None
                    self.inv_repo.update(inv)

        # Purge physical file
        self.storage.delete_file(doc.storage_key, company_id)
        # Delete document record
        return self.doc_repo.delete(doc_id, company_id)

