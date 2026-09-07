"""Pydantic v2 schemas for Invoice API request and response validation."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class InvoiceCreateRequest(BaseModel):
    """Schema for manual invoice creation."""
    invoice_number: str = Field(..., min_length=1, max_length=100, description="Unique invoice number")
    customer_id: UUID = Field(..., description="Target customer counterparty UUID")
    issue_date: date = Field(..., description="Date when invoice was issued")
    due_date: date = Field(..., description="Payment due date")
    total_amount: Decimal = Field(..., gt=0, decimal_places=2, description="Gross total invoice amount")
    currency: str = Field(default="INR", min_length=3, max_length=3, description="ISO currency code")
    tax_amount: Optional[Decimal] = Field(default=None, ge=0, decimal_places=2, description="Tax / GST component")
    notes: Optional[str] = Field(default=None, max_length=1000, description="Optional invoice remarks")
    document_id: Optional[UUID] = Field(default=None, description="Optional attached source document UUID")


class InvoiceUpdateRequest(BaseModel):
    """Schema for updating operational invoice metadata."""
    due_date: Optional[date] = Field(default=None, description="Updated payment due date")
    notes: Optional[str] = Field(default=None, max_length=1000, description="Updated notes")


class InvoiceConfirmDraftRequest(BaseModel):
    """Schema for confirming and publishing a DRAFT invoice."""
    customer_id: UUID = Field(..., description="Customer to bind invoice to")
    invoice_number: Optional[str] = Field(default=None, min_length=1, max_length=100)
    issue_date: Optional[date] = None
    due_date: Optional[date] = None
    total_amount: Optional[Decimal] = Field(default=None, gt=0, decimal_places=2)
    tax_amount: Optional[Decimal] = Field(default=None, ge=0, decimal_places=2)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    notes: Optional[str] = Field(default=None, max_length=1000)


class InvoiceCancelRequest(BaseModel):
    """Schema for invoice cancellation."""
    reason: Optional[str] = Field(default=None, max_length=500, description="Reason for cancellation")


class InvoiceDocumentResponse(BaseModel):
    """Schema for source invoice document metadata."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_name: str
    file_size_bytes: int
    mime_type: str
    file_hash: str
    ocr_status: str
    extracted_data: Optional[Dict[str, Any]] = None
    created_at: datetime


class CustomerSummaryResponse(BaseModel):
    """Minimal customer summary schema for invoice detail views."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    tax_id: Optional[str] = None
    email: Optional[str] = None


class InvoiceResponse(BaseModel):
    """Schema representing an invoice."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    customer_id: Optional[UUID] = None
    document_id: Optional[UUID] = None
    invoice_number: str
    issue_date: date
    due_date: date
    total_amount: Decimal
    paid_amount: Decimal
    outstanding_amount: Decimal
    tax_amount: Optional[Decimal] = None
    currency: str
    status: str
    source: str
    notes: Optional[str] = None
    is_archived: bool = False
    created_at: datetime
    updated_at: datetime


class InvoiceDetailResponse(InvoiceResponse):
    """Enriched invoice schema with joined customer and document metadata."""
    customer: Optional[CustomerSummaryResponse] = None
    document: Optional[InvoiceDocumentResponse] = None


class InvoiceListResponse(BaseModel):
    """Paginated list of invoices."""
    items: List[InvoiceResponse]
    total: int
    limit: int
    offset: int


class InvoiceUploadResponse(BaseModel):
    """Response returned upon document upload and OCR processing."""
    document: InvoiceDocumentResponse
    extracted_data: Dict[str, Any]
    draft_invoice: Optional[InvoiceResponse] = None


class CSVRowErrorResponse(BaseModel):
    """Error detail for a single invalid CSV row."""
    row_number: int
    invoice_number: Optional[str] = None
    error_message: str


class CSVImportResultResponse(BaseModel):
    """Batch CSV import outcome with row-level error reporting."""
    total_rows: int
    imported_count: int
    failed_count: int
    errors: List[CSVRowErrorResponse]
    imported_invoices: List[InvoiceResponse]
