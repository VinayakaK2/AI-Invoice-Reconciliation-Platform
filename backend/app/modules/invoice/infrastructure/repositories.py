"""Repository implementations for Invoices and Invoice Documents.

Handles SQLAlchemy database operations, mapping to pure domain entities,
enforcing multi-tenant isolation, and executing filtered search queries.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from app.modules.customer.infrastructure.models import CustomerModel
from app.modules.invoice.domain.entities import (
    Invoice,
    InvoiceDocument,
    InvoiceSource,
    InvoiceStatus,
    OCRStatus,
)
from app.modules.invoice.infrastructure.models import (
    InvoiceDocumentModel,
    InvoiceModel,
)
from app.shared.domain.money import Money


class InvoiceDocumentRepository:
    """Repository handling database persistence for uploaded invoice documents."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _to_domain(self, model: InvoiceDocumentModel) -> InvoiceDocument:
        """Map ORM model to pure domain entity."""
        return InvoiceDocument(
            id=model.id,
            company_id=model.company_id,
            file_name=model.file_name,
            storage_key=model.storage_key,
            file_size_bytes=model.file_size_bytes,
            mime_type=model.mime_type,
            file_hash=model.file_hash,
            invoice_id=model.invoice_id,
            ocr_status=OCRStatus(model.ocr_status),
            extracted_data=model.extracted_data,
            created_at=model.created_at,
        )

    def create(self, doc: InvoiceDocument) -> InvoiceDocument:
        """Persist a new document metadata record."""
        model = InvoiceDocumentModel(
            id=doc.id,
            company_id=doc.company_id,
            file_name=doc.file_name,
            storage_key=doc.storage_key,
            file_size_bytes=doc.file_size_bytes,
            mime_type=doc.mime_type,
            file_hash=doc.file_hash,
            invoice_id=doc.invoice_id,
            ocr_status=doc.ocr_status.value,
            extracted_data=doc.extracted_data,
            created_at=doc.created_at,
        )
        self.db.add(model)
        self.db.flush()
        return self._to_domain(model)

    def get_by_id(self, doc_id: UUID, company_id: UUID) -> Optional[InvoiceDocument]:
        """Fetch document by ID within tenant context."""
        model = (
            self.db.query(InvoiceDocumentModel)
            .filter(
                InvoiceDocumentModel.id == doc_id,
                InvoiceDocumentModel.company_id == company_id,
            )
            .first()
        )
        return self._to_domain(model) if model else None

    def get_by_hash(self, file_hash: str, company_id: UUID) -> Optional[InvoiceDocument]:
        """Find existing document by file content hash within tenant context."""
        model = (
            self.db.query(InvoiceDocumentModel)
            .filter(
                InvoiceDocumentModel.file_hash == file_hash,
                InvoiceDocumentModel.company_id == company_id,
            )
            .first()
        )
        return self._to_domain(model) if model else None

    def link_to_invoice(self, doc_id: UUID, invoice_id: UUID, company_id: UUID) -> None:
        """Associate document with created invoice."""
        model = (
            self.db.query(InvoiceDocumentModel)
            .filter(
                InvoiceDocumentModel.id == doc_id,
                InvoiceDocumentModel.company_id == company_id,
            )
            .first()
        )
        if model:
            model.invoice_id = invoice_id
            self.db.flush()

    def update_ocr_status(
        self,
        doc_id: UUID,
        company_id: UUID,
        ocr_status: OCRStatus,
        extracted_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update extraction status and extracted structured JSON payload."""
        model = (
            self.db.query(InvoiceDocumentModel)
            .filter(
                InvoiceDocumentModel.id == doc_id,
                InvoiceDocumentModel.company_id == company_id,
            )
            .first()
        )
        if model:
            model.ocr_status = ocr_status.value
            if extracted_data is not None:
                model.extracted_data = extracted_data
            self.db.flush()


class InvoiceRepository:
    """Repository handling database operations for Invoices."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _to_domain(self, model: InvoiceModel) -> Invoice:
        """Convert ORM model to pure domain Invoice aggregate root with exact Money types."""
        curr = model.currency or "INR"
        tax = (
            Money(model.tax_amount, currency=curr)
            if model.tax_amount is not None
            else None
        )
        return Invoice(
            id=model.id,
            company_id=model.company_id,
            customer_id=model.customer_id,
            document_id=model.document_id,
            invoice_number=model.invoice_number,
            issue_date=model.issue_date,
            due_date=model.due_date,
            total_amount=Money(model.total_amount, currency=curr),
            paid_amount=Money(model.paid_amount, currency=curr),
            outstanding_amount=Money(model.outstanding_amount, currency=curr),
            tax_amount=tax,
            currency=curr,
            status=InvoiceStatus(model.status),
            source=InvoiceSource(model.source),
            notes=model.notes,
            is_archived=model.is_archived,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def create(self, invoice: Invoice) -> Invoice:
        """Persist a new invoice record."""
        model = InvoiceModel(
            id=invoice.id,
            company_id=invoice.company_id,
            customer_id=invoice.customer_id,
            document_id=invoice.document_id,
            invoice_number=invoice.invoice_number,
            issue_date=invoice.issue_date,
            due_date=invoice.due_date,
            total_amount=invoice.total_amount.amount,
            paid_amount=invoice.paid_amount.amount,
            outstanding_amount=invoice.outstanding_amount.amount,
            tax_amount=invoice.tax_amount.amount if invoice.tax_amount else None,
            currency=invoice.currency,
            status=invoice.status.value,
            source=invoice.source.value,
            notes=invoice.notes,
            is_archived=invoice.is_archived,
            created_at=invoice.created_at,
            updated_at=invoice.updated_at,
        )
        self.db.add(model)
        self.db.flush()
        return self._to_domain(model)

    def get_by_id(self, invoice_id: UUID, company_id: UUID) -> Optional[Invoice]:
        """Fetch invoice by UUID within tenant context."""
        model = (
            self.db.query(InvoiceModel)
            .filter(
                InvoiceModel.id == invoice_id,
                InvoiceModel.company_id == company_id,
            )
            .first()
        )
        return self._to_domain(model) if model else None

    def get_by_number(self, invoice_number: str, company_id: UUID) -> Optional[Invoice]:
        """Fetch invoice by exact number within tenant context."""
        model = (
            self.db.query(InvoiceModel)
            .filter(
                func.lower(InvoiceModel.invoice_number) == invoice_number.strip().lower(),
                InvoiceModel.company_id == company_id,
            )
            .first()
        )
        return self._to_domain(model) if model else None

    def update(self, invoice: Invoice) -> Invoice:
        """Update existing invoice state, amounts, and metadata."""
        model = (
            self.db.query(InvoiceModel)
            .filter(
                InvoiceModel.id == invoice.id,
                InvoiceModel.company_id == invoice.company_id,
            )
            .first()
        )
        if not model:
            raise ValueError(f"Invoice {invoice.id} not found for update.")

        model.customer_id = invoice.customer_id
        model.document_id = invoice.document_id
        model.invoice_number = invoice.invoice_number
        model.issue_date = invoice.issue_date
        model.due_date = invoice.due_date
        model.total_amount = invoice.total_amount.amount
        model.paid_amount = invoice.paid_amount.amount
        model.outstanding_amount = invoice.outstanding_amount.amount
        model.tax_amount = invoice.tax_amount.amount if invoice.tax_amount else None
        model.currency = invoice.currency
        model.status = invoice.status.value
        model.notes = invoice.notes
        model.is_archived = invoice.is_archived
        model.updated_at = invoice.updated_at or datetime.now(timezone.utc)

        self.db.flush()
        return self._to_domain(model)

    def delete(self, invoice_id: UUID, company_id: UUID) -> bool:
        """Delete invoice within tenant context."""
        model = (
            self.db.query(InvoiceModel)
            .filter(
                InvoiceModel.id == invoice_id,
                InvoiceModel.company_id == company_id,
            )
            .first()
        )
        if model:
            self.db.delete(model)
            self.db.flush()
            return True
        return False

    def list_invoices(
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
        """Query paginated invoices with flexible operational filters."""
        query = self.db.query(InvoiceModel).filter(InvoiceModel.company_id == company_id)

        if customer_id:
            query = query.filter(InvoiceModel.customer_id == customer_id)
        if status:
            query = query.filter(InvoiceModel.status == status.value)
        if is_archived is not None:
            query = query.filter(InvoiceModel.is_archived == is_archived)
        if date_from:
            query = query.filter(InvoiceModel.issue_date >= date_from)
        if date_to:
            query = query.filter(InvoiceModel.issue_date <= date_to)
        if due_date_from:
            query = query.filter(InvoiceModel.due_date >= due_date_from)
        if due_date_to:
            query = query.filter(InvoiceModel.due_date <= due_date_to)
        if has_outstanding is True:
            query = query.filter(InvoiceModel.outstanding_amount > 0)
        elif has_outstanding is False:
            query = query.filter(InvoiceModel.outstanding_amount == 0)

        total = query.count()
        models = (
            query.order_by(InvoiceModel.issue_date.desc(), InvoiceModel.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [self._to_domain(m) for m in models], total

    def search_invoices(
        self,
        company_id: UUID,
        query_str: str,
        is_archived: Optional[bool] = False,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Invoice], int]:
        """Search invoices by number or customer name/tax_id."""
        clean_q = query_str.strip()
        if not clean_q:
            return self.list_invoices(
                company_id=company_id,
                is_archived=is_archived,
                limit=limit,
                offset=offset,
            )

        like_pattern = f"%{clean_q}%"

        # Subquery matching customers
        cust_select = (
            select(CustomerModel.id)
            .where(
                CustomerModel.company_id == company_id,
                or_(
                    CustomerModel.name.ilike(like_pattern),
                    CustomerModel.tax_id.ilike(like_pattern),
                ),
            )
        )

        query = self.db.query(InvoiceModel).filter(
            InvoiceModel.company_id == company_id,
            or_(
                InvoiceModel.invoice_number.ilike(like_pattern),
                InvoiceModel.customer_id.in_(cust_select),
            ),
        )

        if is_archived is not None:
            query = query.filter(InvoiceModel.is_archived == is_archived)

        total = query.count()
        models = (
            query.order_by(InvoiceModel.issue_date.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [self._to_domain(m) for m in models], total
