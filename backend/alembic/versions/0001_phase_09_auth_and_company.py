"""Phase 9: Create companies, users, user_invitations, and password_reset_tokens.

Revision ID: 0001_phase_09
Revises:
Create Date: 2026-09-05 20:45:00.000000

"""

from alembic import op
import sqlalchemy as sa
from app.shared.infrastructure.db_types import GUID

# revision identifiers, used by Alembic.
revision = "0001_phase_09"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create companies table
    op.create_table(
        "companies",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # 2. Create users table
    op.create_table(
        "users",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("company_id", GUID(), sa.ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False, server_default="ACCOUNTANT"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "email", name="uq_users_company_email"),
    )
    op.create_index("idx_users_company", "users", ["company_id"])
    op.create_index("idx_users_email", "users", ["email"])

    # 3. Create user_invitations table
    op.create_table(
        "user_invitations",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("company_id", GUID(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False, server_default="ACCOUNTANT"),
        sa.Column("invitation_token", sa.String(length=255), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_accepted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_invitations_company", "user_invitations", ["company_id"])
    op.create_index("idx_invitations_token", "user_invitations", ["invitation_token"])

    # 4. Create password_reset_tokens table
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("user_id", GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_reset_user", "password_reset_tokens", ["user_id"])
    op.create_index("idx_reset_token_hash", "password_reset_tokens", ["token_hash"])


def downgrade() -> None:
    op.drop_table("password_reset_tokens")
    op.drop_table("user_invitations")
    op.drop_table("users")
    op.drop_table("companies")
