"""Phase 12: Add updated_at and status index to invoice_documents.

Revision ID: 0006_phase_12_ocr
Revises: 0005_phase_12
Create Date: 2026-09-08 11:30:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0006_phase_12_ocr"
down_revision = "0005_phase_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add updated_at column to invoice_documents
    op.add_column(
        "invoice_documents",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # 2. Add composite index on (company_id, ocr_status) for fast pipeline queries
    op.create_index(
        "idx_invoice_docs_company_status",
        "invoice_documents",
        ["company_id", "ocr_status"],
    )


def downgrade() -> None:
    op.drop_index("idx_invoice_docs_company_status", table_name="invoice_documents")
    op.drop_column("invoice_documents", "updated_at")
