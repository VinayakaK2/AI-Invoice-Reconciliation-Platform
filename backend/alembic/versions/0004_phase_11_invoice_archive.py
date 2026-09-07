"""Phase 11: Add is_archived column and index to invoices table.

Revision ID: 0004_phase_11_archive
Revises: 0003_phase_11
Create Date: 2026-09-06 18:30:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_phase_11_archive"
down_revision = "0003_phase_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column(
            "is_archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "idx_invoices_company_archived",
        "invoices",
        ["company_id", "is_archived"],
    )


def downgrade() -> None:
    op.drop_index("idx_invoices_company_archived", table_name="invoices")
    op.drop_column("invoices", "is_archived")
