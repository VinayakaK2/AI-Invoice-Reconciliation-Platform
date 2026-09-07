"""Phase 12: Create import_batches, bank_transactions, and payments tables.

Revision ID: 0005_phase_12
Revises: 0004_phase_11_archive
Create Date: 2026-09-06 20:30:00.000000

"""

from alembic import op
import sqlalchemy as sa
from app.shared.infrastructure.db_types import GUID

# revision identifiers, used by Alembic.
revision = "0005_phase_12"
down_revision = "0004_phase_11_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create import_batches table
    op.create_table(
        "import_batches",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column(
            "company_id",
            GUID(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=True),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="PENDING"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("company_id", "file_hash", name="uq_import_batches_company_hash"),
    )
    op.create_index(
        "idx_import_batches_company_status",
        "import_batches",
        ["company_id", "status"],
    )
    op.create_index(
        "idx_import_batches_company_created",
        "import_batches",
        ["company_id", "created_at"],
    )

    # 2. Create bank_transactions table (Raw line items - credits & debits)
    op.create_table(
        "bank_transactions",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column(
            "company_id",
            GUID(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            GUID(),
            sa.ForeignKey("import_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("transaction_type", sa.String(length=10), nullable=False),
        sa.Column("narration", sa.Text(), nullable=False),
        sa.Column("reference_number", sa.String(length=255), nullable=True),
        sa.Column("bank_account_number", sa.String(length=100), nullable=True),
        sa.Column("counterparty_name", sa.String(length=255), nullable=True),
        sa.Column("balance", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("raw_row", sa.JSON(), nullable=True),
        sa.Column("deduplication_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "company_id",
            "deduplication_hash",
            name="uq_bank_transactions_company_dedup",
        ),
        sa.CheckConstraint("amount > 0", name="chk_bank_txn_amount_positive"),
    )
    op.create_index(
        "idx_bank_txns_company_date",
        "bank_transactions",
        ["company_id", "transaction_date"],
    )
    op.create_index(
        "idx_bank_txns_company_type",
        "bank_transactions",
        ["company_id", "transaction_type"],
    )
    op.create_index(
        "idx_bank_txns_company_ref",
        "bank_transactions",
        ["company_id", "reference_number"],
    )
    op.create_index(
        "idx_bank_txns_company_batch",
        "bank_transactions",
        ["company_id", "batch_id"],
    )

    # 3. Create payments table (Normalized incoming receivable candidates)
    op.create_table(
        "payments",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column(
            "company_id",
            GUID(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            GUID(),
            sa.ForeignKey("import_batches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "bank_transaction_id",
            GUID(),
            sa.ForeignKey("bank_transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "allocated_amount",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default="0.00",
        ),
        sa.Column("unallocated_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("narration", sa.Text(), nullable=False),
        sa.Column("reference_number", sa.String(length=255), nullable=True),
        sa.Column("bank_account_number", sa.String(length=100), nullable=True),
        sa.Column("payer_raw_name", sa.String(length=255), nullable=True),
        sa.Column("payer_raw_identifier", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default="UNRECONCILED",
        ),
        sa.Column(
            "source",
            sa.String(length=50),
            nullable=False,
            server_default="BANK_STATEMENT_CSV",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("amount > 0", name="chk_payment_amount_positive"),
        sa.CheckConstraint("allocated_amount >= 0", name="chk_payment_allocated_non_negative"),
        sa.CheckConstraint("unallocated_amount >= 0", name="chk_payment_unallocated_non_negative"),
        sa.CheckConstraint(
            "allocated_amount + unallocated_amount = amount",
            name="chk_payment_balance",
        ),
    )
    op.create_index(
        "idx_payments_company_status",
        "payments",
        ["company_id", "status"],
    )
    op.create_index(
        "idx_payments_company_date",
        "payments",
        ["company_id", "transaction_date"],
    )
    op.create_index(
        "idx_payments_company_ref",
        "payments",
        ["company_id", "reference_number"],
    )
    op.create_index(
        "idx_payments_company_source",
        "payments",
        ["company_id", "source"],
    )


def downgrade() -> None:
    # Drop tables in reverse order of creation
    op.drop_index("idx_payments_company_source", table_name="payments")
    op.drop_index("idx_payments_company_ref", table_name="payments")
    op.drop_index("idx_payments_company_date", table_name="payments")
    op.drop_index("idx_payments_company_status", table_name="payments")
    op.drop_table("payments")

    op.drop_index("idx_bank_txns_company_batch", table_name="bank_transactions")
    op.drop_index("idx_bank_txns_company_ref", table_name="bank_transactions")
    op.drop_index("idx_bank_txns_company_type", table_name="bank_transactions")
    op.drop_index("idx_bank_txns_company_date", table_name="bank_transactions")
    op.drop_table("bank_transactions")

    op.drop_index("idx_import_batches_company_created", table_name="import_batches")
    op.drop_index("idx_import_batches_company_status", table_name="import_batches")
    op.drop_table("import_batches")
