"""SQLAlchemy 2.0 ORM persistence models for Customers, Aliases, and Payment Identifiers."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, UniqueConstraint
from app.core.database import Base
from app.shared.infrastructure.db_types import GUID


class CustomerModel(Base):
    """Database persistence model for counterparty customers."""

    __tablename__ = "customers"

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
    name = Column(String(255), nullable=False, index=True)
    tax_id = Column(String(50), nullable=True, index=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    notes = Column(String(1000), nullable=True)
    is_archived = Column(Boolean, nullable=False, default=False, index=True)
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
        UniqueConstraint("company_id", "name", name="uq_customers_company_name"),
    )


class CustomerAliasModel(Base):
    """Database persistence model for customer name aliases."""

    __tablename__ = "customer_aliases"

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
    customer_id = Column(
        GUID(),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias_name = Column(String(255), nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("company_id", "alias_name", name="uq_customer_aliases_company_alias"),
    )


class CustomerPaymentIdentifierModel(Base):
    """Database persistence model for known bank accounts, virtual accounts, and UPI IDs."""

    __tablename__ = "customer_payment_identifiers"

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
    customer_id = Column(
        GUID(),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    identifier_type = Column(String(50), nullable=False)
    identifier_value = Column(String(255), nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("company_id", "identifier_type", "identifier_value", name="uq_customer_identifiers"),
        Index("idx_customer_identifiers_lookup", "company_id", "identifier_value"),
    )
