"""Application use cases for Payment and Bank Statement Management.

Implements Clean Architecture orchestration:
- Multi-tenant company context enforcement
- 3 Deduplication gates (File hash, Intra-batch, Database)
- Savepoint isolation for partial batch persistence
- Credit vs Debit segregation (Only credits create normalized receivable Payments)
- Invariant verification and financial state protection
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.payment.application.csv_parser import CSVBankStatementParser
from app.modules.payment.application.ports import (
    BankStatementParser,
    LocalStorageService,
    StorageService,
)
from app.modules.payment.domain.entities import (
    BankStatementImportResult,
    BankTransaction,
    CSVRowError,
    ImportBatch,
    ImportBatchStatus,
    ParsedBankTransaction,
    Payment,
    PaymentSource,
    PaymentStatus,
    TransactionFingerprint,
    TransactionType,
)
from app.modules.payment.infrastructure.repositories import (
    BankTransactionRepository,
    ImportBatchRepository,
    PaymentRepository,
)
from app.shared.domain.money import Money
from app.shared.exceptions import (
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)


class ImportBankStatementCSVUseCase:
    """Orchestrates secure, multi-tenant bank statement CSV ingestion."""

    def __init__(
        self,
        db: Session,
        storage_service: Optional[StorageService] = None,
        parser: Optional[BankStatementParser] = None,
    ) -> None:
        self.db = db
        self.storage = storage_service or LocalStorageService()
        self.parser = parser or CSVBankStatementParser()
        self.batch_repo = ImportBatchRepository(db)
        self.bank_txn_repo = BankTransactionRepository(db)
        self.payment_repo = PaymentRepository(db)

    def execute(
        self,
        company_id: UUID,
        file_content: bytes,
        file_name: str,
        bank_account_number: Optional[str] = None,
        default_currency: str = "INR",
        skip_duplicates: bool = True,
    ) -> BankStatementImportResult:
        """Execute bank statement ingestion workflow."""
        if not file_content:
            raise ValidationError("Uploaded file content cannot be empty.")

        ext = Path(file_name).suffix.lower()
        if ext not in settings.ALLOWED_STATEMENT_EXTENSIONS:
            raise ValidationError(
                f"File format '{ext}' is not supported. Allowed extensions: {', '.join(settings.ALLOWED_STATEMENT_EXTENSIONS)}."
            )

        if len(file_content) > settings.MAX_UPLOAD_SIZE_BYTES:
            raise ValidationError(
                f"File size ({len(file_content)} bytes) exceeds maximum limit of {settings.MAX_UPLOAD_SIZE_BYTES} bytes."
            )

        # Gate 1: File-level SHA-256 content deduplication
        file_hash = hashlib.sha256(file_content).hexdigest()
        existing_batch = self.batch_repo.get_by_hash(file_hash=file_hash, company_id=company_id)
        if existing_batch:
            if existing_batch.status != ImportBatchStatus.FAILED:
                created_date = existing_batch.created_at.date().isoformat() if existing_batch.created_at else "previously"
                raise ConflictError(
                    f"This bank statement file ('{file_name}') has already been imported in batch {existing_batch.id} on {created_date}."
                )
            else:
                # Prior attempt failed; delete the failed batch record so retry proceeds cleanly
                self.batch_repo.delete(batch_id=existing_batch.id, company_id=company_id)

        # Persist raw file to tenant-isolated storage
        storage_key, _, _ = self.storage.save_file(
            content=file_content,
            filename=file_name,
            company_id=company_id,
        )

        batch_id = uuid4()
        batch = ImportBatch(
            id=batch_id,
            company_id=company_id,
            file_name=file_name,
            file_hash=file_hash,
            storage_key=storage_key,
            total_rows=0,
            imported_count=0,
            failed_count=0,
            duplicate_count=0,
            status=ImportBatchStatus.PROCESSING,
        )
        try:
            self.batch_repo.create(batch)
        except IntegrityError:
            self.db.rollback()
            raise ConflictError(
                f"This bank statement file ('{file_name}') is already being processed or has been imported."
            )

        # Parse CSV content
        transactions, parse_errors = self.parser.parse(
            content=file_content,
            default_currency=default_currency,
            bank_account_number=bank_account_number,
        )

        # If schema-level error occurred, mark batch as FAILED and abort
        if any(err.field in {"schema", "file"} for err in parse_errors):
            batch.mark_failed(parse_errors[0].message)
            self.batch_repo.update(batch)
            self.db.commit()
            raise ValidationError(parse_errors[0].message)

        imported_payment_ids: List[UUID] = []
        errors: List[CSVRowError] = list(parse_errors)
        seen_in_batch: set = set()
        skipped_duplicates = 0
        skipped_debits = 0

        # Process each parsed transaction with savepoint isolation
        for txn in transactions:
            # Generate deterministic deduplication hash
            dedup_hash = TransactionFingerprint.compute(
                company_id=company_id,
                reference_number=txn.reference_number,
                account_identifier=bank_account_number or txn.bank_account_number,
                txn_date=txn.transaction_date,
                amount=txn.amount,
                currency=txn.currency,
                txn_type=txn.transaction_type.value,
                narration=txn.narration,
                balance=txn.balance,
            )

            # Gate 2: Intra-Batch Deduplication
            if dedup_hash in seen_in_batch:
                errors.append(
                    CSVRowError(
                        row_number=txn.row_number,
                        field="reference_number",
                        message=f"Duplicate transaction within statement batch (date: {txn.transaction_date}, amount: {txn.amount}).",
                    )
                )
                skipped_duplicates += 1
                continue

            # Gate 3: Database Idempotency Check
            existing_db_txn = self.bank_txn_repo.get_by_dedup_hash(
                dedup_hash=dedup_hash, company_id=company_id
            )
            if existing_db_txn:
                if skip_duplicates:
                    skipped_duplicates += 1
                    continue
                else:
                    errors.append(
                        CSVRowError(
                            row_number=txn.row_number,
                            field="reference_number",
                            message=f"Transaction with reference '{txn.reference_number or dedup_hash[:12]}' already exists in database.",
                        )
                    )
                    continue

            # Construct BankTransaction domain entity
            try:
                txn_money = Money(txn.amount, txn.currency)
                balance_money = Money(txn.balance, txn.currency) if txn.balance is not None else None
                bank_txn = BankTransaction(
                    id=uuid4(),
                    company_id=company_id,
                    batch_id=batch_id,
                    transaction_date=txn.transaction_date,
                    amount=txn_money,
                    transaction_type=txn.transaction_type,
                    narration=txn.narration,
                    reference_number=txn.reference_number,
                    value_date=txn.value_date,
                    bank_account_number=bank_account_number or txn.bank_account_number,
                    counterparty_name=txn.counterparty_name,
                    balance=balance_money,
                    raw_row=txn.raw_data,
                    deduplication_hash=dedup_hash,
                )
            except Exception as ex:
                errors.append(
                    CSVRowError(
                        row_number=txn.row_number,
                        field="amount",
                        message=f"Invariant validation failed: {str(ex)}",
                    )
                )
                continue

            # Atomic row persistence wrapped in a SAVEPOINT
            try:
                with self.db.begin_nested():
                    self.bank_txn_repo.create(bank_txn)

                    # Only CREDIT transactions instantiate receivable Payment entities
                    if bank_txn.is_credit:
                        payment = bank_txn.to_payment_candidate(payment_id=uuid4())
                        self.payment_repo.create(payment)
                        imported_payment_ids.append(payment.id)
                    else:
                        skipped_debits += 1

                seen_in_batch.add(dedup_hash)
            except IntegrityError:
                # Concurrent race condition or deduplication constraint violation
                if skip_duplicates:
                    skipped_duplicates += 1
                else:
                    errors.append(
                        CSVRowError(
                            row_number=txn.row_number,
                            field="reference_number",
                            message=f"Duplicate transaction '{txn.reference_number or dedup_hash[:12]}'.",
                        )
                    )
            except Exception as ex:
                errors.append(
                    CSVRowError(
                        row_number=txn.row_number,
                        field="database",
                        message=f"Error persisting row {txn.row_number}: {str(ex)}",
                    )
                )

        # Batch row conservation accounting
        total_rows_evaluated = len(transactions) + len(parse_errors)
        batch.total_rows = total_rows_evaluated
        batch.mark_completed(
            imported=len(imported_payment_ids),
            failed=len(errors),
            duplicates=skipped_duplicates,
        )
        self.batch_repo.update(batch)
        self.db.commit()

        return BankStatementImportResult(
            batch_id=batch_id,
            file_name=file_name,
            total_rows=total_rows_evaluated,
            imported_payments_count=len(imported_payment_ids),
            skipped_debits_count=skipped_debits,
            skipped_duplicates_count=skipped_duplicates,
            failed_rows_count=len(errors),
            errors=errors,
            imported_payment_ids=imported_payment_ids,
        )


class ListPaymentsUseCase:
    """Use case to list normalized incoming receivable payments with filtering and pagination."""

    def __init__(self, db: Session) -> None:
        self.payment_repo = PaymentRepository(db)

    def execute(
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
        """Execute payment list query strictly scoped to tenant."""
        return self.payment_repo.list_by_company(
            company_id=company_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            min_amount=min_amount,
            max_amount=max_amount,
            search=search,
            page=page,
            page_size=page_size,
        )


class GetPaymentDetailUseCase:
    """Use case to fetch payment details with strict tenant verification."""

    def __init__(self, db: Session) -> None:
        self.payment_repo = PaymentRepository(db)

    def execute(self, payment_id: UUID, company_id: UUID) -> Payment:
        """Fetch payment or fail closed with 404."""
        payment = self.payment_repo.get_by_id(payment_id=payment_id, company_id=company_id)
        if not payment:
            raise NotFoundError("Payment", payment_id)
        return payment


class CreateManualPaymentUseCase:
    """Use case to register a direct manual bank receipt payment."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.payment_repo = PaymentRepository(db)

    def execute(
        self,
        company_id: UUID,
        transaction_date: date,
        amount: Decimal,
        currency: str = "INR",
        narration: str = "Manual payment receipt",
        reference_number: Optional[str] = None,
        bank_account_number: Optional[str] = None,
        payer_raw_name: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Payment:
        """Create manual payment with balance conservation and duplicate check."""
        if reference_number and reference_number.strip():
            existing = self.payment_repo.get_by_reference(
                reference_number=reference_number.strip(), company_id=company_id
            )
            if existing:
                raise ConflictError(
                    f"A payment with reference '{reference_number.strip()}' already exists."
                )

        amount_money = Money(amount, currency)
        payment_id = uuid4()
        payment = Payment(
            id=payment_id,
            company_id=company_id,
            transaction_date=transaction_date,
            amount=amount_money,
            allocated_amount=Money.zero(currency),
            unallocated_amount=amount_money,
            currency=currency,
            narration=narration,
            reference_number=reference_number.strip() if reference_number else None,
            bank_account_number=bank_account_number.strip() if bank_account_number else None,
            payer_raw_name=payer_raw_name.strip() if payer_raw_name else None,
            status=PaymentStatus.UNRECONCILED,
            source=PaymentSource.MANUAL,
            notes=notes,
        )

        created_payment = self.payment_repo.create(payment)
        self.db.commit()
        return created_payment


class IgnorePaymentUseCase:
    """Use case to mark an unmatchable payment as IGNORED."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.payment_repo = PaymentRepository(db)

    def execute(self, payment_id: UUID, company_id: UUID, reason: str) -> Payment:
        """Mark payment as IGNORED."""
        payment = self.payment_repo.get_by_id(payment_id=payment_id, company_id=company_id)
        if not payment:
            raise NotFoundError("Payment", payment_id)

        payment.mark_ignored(reason=reason)
        updated_payment = self.payment_repo.update(payment)
        self.db.commit()
        return updated_payment


class UnignorePaymentUseCase:
    """Use case to restore an IGNORED payment back to UNRECONCILED."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.payment_repo = PaymentRepository(db)

    def execute(self, payment_id: UUID, company_id: UUID) -> Payment:
        """Restore IGNORED payment."""
        payment = self.payment_repo.get_by_id(payment_id=payment_id, company_id=company_id)
        if not payment:
            raise NotFoundError("Payment", payment_id)

        payment.unmark_ignored()
        updated_payment = self.payment_repo.update(payment)
        self.db.commit()
        return updated_payment


class ListImportBatchesUseCase:
    """Use case to list statement import batches for a company."""

    def __init__(self, db: Session) -> None:
        self.batch_repo = ImportBatchRepository(db)

    def execute(
        self,
        company_id: UUID,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[ImportBatch], int]:
        """List batches for company."""
        return self.batch_repo.list_by_company(
            company_id=company_id, page=page, page_size=page_size
        )


class GetImportBatchDetailUseCase:
    """Use case to fetch an import batch with its bank transactions."""

    def __init__(self, db: Session) -> None:
        self.batch_repo = ImportBatchRepository(db)
        self.bank_txn_repo = BankTransactionRepository(db)

    def execute(
        self, batch_id: UUID, company_id: UUID
    ) -> Tuple[ImportBatch, List[BankTransaction]]:
        """Fetch batch and associated raw transactions, fail closed with 404."""
        batch = self.batch_repo.get_by_id(batch_id=batch_id, company_id=company_id)
        if not batch:
            raise NotFoundError("ImportBatch", batch_id)

        transactions = self.bank_txn_repo.list_by_batch(
            batch_id=batch_id, company_id=company_id
        )
        return batch, transactions


class ListBankTransactionsUseCase:
    """Use case to list raw bank statement transactions (both credit and debit)."""

    def __init__(self, db: Session) -> None:
        self.bank_txn_repo = BankTransactionRepository(db)

    def execute(
        self,
        company_id: UUID,
        txn_type: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[BankTransaction], int]:
        """List raw bank transactions for company."""
        return self.bank_txn_repo.list_by_company(
            company_id=company_id,
            txn_type=txn_type,
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=page_size,
        )
