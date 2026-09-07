"""REST API routes for Invoice Management.

Exposes endpoints for manual invoice entry, PDF upload and OCR extraction,
draft invoice confirmation, bulk CSV import with row-level error reporting,
filtering, search, details, cancellation, and secure document streaming.
Adheres to the universal {"success": True, "data": ...} envelope standard.
"""

from datetime import date
from typing import Any, Dict, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.modules.auth.domain.entities import User
from app.modules.auth.presentation.dependencies import get_current_user
from app.modules.invoice.application.use_cases import (
    ArchiveInvoiceUseCase,
    CancelInvoiceUseCase,
    ConfirmDraftInvoiceUseCase,
    CreateInvoiceUseCase,
    DeleteInvoiceUseCase,
    GetInvoiceDetailUseCase,
    GetInvoiceDocumentFileUseCase,
    ImportInvoicesCSVUseCase,
    ListInvoicesUseCase,
    SearchInvoicesUseCase,
    UnarchiveInvoiceUseCase,
    UpdateInvoiceUseCase,
    UploadInvoiceDocumentUseCase,
)
from app.modules.invoice.domain.entities import Invoice, InvoiceStatus
from app.modules.invoice.presentation.schemas import (
    CSVImportResultResponse,
    CSVRowErrorResponse,
    CustomerSummaryResponse,
    InvoiceCancelRequest,
    InvoiceConfirmDraftRequest,
    InvoiceCreateRequest,
    InvoiceDetailResponse,
    InvoiceDocumentResponse,
    InvoiceListResponse,
    InvoiceResponse,
    InvoiceUpdateRequest,
    InvoiceUploadResponse,
)

router = APIRouter(prefix="/invoices", tags=["Invoices"])


def _to_invoice_response(inv: Invoice) -> InvoiceResponse:
    """Helper to convert domain entity to API schema."""
    return InvoiceResponse(
        id=inv.id,
        company_id=inv.company_id,
        customer_id=inv.customer_id,
        document_id=inv.document_id,
        invoice_number=inv.invoice_number,
        issue_date=inv.issue_date,
        due_date=inv.due_date,
        total_amount=inv.total_amount.amount,
        paid_amount=inv.paid_amount.amount,
        outstanding_amount=inv.outstanding_amount.amount,
        tax_amount=inv.tax_amount.amount if inv.tax_amount else None,
        currency=inv.currency,
        status=inv.status.value,
        source=inv.source.value,
        notes=inv.notes,
        is_archived=inv.is_archived,
        created_at=inv.created_at,
        updated_at=inv.updated_at,
    )


@router.get("/status", summary="Invoice module status probe")
def invoice_module_status() -> Dict[str, str]:
    """Return invoice module readiness status."""
    return {"module": "invoice", "status": "initialized"}


@router.post("", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
def create_invoice(
    payload: InvoiceCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Manually create an operational PENDING invoice."""
    use_case = CreateInvoiceUseCase(db)
    invoice = use_case.execute(
        company_id=current_user.company_id,
        invoice_number=payload.invoice_number,
        customer_id=payload.customer_id,
        issue_date=payload.issue_date,
        due_date=payload.due_date,
        total_amount=payload.total_amount,
        currency=payload.currency,
        tax_amount=payload.tax_amount,
        notes=payload.notes,
        document_id=payload.document_id,
    )
    db.commit()
    return {
        "success": True,
        "data": _to_invoice_response(invoice),
    }


@router.post("/upload", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def upload_invoice_document(
    file: UploadFile = File(...),
    auto_create_draft: bool = Query(default=True, description="Automatically generate a DRAFT invoice from OCR output"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Upload invoice PDF/image document, store securely, and run OCR extraction."""
    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    use_case = UploadInvoiceDocumentUseCase(db)
    doc, extracted, draft_inv = use_case.execute(
        company_id=current_user.company_id,
        file_content=content,
        filename=file.filename or "invoice.pdf",
        mime_type=file.content_type or "application/pdf",
        auto_create_draft=auto_create_draft,
    )
    db.commit()

    doc_response = InvoiceDocumentResponse(
        id=doc.id,
        file_name=doc.file_name,
        file_size_bytes=doc.file_size_bytes,
        mime_type=doc.mime_type,
        file_hash=doc.file_hash,
        ocr_status=doc.ocr_status.value,
        extracted_data=doc.extracted_data,
        created_at=doc.created_at,
    )

    draft_response = _to_invoice_response(draft_inv) if draft_inv else None

    return {
        "success": True,
        "data": InvoiceUploadResponse(
            document=doc_response,
            extracted_data=extracted.model_dump(mode="json"),
            draft_invoice=draft_response,
        ),
    }


@router.post("/{invoice_id}/confirm-draft", response_model=Dict[str, Any])
def confirm_draft_invoice(
    invoice_id: UUID,
    payload: InvoiceConfirmDraftRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Promote an extracted DRAFT invoice into an active PENDING invoice after accountant verification."""
    use_case = ConfirmDraftInvoiceUseCase(db)
    invoice = use_case.execute(
        company_id=current_user.company_id,
        invoice_id=invoice_id,
        customer_id=payload.customer_id,
        invoice_number=payload.invoice_number,
        issue_date=payload.issue_date,
        due_date=payload.due_date,
        total_amount=payload.total_amount,
        tax_amount=payload.tax_amount,
        currency=payload.currency,
        notes=payload.notes,
    )
    db.commit()
    return {
        "success": True,
        "data": _to_invoice_response(invoice),
    }


@router.post("/import-csv", response_model=Dict[str, Any])
async def import_invoices_csv(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Bulk import invoices from CSV with row-level validation and detailed error reporting."""
    content_bytes = await file.read()
    try:
        csv_text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        csv_text = content_bytes.decode("latin-1")

    use_case = ImportInvoicesCSVUseCase(db)
    result = use_case.execute(company_id=current_user.company_id, csv_text=csv_text)
    db.commit()

    return {
        "success": True,
        "data": CSVImportResultResponse(
            total_rows=result.total_rows,
            imported_count=result.imported_count,
            failed_count=result.failed_count,
            errors=[
                CSVRowErrorResponse(
                    row_number=e.row_number,
                    invoice_number=e.invoice_number,
                    error_message=e.error_message,
                )
                for e in result.errors
            ],
            imported_invoices=[_to_invoice_response(inv) for inv in result.imported_invoices],
        ),
    }


@router.get("", response_model=Dict[str, Any])
def list_invoices(
    customer_id: Optional[UUID] = None,
    status: Optional[InvoiceStatus] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    due_date_from: Optional[date] = None,
    due_date_to: Optional[date] = None,
    has_outstanding: Optional[bool] = None,
    is_archived: Optional[bool] = Query(default=False, description="Filter by archived state (default: active non-archived)"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """List invoices with status, date range, and customer filtering."""
    use_case = ListInvoicesUseCase(db)
    items, total = use_case.execute(
        company_id=current_user.company_id,
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
    return {
        "success": True,
        "data": InvoiceListResponse(
            items=[_to_invoice_response(inv) for inv in items],
            total=total,
            limit=limit,
            offset=offset,
        ),
    }


@router.get("/search", response_model=Dict[str, Any])
def search_invoices(
    q: str = Query(..., min_length=1, description="Search query matching invoice number or customer"),
    is_archived: Optional[bool] = Query(default=False, description="Filter by archived state"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Fast search across invoice numbers and customer identities."""
    use_case = SearchInvoicesUseCase(db)
    items, total = use_case.execute(
        company_id=current_user.company_id,
        query_str=q,
        is_archived=is_archived,
        limit=limit,
        offset=offset,
    )
    return {
        "success": True,
        "data": InvoiceListResponse(
            items=[_to_invoice_response(inv) for inv in items],
            total=total,
            limit=limit,
            offset=offset,
        ),
    }


@router.get("/{invoice_id}", response_model=Dict[str, Any])
def get_invoice_detail(
    invoice_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Get complete invoice details with eager customer and document metadata."""
    use_case = GetInvoiceDetailUseCase(db)
    detail = use_case.execute(invoice_id=invoice_id, company_id=current_user.company_id)

    cust_resp: Optional[CustomerSummaryResponse] = None
    if detail.customer:
        cust_resp = CustomerSummaryResponse(
            id=detail.customer.id,
            name=detail.customer.name,
            tax_id=detail.customer.tax_id,
            email=detail.customer.email,
        )

    doc_resp: Optional[InvoiceDocumentResponse] = None
    if detail.document:
        doc_resp = InvoiceDocumentResponse(
            id=detail.document.id,
            file_name=detail.document.file_name,
            file_size_bytes=detail.document.file_size_bytes,
            mime_type=detail.document.mime_type,
            file_hash=detail.document.file_hash,
            ocr_status=detail.document.ocr_status.value,
            extracted_data=detail.document.extracted_data,
            created_at=detail.document.created_at,
        )

    base_resp = _to_invoice_response(detail.invoice)
    detail_payload = InvoiceDetailResponse(
        **base_resp.model_dump(),
        customer=cust_resp,
        document=doc_resp,
    )
    return {
        "success": True,
        "data": detail_payload,
    }


@router.put("/{invoice_id}", response_model=Dict[str, Any])
def update_invoice(
    invoice_id: UUID,
    payload: InvoiceUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Update allowed metadata (due date, notes) on an active invoice."""
    use_case = UpdateInvoiceUseCase(db)
    updated = use_case.execute(
        invoice_id=invoice_id,
        company_id=current_user.company_id,
        due_date=payload.due_date,
        notes=payload.notes,
    )
    db.commit()
    return {
        "success": True,
        "data": _to_invoice_response(updated),
    }


@router.post("/{invoice_id}/cancel", response_model=Dict[str, Any])
def cancel_invoice(
    invoice_id: UUID,
    payload: InvoiceCancelRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Cancel an invoice, verifying no payments have been allocated."""
    use_case = CancelInvoiceUseCase(db)
    cancelled = use_case.execute(
        invoice_id=invoice_id,
        company_id=current_user.company_id,
        reason=payload.reason,
    )
    db.commit()
    return {
        "success": True,
        "data": _to_invoice_response(cancelled),
    }


@router.post("/{invoice_id}/archive", response_model=Dict[str, Any])
def archive_invoice(
    invoice_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Archive an invoice, preserving its historical record while hiding from active lists."""
    use_case = ArchiveInvoiceUseCase(db)
    archived = use_case.execute(invoice_id=invoice_id, company_id=current_user.company_id)
    db.commit()
    return {
        "success": True,
        "data": _to_invoice_response(archived),
    }


@router.post("/{invoice_id}/unarchive", response_model=Dict[str, Any])
def unarchive_invoice(
    invoice_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Restore an archived invoice back to active operational views."""
    use_case = UnarchiveInvoiceUseCase(db)
    unarchived = use_case.execute(invoice_id=invoice_id, company_id=current_user.company_id)
    db.commit()
    return {
        "success": True,
        "data": _to_invoice_response(unarchived),
    }


@router.delete("/{invoice_id}", response_model=Dict[str, Any])
def delete_invoice(
    invoice_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Safely delete an unallocated invoice."""
    use_case = DeleteInvoiceUseCase(db)
    use_case.execute(invoice_id=invoice_id, company_id=current_user.company_id)
    db.commit()
    return {
        "success": True,
        "data": {"message": "Invoice deleted successfully", "id": str(invoice_id)},
    }


@router.get("/{invoice_id}/document")
def get_invoice_document_file(
    invoice_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Stream or download original source document with tenant isolation."""
    use_case = GetInvoiceDocumentFileUseCase(db)
    file_bytes, filename, mime_type = use_case.execute(
        invoice_id=invoice_id, company_id=current_user.company_id
    )
    return Response(
        content=file_bytes,
        media_type=mime_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
