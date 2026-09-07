"""SQLAlchemy 2.0 ORM persistence models for Authentication and Tenant Membership."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, UniqueConstraint
from app.core.database import Base
from app.shared.infrastructure.db_types import GUID


class UserModel(Base):
    """Database persistence model for tenant users."""

    __tablename__ = "users"

    id = Column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    company_id = Column(
        GUID(),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    email = Column(String(255), nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="ACCOUNTANT")
    is_active = Column(Boolean, nullable=False, default=True)
    is_verified = Column(Boolean, nullable=False, default=False)
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

    __table_args__ = (
        UniqueConstraint("company_id", "email", name="uq_users_company_email"),
    )


class UserInvitationModel(Base):
    """Database persistence model for company invitations sent to accountants."""

    __tablename__ = "user_invitations"

    id = Column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    company_id = Column(
        GUID(),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="ACCOUNTANT")
    invitation_token = Column(String(255), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_accepted = Column(Boolean, nullable=False, default=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class PasswordResetTokenModel(Base):
    """Database persistence model for secure password reset tokens."""

    __tablename__ = "password_reset_tokens"

    id = Column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    user_id = Column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash = Column(String(255), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_used = Column(Boolean, nullable=False, default=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
