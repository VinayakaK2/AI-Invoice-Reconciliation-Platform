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
        # 1. Validation of extension and size
        ext = Path(filename).suffix.lower()
        if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS:
            raise ValidationError(
                f"File extension '{ext}' not allowed. Permitted types: {settings.ALLOWED_UPLOAD_EXTENSIONS}"
            )

        # 2. Persist to storage
        storage_key, file_hash, file_size = self.storage.save_file(file_content, filename, company_id)

        # Check existing document hash for duplicate upload
        existing_doc = self.document_repo.get_by_hash(file_hash, company_id)
        if existing_doc:
            # Document already processed; perform OCR on existing or return
            extracted = self.ocr.extract_from_file(file_content, filename, mime_type)
            existing_inv = (
                self.invoice_repo.get_by_id(existing_doc.invoice_id, company_id)
                if existing_doc.invoice_id
                else None
            )
            return existing_doc, extracted, existing_inv

        # 3. Perform OCR Extraction
        try:
            extracted_data = self.ocr.extract_from_file(file_content, filename, mime_type)
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
        return self.invoice_repo.update(invoice)


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
