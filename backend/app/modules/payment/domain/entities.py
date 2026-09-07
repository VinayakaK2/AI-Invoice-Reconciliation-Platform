"""Payment module domain entities, value objects, and financial invariants.

Adheres to Clean Architecture and DDD Lite principles:
- Pure Python dataclasses with zero framework/ORM dependencies.
- Exact monetary representations via Money (Decimal).
- Strict financial balance conservation invariants.
- Credit vs Debit segregation.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import re
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.shared.domain.money import Money
from app.shared.exceptions import DomainError, FinancialInvariantError, ValidationError


class ImportBatchStatus(str, Enum):
    """Lifecycle status of a bank statement file import batch."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TransactionType(str, Enum):
    """Direction of bank transaction from statement."""

    CREDIT = "CREDIT"  # Inflow: Incoming funds / receivable candidate
    DEBIT = "DEBIT"    # Outflow: Withdrawal / charge / expense


class PaymentStatus(str, Enum):
    """Authoritative reconciliation state of an incoming receivable payment."""

    UNRECONCILED = "UNRECONCILED"              # Zero amount allocated
    PARTIALLY_RECONCILED = "PARTIALLY_RECONCILED"  # Partial amount allocated
    RECONCILED = "RECONCILED"                  # Fully allocated against invoices
    IGNORED = "IGNORED"                        # Intentionally skipped by accountant (e.g. non-sales receipt)


class PaymentSource(str, Enum):
    """Source origin of the payment record."""

    BANK_STATEMENT_CSV = "BANK_STATEMENT_CSV"
    MANUAL = "MANUAL"
    API = "API"


@dataclass(frozen=True)
class CSVRowError:
    """Granular error detailing a single row parsing or validation failure."""

    row_number: int
    field: Optional[str]
    message: str


@dataclass
class ParsedBankTransaction:
    """Intermediate validated row emitted by the statement parser before database persistence."""

    row_number: int
    transaction_date: date
    amount: Decimal
    transaction_type: TransactionType
    narration: str
    reference_number: Optional[str] = None
    value_date: Optional[date] = None
    balance: Optional[Decimal] = None
    currency: str = "INR"
    bank_account_number: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)
    counterparty_name: Optional[str] = None


@dataclass
class BankStatementImportResult:
    """Batch execution summary providing complete visibility into import results."""

    batch_id: Optional[UUID]
    file_name: str
    total_rows: int
    imported_payments_count: int
    skipped_debits_count: int
    skipped_duplicates_count: int
    failed_rows_count: int
    errors: List[CSVRowError] = field(default_factory=list)
    imported_payment_ids: List[UUID] = field(default_factory=list)


class TransactionFingerprint:
    """Generates deterministic deduplication hashes for bank transactions."""

    GENERIC_PLACEHOLDERS = {"NA", "N/A", "NONE", "0", "-", ".", "NULL", "N.A.", "NOTAVAILABLE"}

    @classmethod
    def is_valid_reference(cls, reference_number: Optional[str]) -> bool:
        """Check whether reference is a real reference rather than a blank or generic placeholder."""
        if not reference_number or not reference_number.strip():
            return False
        clean = re.sub(r"[^A-Za-z0-9]", "", reference_number.strip()).upper()
        if not clean or clean in cls.GENERIC_PLACEHOLDERS:
            return False
        return len(clean) >= 4

    @staticmethod
    def compute_primary(company_id: UUID, reference_number: str, account_identifier: Optional[str] = None) -> str:
        """Generate hash from verified bank reference / UTR, scoped by account if available."""
        norm_ref = re.sub(r"\s+", "", reference_number.strip().upper())
        norm_acc = re.sub(r"\s+", "", (account_identifier or "SHARED").strip().upper())
        raw = f"REF|{str(company_id)}|{norm_acc}|{norm_ref}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_fallback(
        company_id: UUID,
        account_identifier: Optional[str],
        txn_date: date,
        amount: Decimal,
        currency: str,
        txn_type: str,
        narration: str,
        balance: Optional[Decimal] = None,
    ) -> str:
        """Generate deterministic fallback hash when no explicit reference number is available."""
        norm_acc = re.sub(r"\s+", "", (account_identifier or "UNKNOWN").strip().upper())
        norm_narr = re.sub(r"\s+", " ", narration.strip().lower())
        norm_amt = f"{amount:.2f}"
        norm_bal = f"{balance:.2f}" if balance is not None else "NOBAL"
        raw = f"FALLBACK|{str(company_id)}|{norm_acc}|{txn_date.isoformat()}|{norm_amt}|{currency.upper()}|{txn_type.upper()}|{norm_bal}|{norm_narr}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def compute(
        cls,
        company_id: UUID,
        reference_number: Optional[str],
        account_identifier: Optional[str],
        txn_date: date,
        amount: Decimal,
        currency: str,
        txn_type: str,
        narration: str,
        balance: Optional[Decimal] = None,
    ) -> str:
        """Compute primary hash if reference is valid; otherwise compute fallback hash."""
        if cls.is_valid_reference(reference_number):
            return cls.compute_primary(company_id, reference_number, account_identifier)
        return cls.compute_fallback(
            company_id=company_id,
            account_identifier=account_identifier,
            txn_date=txn_date,
            amount=amount,
            currency=currency,
            txn_type=txn_type,
            narration=narration,
            balance=balance,
        )


@dataclass
class ImportBatch:
    """Entity representing a bank statement file ingestion batch."""

    id: UUID
    company_id: UUID
    file_name: str
    file_hash: str
    storage_key: Optional[str] = None
    total_rows: int = 0
    imported_count: int = 0
    failed_count: int = 0
    duplicate_count: int = 0
    status: ImportBatchStatus = ImportBatchStatus.PENDING
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None

    def mark_completed(self, imported: int, failed: int, duplicates: int) -> None:
        """Finalize batch status to COMPLETED."""
        self.imported_count = imported
        self.failed_count = failed
        self.duplicate_count = duplicates
        self.status = ImportBatchStatus.COMPLETED

    def mark_failed(self, error_message: str) -> None:
        """Mark batch as FAILED with actionable diagnostic message."""
        self.status = ImportBatchStatus.FAILED
        self.error_message = error_message


@dataclass
class BankTransaction:
    """Raw bank statement transaction line item (source truth: credits and debits)."""

    id: UUID
    company_id: UUID
    batch_id: UUID
    transaction_date: date
    amount: Money
    transaction_type: TransactionType
    narration: str
    reference_number: Optional[str] = None
    value_date: Optional[date] = None
    bank_account_number: Optional[str] = None
    counterparty_name: Optional[str] = None
    balance: Optional[Money] = None
    raw_row: Dict[str, Any] = field(default_factory=dict)
    deduplication_hash: str = ""
    created_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        """Validate raw bank transaction invariants."""
        if self.amount.is_zero() or self.amount.is_negative():
            raise FinancialInvariantError(
                f"Bank transaction amount must be positive. Received {self.amount.amount}."
            )
        if not self.deduplication_hash:
            self.deduplication_hash = TransactionFingerprint.compute(
                company_id=self.company_id,
                reference_number=self.reference_number,
                account_identifier=self.bank_account_number,
                txn_date=self.transaction_date,
                amount=self.amount.amount,
                currency=self.amount.currency,
                txn_type=self.transaction_type.value,
                narration=self.narration,
                balance=self.balance.amount if self.balance else None,
            )

    @property
    def is_credit(self) -> bool:
        """Check if transaction is an inflow (credit)."""
        return self.transaction_type == TransactionType.CREDIT

    @property
    def is_debit(self) -> bool:
        """Check if transaction is an outflow (debit)."""
        return self.transaction_type == TransactionType.DEBIT

    def to_payment_candidate(
        self,
        payment_id: UUID,
        source: PaymentSource = PaymentSource.BANK_STATEMENT_CSV,
    ) -> "Payment":
        """Convert a credit transaction into a normalized receivable payment candidate.

        Only CREDIT transactions can become receivable payments. Debits raise DomainError.
        """
        if not self.is_credit:
            raise DomainError(
                f"Cannot create a receivable Payment from a {self.transaction_type.value} transaction. "
                "Only incoming CREDIT transactions represent receivable payments."
            )

        return Payment(
            id=payment_id,
            company_id=self.company_id,
            transaction_date=self.transaction_date,
            amount=self.amount,
            allocated_amount=Money.zero(self.amount.currency),
            unallocated_amount=self.amount,
            currency=self.amount.currency,
            narration=self.narration,
            reference_number=self.reference_number,
            bank_account_number=self.bank_account_number,
            batch_id=self.batch_id,
            bank_transaction_id=self.id,
            source=source,
            status=PaymentStatus.UNRECONCILED,
        )


@dataclass
class Payment:
    """Normalized incoming receivable payment candidate aggregate root.

    Invariant rules:
    1. amount > 0
    2. allocated_amount >= 0, unallocated_amount >= 0
    3. allocated_amount + unallocated_amount == amount (exact balance conservation)
    4. Currency must match across all monetary components.
    """

    id: UUID
    company_id: UUID
    transaction_date: date
    amount: Money
    allocated_amount: Money
    unallocated_amount: Money
    currency: str = "INR"
    narration: str = ""
    reference_number: Optional[str] = None
    bank_account_number: Optional[str] = None
    batch_id: Optional[UUID] = None
    bank_transaction_id: Optional[UUID] = None
    payer_raw_name: Optional[str] = None
    payer_raw_identifier: Optional[str] = None
    status: PaymentStatus = PaymentStatus.UNRECONCILED
    source: PaymentSource = PaymentSource.BANK_STATEMENT_CSV
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        """Enforce strict financial invariants upon instantiation."""
        # Non-positive amount check
        if self.amount.is_zero() or self.amount.is_negative():
            raise FinancialInvariantError(
                f"Payment amount must be strictly positive. Received: {self.amount.amount}."
            )

        # Currency consistency
        if (
            self.amount.currency != self.currency
            or self.allocated_amount.currency != self.currency
            or self.unallocated_amount.currency != self.currency
        ):
            raise FinancialInvariantError(
                f"Currency mismatch on payment: entity={self.currency}, amount={self.amount.currency}, "
                f"allocated={self.allocated_amount.currency}, unallocated={self.unallocated_amount.currency}."
            )

        # Non-negative sub-balances
        if self.allocated_amount.is_negative():
            raise FinancialInvariantError(
                f"Allocated amount cannot be negative. Received: {self.allocated_amount.amount}."
            )
        if self.unallocated_amount.is_negative():
            raise FinancialInvariantError(
                f"Unallocated amount cannot be negative. Received: {self.unallocated_amount.amount}."
            )

        # Exact balance conservation
        total_balance = self.allocated_amount + self.unallocated_amount
        if total_balance != self.amount:
            raise FinancialInvariantError(
                f"Payment balance conservation violated: allocated ({self.allocated_amount.amount}) + "
                f"unallocated ({self.unallocated_amount.amount}) != total amount ({self.amount.amount})."
            )

    def record_allocation(self, allocation: Money) -> None:
        """Record an allocation against an invoice, updating balances and status."""
        if self.status == PaymentStatus.IGNORED:
            raise DomainError("Cannot allocate an IGNORED payment. Unmark ignored first.")

        if allocation.currency != self.currency:
            raise FinancialInvariantError(
                f"Allocation currency mismatch: {allocation.currency} != {self.currency}."
            )

        if allocation.is_zero() or allocation.is_negative():
            raise FinancialInvariantError(
                f"Allocation amount must be strictly positive. Received: {allocation.amount}."
            )

        if allocation > self.unallocated_amount:
            raise FinancialInvariantError(
                f"Allocation amount ({allocation.amount}) exceeds unallocated balance ({self.unallocated_amount.amount})."
            )

        new_allocated = self.allocated_amount + allocation
        new_unallocated = self.unallocated_amount - allocation

        self.allocated_amount = new_allocated
        self.unallocated_amount = new_unallocated

        if self.unallocated_amount.is_zero():
            self.status = PaymentStatus.RECONCILED
        else:
            self.status = PaymentStatus.PARTIALLY_RECONCILED

    def unallocate(self, amount_to_revert: Money) -> None:
        """Revert a previously recorded allocation."""
        if amount_to_revert.currency != self.currency:
            raise FinancialInvariantError(
                f"Revert currency mismatch: {amount_to_revert.currency} != {self.currency}."
            )

        if amount_to_revert.is_zero() or amount_to_revert.is_negative():
            raise FinancialInvariantError(
                f"Amount to revert must be strictly positive. Received: {amount_to_revert.amount}."
            )

        if amount_to_revert > self.allocated_amount:
            raise FinancialInvariantError(
                f"Amount to revert ({amount_to_revert.amount}) exceeds allocated balance ({self.allocated_amount.amount})."
            )

        self.allocated_amount = self.allocated_amount - amount_to_revert
        self.unallocated_amount = self.unallocated_amount + amount_to_revert

        if self.allocated_amount.is_zero():
            self.status = PaymentStatus.UNRECONCILED
        else:
            self.status = PaymentStatus.PARTIALLY_RECONCILED

    def mark_ignored(self, reason: str) -> None:
        """Mark payment as IGNORED by accountant."""
        if not reason or not reason.strip():
            raise ValidationError("A reason must be provided when ignoring a payment.")

        if not self.allocated_amount.is_zero():
            raise DomainError(
                f"Cannot ignore payment with active allocations ({self.allocated_amount.amount}). "
                "De-allocate first before ignoring."
            )

        self.status = PaymentStatus.IGNORED
        self.notes = f"[IGNORED]: {reason.strip()}" + (f" | {self.notes}" if self.notes else "")

    def unmark_ignored(self) -> None:
        """Restore an IGNORED payment back to UNRECONCILED."""
        if self.status != PaymentStatus.IGNORED:
            raise DomainError(f"Payment is not in IGNORED status (current: {self.status.value}).")

        self.status = PaymentStatus.UNRECONCILED
