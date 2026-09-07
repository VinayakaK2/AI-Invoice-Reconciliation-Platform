"""Phase 11: Create invoice_documents and invoices tables.

Revision ID: 0003_phase_11
Revises: 0002_phase_10
Create Date: 2026-09-06 17:30:00.000000

"""

from alembic import op
import sqlalchemy as sa
from app.shared.infrastructure.db_types import GUID

# revision identifiers, used by Alembic.
revision = "0003_phase_11"
down_revision = "0002_phase_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create invoice_documents table
    op.create_table(
        "invoice_documents",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column(
            "company_id",
            GUID(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("invoice_id", GUID(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("ocr_status", sa.String(length=50), nullable=False, server_default="PENDING"),
        sa.Column("extracted_data", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_invoice_docs_company", "invoice_documents", ["company_id"])
    op.create_index("idx_invoice_docs_hash", "invoice_documents", ["file_hash"])
    op.create_index(
        "idx_invoice_docs_company_hash",
        "invoice_documents",
        ["company_id", "file_hash"],
    )

    # 2. Create invoices table
    op.create_table(
        "invoices",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column(
            "company_id",
            GUID(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            GUID(),
            sa.ForeignKey("customers.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "document_id",
            GUID(),
            sa.ForeignKey("invoice_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("invoice_number", sa.String(length=100), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "paid_amount",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column(
            "outstanding_amount",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
        ),
        sa.Column("tax_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="PENDING"),
        sa.Column("source", sa.String(length=50), nullable=False, server_default="MANUAL"),
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
        sa.UniqueConstraint("company_id", "invoice_number", name="uq_invoices_company_number"),
        sa.CheckConstraint("total_amount > 0", name="chk_invoice_total_positive"),
        sa.CheckConstraint("paid_amount >= 0", name="chk_invoice_paid_non_negative"),
        sa.CheckConstraint(
            "outstanding_amount >= 0",
            name="chk_invoice_outstanding_non_negative",
        ),
        sa.CheckConstraint(
            "paid_amount + outstanding_amount = total_amount",
            name="chk_invoice_balance",
        ),
    )
    op.create_index("idx_invoices_company_status", "invoices", ["company_id", "status"])
    op.create_index("idx_invoices_company_customer", "invoices", ["company_id", "customer_id"])
    op.create_index(
        "idx_invoices_company_dates",
        "invoices",
        ["company_id", "issue_date", "due_date"],
    )


def downgrade() -> None:
    op.drop_index("idx_invoices_company_dates", table_name="invoices")
    op.drop_index("idx_invoices_company_customer", table_name="invoices")
    op.drop_index("idx_invoices_company_status", table_name="invoices")
    op.drop_table("invoices")

    op.drop_index("idx_invoice_docs_company_hash", table_name="invoice_documents")
    op.drop_index("idx_invoice_docs_hash", table_name="invoice_documents")
    op.drop_index("idx_invoice_docs_company", table_name="invoice_documents")
    op.drop_table("invoice_documents")
