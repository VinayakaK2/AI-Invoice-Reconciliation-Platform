"""Repository implementations for Statement Batches, Bank Transactions, and Payments.

Handles SQLAlchemy database operations, maps to pure domain entities,
and enforces multi-tenant company isolation across all queries and commands.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.payment.domain.entities import (
    BankTransaction,
    ImportBatch,
    ImportBatchStatus,
    Payment,
    PaymentSource,
    PaymentStatus,
    TransactionType,
)
from app.modules.payment.infrastructure.models import (
    BankTransactionModel,
    ImportBatchModel,
    PaymentModel,
)
from app.shared.domain.money import Money


class ImportBatchRepository:
    """Repository handling database persistence for statement ingestion batches."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _to_domain(self, model: ImportBatchModel) -> ImportBatch:
        """Map ORM model to pure domain entity."""
        return ImportBatch(
            id=model.id,
            company_id=model.company_id,
            file_name=model.file_name,
            file_hash=model.file_hash,
            storage_key=model.storage_key,
            total_rows=model.total_rows,
            imported_count=model.imported_count,
            failed_count=model.failed_count,
            duplicate_count=model.duplicate_count,
            status=ImportBatchStatus(model.status),
            error_message=model.error_message,
            created_at=model.created_at,
        )

    def create(self, batch: ImportBatch) -> ImportBatch:
        """Persist a new statement import batch record."""
        model = ImportBatchModel(
            id=batch.id,
            company_id=batch.company_id,
            file_name=batch.file_name,
            file_hash=batch.file_hash,
            storage_key=batch.storage_key,
            total_rows=batch.total_rows,
            imported_count=batch.imported_count,
            failed_count=batch.failed_count,
            duplicate_count=batch.duplicate_count,
            status=batch.status.value,
            error_message=batch.error_message,
        )
        self.db.add(model)
        self.db.flush()
        return self._to_domain(model)

    def get_by_id(self, batch_id: UUID, company_id: UUID) -> Optional[ImportBatch]:
        """Fetch batch by ID strictly scoped to company."""
        model = (
            self.db.query(ImportBatchModel)
            .filter(
                ImportBatchModel.id == batch_id,
                ImportBatchModel.company_id == company_id,
            )
            .first()
        )
        return self._to_domain(model) if model else None

    def get_by_hash(self, file_hash: str, company_id: UUID) -> Optional[ImportBatch]:
        """Fetch batch by file content SHA-256 hash strictly scoped to company."""
        model = (
            self.db.query(ImportBatchModel)
            .filter(
                ImportBatchModel.company_id == company_id,
                ImportBatchModel.file_hash == file_hash,
            )
            .first()
        )
        return self._to_domain(model) if model else None

    def update(self, batch: ImportBatch) -> ImportBatch:
        """Update batch progress and status."""
        model = (
            self.db.query(ImportBatchModel)
            .filter(
                ImportBatchModel.id == batch.id,
                ImportBatchModel.company_id == batch.company_id,
            )
            .first()
        )
        if not model:
            raise ValueError(f"Import batch {batch.id} not found for company {batch.company_id}")

        model.total_rows = batch.total_rows
        model.imported_count = batch.imported_count
        model.failed_count = batch.failed_count
        model.duplicate_count = batch.duplicate_count
        model.status = batch.status.value
        model.error_message = batch.error_message
        self.db.flush()
        return self._to_domain(model)

    def delete(self, batch_id: UUID, company_id: UUID) -> bool:
        """Delete import batch strictly scoped to company."""
        model = (
            self.db.query(ImportBatchModel)
            .filter(
                ImportBatchModel.id == batch_id,
                ImportBatchModel.company_id == company_id,
            )
            .first()
        )
        if model:
            self.db.delete(model)
            self.db.flush()
            return True
        return False

    def list_by_company(
        self,
        company_id: UUID,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[ImportBatch], int]:
        """Return paginated list of import batches for a company."""
        query = (
            self.db.query(ImportBatchModel)
            .filter(ImportBatchModel.company_id == company_id)
            .order_by(ImportBatchModel.created_at.desc())
        )
        total = query.count()
        models = query.offset((page - 1) * page_size).limit(page_size).all()
        return [self._to_domain(m) for m in models], total


class BankTransactionRepository:
    """Repository handling database persistence for raw bank statement transactions."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _to_domain(self, model: BankTransactionModel) -> BankTransaction:
        """Map ORM model to pure domain entity."""
        return BankTransaction(
            id=model.id,
            company_id=model.company_id,
            batch_id=model.batch_id,
            transaction_date=model.transaction_date,
            value_date=model.value_date,
            amount=Money(model.amount),
            transaction_type=TransactionType(model.transaction_type),
            narration=model.narration,
            reference_number=model.reference_number,
            bank_account_number=model.bank_account_number,
            counterparty_name=model.counterparty_name,
            balance=Money(model.balance) if model.balance is not None else None,
            raw_row=model.raw_row or {},
            deduplication_hash=model.deduplication_hash,
            created_at=model.created_at,
        )

    def create(self, txn: BankTransaction) -> BankTransaction:
        """Persist a raw bank transaction record."""
        model = BankTransactionModel(
            id=txn.id,
            company_id=txn.company_id,
            batch_id=txn.batch_id,
            transaction_date=txn.transaction_date,
            value_date=txn.value_date,
            amount=txn.amount.amount,
            transaction_type=txn.transaction_type.value,
            narration=txn.narration,
            reference_number=txn.reference_number,
            bank_account_number=txn.bank_account_number,
            counterparty_name=txn.counterparty_name,
            balance=txn.balance.amount if txn.balance else None,
            raw_row=txn.raw_row,
            deduplication_hash=txn.deduplication_hash,
        )
        self.db.add(model)
        self.db.flush()
        return self._to_domain(model)

    def get_by_id(self, txn_id: UUID, company_id: UUID) -> Optional[BankTransaction]:
        """Fetch bank transaction by ID strictly scoped to company."""
        model = (
            self.db.query(BankTransactionModel)
            .filter(
                BankTransactionModel.id == txn_id,
                BankTransactionModel.company_id == company_id,
            )
            .first()
        )
        return self._to_domain(model) if model else None

    def get_by_dedup_hash(self, dedup_hash: str, company_id: UUID) -> Optional[BankTransaction]:
        """Check if deduplication hash already exists for this company."""
        model = (
            self.db.query(BankTransactionModel)
            .filter(
                BankTransactionModel.company_id == company_id,
                BankTransactionModel.deduplication_hash == dedup_hash,
            )
            .first()
        )
        return self._to_domain(model) if model else None

    def list_by_batch(self, batch_id: UUID, company_id: UUID) -> List[BankTransaction]:
        """Fetch all transactions belonging to an import batch."""
        models = (
            self.db.query(BankTransactionModel)
            .filter(
                BankTransactionModel.batch_id == batch_id,
                BankTransactionModel.company_id == company_id,
            )
            .order_by(BankTransactionModel.transaction_date.asc())
            .all()
        )
        return [self._to_domain(m) for m in models]

    def list_by_company(
        self,
        company_id: UUID,
        txn_type: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[BankTransaction], int]:
        """Return paginated list of bank transactions filtered by type and date range."""
        query = self.db.query(BankTransactionModel).filter(BankTransactionModel.company_id == company_id)

        if txn_type:
            query = query.filter(BankTransactionModel.transaction_type == txn_type.upper())
        if start_date:
            query = query.filter(BankTransactionModel.transaction_date >= start_date)
        if end_date:
            query = query.filter(BankTransactionModel.transaction_date <= end_date)

        total = query.count()
        models = (
            query.order_by(BankTransactionModel.transaction_date.desc(), BankTransactionModel.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return [self._to_domain(m) for m in models], total


class PaymentRepository:
    """Repository handling database persistence for normalized incoming receivable payments."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _to_domain(self, model: PaymentModel) -> Payment:
        """Map ORM model to pure domain entity."""
        return Payment(
            id=model.id,
            company_id=model.company_id,
            transaction_date=model.transaction_date,
            amount=Money(model.amount, model.currency),
            allocated_amount=Money(model.allocated_amount, model.currency),
            unallocated_amount=Money(model.unallocated_amount, model.currency),
            currency=model.currency,
            narration=model.narration,
            reference_number=model.reference_number,
            bank_account_number=model.bank_account_number,
            batch_id=model.batch_id,
            bank_transaction_id=model.bank_transaction_id,
            payer_raw_name=model.payer_raw_name,
            payer_raw_identifier=model.payer_raw_identifier,
            status=PaymentStatus(model.status),
            source=PaymentSource(model.source),
            notes=model.notes,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def create(self, payment: Payment) -> Payment:
        """Persist a normalized payment entity."""
        model = PaymentModel(
            id=payment.id,
            company_id=payment.company_id,
            batch_id=payment.batch_id,
            bank_transaction_id=payment.bank_transaction_id,
            transaction_date=payment.transaction_date,
            amount=payment.amount.amount,
            allocated_amount=payment.allocated_amount.amount,
            unallocated_amount=payment.unallocated_amount.amount,
            currency=payment.currency,
            narration=payment.narration,
            reference_number=payment.reference_number,
            bank_account_number=payment.bank_account_number,
            payer_raw_name=payment.payer_raw_name,
            payer_raw_identifier=payment.payer_raw_identifier,
            status=payment.status.value,
            source=payment.source.value,
            notes=payment.notes,
        )
        self.db.add(model)
        self.db.flush()
        return self._to_domain(model)

    def get_by_id(self, payment_id: UUID, company_id: UUID) -> Optional[Payment]:
        """Fetch payment by ID strictly scoped to company."""
        model = (
            self.db.query(PaymentModel)
            .filter(
                PaymentModel.id == payment_id,
                PaymentModel.company_id == company_id,
            )
            .first()
        )
        return self._to_domain(model) if model else None

    def get_by_reference(self, reference_number: str, company_id: UUID) -> Optional[Payment]:
        """Fetch payment by reference number within company."""
        model = (
            self.db.query(PaymentModel)
            .filter(
                PaymentModel.company_id == company_id,
                PaymentModel.reference_number == reference_number,
            )
            .first()
        )
        return self._to_domain(model) if model else None

    def get_by_bank_transaction_id(
        self, bank_transaction_id: UUID, company_id: UUID
    ) -> Optional[Payment]:
        """Fetch payment linked 1-to-1 to a raw bank transaction."""
        model = (
            self.db.query(PaymentModel)
            .filter(
                PaymentModel.company_id == company_id,
                PaymentModel.bank_transaction_id == bank_transaction_id,
            )
            .first()
        )
        return self._to_domain(model) if model else None

    def update(self, payment: Payment) -> Payment:
        """Update payment allocations, status, or notes."""
        model = (
            self.db.query(PaymentModel)
            .filter(
                PaymentModel.id == payment.id,
                PaymentModel.company_id == payment.company_id,
            )
            .first()
        )
        if not model:
            raise ValueError(f"Payment {payment.id} not found for company {payment.company_id}")

        model.allocated_amount = payment.allocated_amount.amount
        model.unallocated_amount = payment.unallocated_amount.amount
        model.status = payment.status.value
        model.notes = payment.notes
        self.db.flush()
        return self._to_domain(model)

    def list_by_company(
        self,
        company_id: UUID,
        status: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        min_amount: Optional[Decimal] = None,
        max_amount: Optional[Decimal] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[Payment], int]:
        """Return paginated list of payments matching filters."""
        query = self.db.query(PaymentModel).filter(PaymentModel.company_id == company_id)

        if status:
            query = query.filter(PaymentModel.status == status.upper())
        if start_date:
            query = query.filter(PaymentModel.transaction_date >= start_date)
        if end_date:
            query = query.filter(PaymentModel.transaction_date <= end_date)
        if min_amount is not None:
            query = query.filter(PaymentModel.amount >= min_amount)
        if max_amount is not None:
            query = query.filter(PaymentModel.amount <= max_amount)
        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    PaymentModel.reference_number.ilike(term),
                    PaymentModel.narration.ilike(term),
                    PaymentModel.payer_raw_name.ilike(term),
                )
            )

        total = query.count()
        models = (
            query.order_by(PaymentModel.transaction_date.desc(), PaymentModel.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return [self._to_domain(m) for m in models], total

    def count_by_status(self, company_id: UUID) -> Dict[str, int]:
        """Return count of payments grouped by reconciliation status."""
        counts = (
            self.db.query(PaymentModel.status, func.count(PaymentModel.id))
            .filter(PaymentModel.company_id == company_id)
            .group_by(PaymentModel.status)
            .all()
        )
        return {status: count for status, count in counts}
