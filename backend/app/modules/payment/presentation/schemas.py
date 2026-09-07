"""Pydantic v2 schemas for Payment & Bank Statement API validation."""

from datetime import date, datetime
from decimal import Decimal
import math
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


def mask_bank_account(account_num: Optional[str]) -> Optional[str]:
    """Mask banking coordinate to protect sensitive coordinates in API responses."""
    if not account_num:
        return None
    clean = account_num.strip()
    if len(clean) <= 4:
        return clean
    return f"••••••••{clean[-4:]}"


class CSVRowErrorResponse(BaseModel):
    """Schema for individual row failure in statement import."""

    row_number: int
    field: Optional[str] = None
    message: str


class ImportStatementResponse(BaseModel):
    """Schema for bank statement import result summary."""

    batch_id: Optional[UUID] = None
    file_name: str
    total_rows: int
    imported_payments_count: int
    skipped_debits_count: int
    skipped_duplicates_count: int
    failed_rows_count: int
    errors: List[CSVRowErrorResponse] = []
    imported_payment_ids: List[UUID] = []


class ImportBatchResponse(BaseModel):
    """Schema for statement batch list item."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    file_name: str
    file_hash: str
    total_rows: int
    imported_count: int
    failed_count: int
    duplicate_count: int
    status: str
    error_message: Optional[str] = None
    created_at: datetime


class BankTransactionResponse(BaseModel):
    """Schema for raw bank statement transaction line item."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    batch_id: UUID
    transaction_date: date
    value_date: Optional[date] = None
    amount: Decimal
    transaction_type: str
    narration: str
    reference_number: Optional[str] = None
    masked_bank_account: Optional[str] = None
    counterparty_name: Optional[str] = None
    balance: Optional[Decimal] = None
    deduplication_hash: str
    created_at: datetime

    @classmethod
    def from_domain(cls, txn: Any) -> "BankTransactionResponse":
        """Construct response from domain BankTransaction entity."""
        return cls(
            id=txn.id,
            company_id=txn.company_id,
            batch_id=txn.batch_id,
            transaction_date=txn.transaction_date,
            value_date=txn.value_date,
            amount=txn.amount.amount,
            transaction_type=txn.transaction_type.value if hasattr(txn.transaction_type, "value") else txn.transaction_type,
            narration=txn.narration,
            reference_number=txn.reference_number,
            masked_bank_account=mask_bank_account(txn.bank_account_number),
            counterparty_name=txn.counterparty_name,
            balance=txn.balance.amount if txn.balance else None,
            deduplication_hash=txn.deduplication_hash,
            created_at=txn.created_at or datetime.now(),
        )


class ImportBatchDetailResponse(BaseModel):
    """Schema for statement batch detail with associated transactions."""

    batch: ImportBatchResponse
    transactions: List[BankTransactionResponse]


class BankTransactionListResponse(BaseModel):
    """Paginated list envelope for bank transactions."""

    items: List[BankTransactionResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class PaymentResponse(BaseModel):
    """Schema for normalized incoming receivable payment."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    batch_id: Optional[UUID] = None
    bank_transaction_id: Optional[UUID] = None
    transaction_date: date
    amount: Decimal
    allocated_amount: Decimal
    unallocated_amount: Decimal
    currency: str
    narration: str
    reference_number: Optional[str] = None
    masked_bank_account: Optional[str] = None
    payer_raw_name: Optional[str] = None
    payer_raw_identifier: Optional[str] = None
    status: str
    source: str
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_domain(cls, p: Any) -> "PaymentResponse":
        """Construct response from domain Payment entity."""
        return cls(
            id=p.id,
            company_id=p.company_id,
            batch_id=p.batch_id,
            bank_transaction_id=p.bank_transaction_id,
            transaction_date=p.transaction_date,
            amount=p.amount.amount,
            allocated_amount=p.allocated_amount.amount,
            unallocated_amount=p.unallocated_amount.amount,
            currency=p.currency,
            narration=p.narration,
            reference_number=p.reference_number,
            masked_bank_account=mask_bank_account(p.bank_account_number),
            payer_raw_name=p.payer_raw_name,
            payer_raw_identifier=p.payer_raw_identifier,
            status=p.status.value if hasattr(p.status, "value") else p.status,
            source=p.source.value if hasattr(p.source, "value") else p.source,
            notes=p.notes,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


class PaymentListResponse(BaseModel):
    """Paginated list envelope for payments."""

    items: List[PaymentResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class CreateManualPaymentRequest(BaseModel):
    """Schema for manual direct payment receipt creation."""

    transaction_date: date = Field(..., description="Date payment was received")
    amount: Decimal = Field(..., gt=0, decimal_places=2, description="Payment receipt amount")
    currency: str = Field(default="INR", min_length=3, max_length=3, description="ISO currency code")
    narration: str = Field(default="Manual payment receipt", max_length=500, description="Payment particulars")
    reference_number: Optional[str] = Field(default=None, max_length=255, description="Bank UTR or reference number")
    bank_account_number: Optional[str] = Field(default=None, max_length=100, description="Receiving bank account number")
    payer_raw_name: Optional[str] = Field(default=None, max_length=255, description="Reported payer / customer name")
    notes: Optional[str] = Field(default=None, max_length=1000, description="Internal remarks")


class IgnorePaymentRequest(BaseModel):
    """Schema for marking a payment as ignored."""

    reason: str = Field(..., min_length=3, max_length=500, description="Justification for ignoring payment")
