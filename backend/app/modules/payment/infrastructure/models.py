"""SQLAlchemy 2.0 ORM models for Statement Import Batches, Raw Bank Transactions, and Normalized Payments.

Enforces:
- Tenant scoping with foreign key cascade to companies.id
- Credit and Debit segregation (Raw Bank Transactions != Payments)
- Exact decimal precision on all monetary columns (Numeric 14, 2)
- Balance conservation constraints: allocated_amount + unallocated_amount == amount
- Deduplication unique constraints
"""

from datetime import datetime, timezone
import uuid
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.shared.infrastructure.db_types import GUID


class ImportBatchModel(Base):
    """SQLAlchemy model for bank statement file ingestion batches."""

    __tablename__ = "import_batches"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        GUID(),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name = Column(String(255), nullable=False)
    file_hash = Column(String(64), nullable=False, index=True)
    storage_key = Column(String(512), nullable=True)
    total_rows = Column(Integer, nullable=False, default=0)
    imported_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    duplicate_count = Column(Integer, nullable=False, default=0)
    status = Column(String(50), nullable=False, default="PENDING", index=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    transactions = relationship(
        "BankTransactionModel",
        back_populates="batch",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("company_id", "file_hash", name="uq_import_batches_company_hash"),
        Index("idx_import_batches_company_status", "company_id", "status"),
        Index("idx_import_batches_company_created", "company_id", "created_at"),
    )


class BankTransactionModel(Base):
    """SQLAlchemy model for raw bank statement transaction line items (source truth)."""

    __tablename__ = "bank_transactions"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        GUID(),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    batch_id = Column(
        GUID(),
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transaction_date = Column(Date, nullable=False)
    value_date = Column(Date, nullable=True)
    amount = Column(Numeric(14, 2), nullable=False)
    transaction_type = Column(String(10), nullable=False)  # CREDIT or DEBIT
    narration = Column(Text, nullable=False)
    reference_number = Column(String(255), nullable=True)
    bank_account_number = Column(String(100), nullable=True)
    counterparty_name = Column(String(255), nullable=True)
    balance = Column(Numeric(14, 2), nullable=True)
    raw_row = Column(JSON, nullable=True)
    deduplication_hash = Column(String(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    batch = relationship("ImportBatchModel", back_populates="transactions")
    payment = relationship("PaymentModel", back_populates="bank_transaction", uselist=False)

    __table_args__ = (
        UniqueConstraint("company_id", "deduplication_hash", name="uq_bank_transactions_company_dedup"),
        CheckConstraint("amount > 0", name="chk_bank_txn_amount_positive"),
        Index("idx_bank_txns_company_date", "company_id", "transaction_date"),
        Index("idx_bank_txns_company_type", "company_id", "transaction_type"),
        Index("idx_bank_txns_company_ref", "company_id", "reference_number"),
        Index("idx_bank_txns_company_batch", "company_id", "batch_id"),
    )


class PaymentModel(Base):
    """SQLAlchemy model for normalized incoming receivable payment candidates."""

    __tablename__ = "payments"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        GUID(),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    batch_id = Column(
        GUID(),
        ForeignKey("import_batches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    bank_transaction_id = Column(
        GUID(),
        ForeignKey("bank_transactions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    transaction_date = Column(Date, nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    allocated_amount = Column(Numeric(14, 2), nullable=False, default=0.00)
    unallocated_amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="INR")
    narration = Column(Text, nullable=False)
    reference_number = Column(String(255), nullable=True)
    bank_account_number = Column(String(100), nullable=True)
    payer_raw_name = Column(String(255), nullable=True)
    payer_raw_identifier = Column(String(255), nullable=True)
    status = Column(String(50), nullable=False, default="UNRECONCILED", index=True)
    source = Column(String(50), nullable=False, default="BANK_STATEMENT_CSV")
    notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    batch = relationship("ImportBatchModel", foreign_keys=[batch_id], lazy="joined")
    bank_transaction = relationship("BankTransactionModel", foreign_keys=[bank_transaction_id], lazy="joined")

    __table_args__ = (
        CheckConstraint("amount > 0", name="chk_payment_amount_positive"),
        CheckConstraint("allocated_amount >= 0", name="chk_payment_allocated_non_negative"),
        CheckConstraint("unallocated_amount >= 0", name="chk_payment_unallocated_non_negative"),
        CheckConstraint(
            "allocated_amount + unallocated_amount = amount",
            name="chk_payment_balance",
        ),
        Index("idx_payments_company_status", "company_id", "status"),
        Index("idx_payments_company_date", "company_id", "transaction_date"),
        Index("idx_payments_company_ref", "company_id", "reference_number"),
        Index("idx_payments_company_source", "company_id", "source"),
    )
