"""Phase 10: Create customers, customer_aliases, and customer_payment_identifiers.

Revision ID: 0002_phase_10
Revises: 0001_phase_09
Create Date: 2026-09-06 12:45:00.000000

"""

from alembic import op
import sqlalchemy as sa
from app.shared.infrastructure.db_types import GUID

# revision identifiers, used by Alembic.
revision = "0002_phase_10"
down_revision = "0001_phase_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create customers table
    op.create_table(
        "customers",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("company_id", GUID(), sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tax_id", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "name", name="uq_customers_company_name"),
    )
    op.create_index("idx_customers_company", "customers", ["company_id"])
    op.create_index("idx_customers_name", "customers", ["name"])
    op.create_index("idx_customers_tax_id", "customers", ["tax_id"])
    op.create_index("idx_customers_archived", "customers", ["is_archived"])

    # 2. Create customer_aliases table
    op.create_table(
        "customer_aliases",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("company_id", GUID(), sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("customer_id", GUID(), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "alias_name", name="uq_customer_aliases_company_alias"),
    )
    op.create_index("idx_customer_aliases_company", "customer_aliases", ["company_id"])
    op.create_index("idx_customer_aliases_customer", "customer_aliases", ["customer_id"])
    op.create_index("idx_customer_aliases_name", "customer_aliases", ["alias_name"])

    # 3. Create customer_payment_identifiers table
    op.create_table(
        "customer_payment_identifiers",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("company_id", GUID(), sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("customer_id", GUID(), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("identifier_type", sa.String(length=50), nullable=False),
        sa.Column("identifier_value", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "identifier_type", "identifier_value", name="uq_customer_identifiers"),
    )
    op.create_index("idx_customer_identifiers_company", "customer_payment_identifiers", ["company_id"])
    op.create_index("idx_customer_identifiers_customer", "customer_payment_identifiers", ["customer_id"])
    op.create_index("idx_customer_identifiers_lookup", "customer_payment_identifiers", ["company_id", "identifier_value"])


def downgrade() -> None:
    op.drop_table("customer_payment_identifiers")
    op.drop_table("customer_aliases")
    op.drop_table("customers")
