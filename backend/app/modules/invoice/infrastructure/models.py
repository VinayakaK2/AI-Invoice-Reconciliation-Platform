"""SQLAlchemy 2.0 ORM models for Invoices and Invoice Documents.

Defines the database schema, foreign key relations, unique constraints, and check constraints
for invoices and original source documents.
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
from app.modules.customer.infrastructure.models import CustomerModel
from app.shared.infrastructure.db_types import GUID


class InvoiceDocumentModel(Base):
    """SQLAlchemy model for uploaded source invoice files."""

    __tablename__ = "invoice_documents"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        GUID(),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoice_id = Column(GUID(), nullable=True, index=True)
    file_name = Column(String(255), nullable=False)
    storage_key = Column(String(512), nullable=False)
    file_size_bytes = Column(Integer, nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_hash = Column(String(64), nullable=False, index=True)
    ocr_status = Column(String(50), nullable=False, default="PENDING")
    extracted_data = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_invoice_docs_company_hash", "company_id", "file_hash"),
    )


class InvoiceModel(Base):
    """SQLAlchemy model for Invoices."""

    __tablename__ = "invoices"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    company_id = Column(
        GUID(),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id = Column(
        GUID(),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    document_id = Column(
        GUID(),
        ForeignKey("invoice_documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    invoice_number = Column(String(100), nullable=False)
    issue_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=False)
    total_amount = Column(Numeric(14, 2), nullable=False)
    paid_amount = Column(Numeric(14, 2), nullable=False, default=0.00)
    outstanding_amount = Column(Numeric(14, 2), nullable=False)
    tax_amount = Column(Numeric(14, 2), nullable=True)
    currency = Column(String(3), nullable=False, default="INR")
    status = Column(String(50), nullable=False, default="PENDING", index=True)
    source = Column(String(50), nullable=False, default="MANUAL")
    notes = Column(Text, nullable=True)
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

    # Relationships
    customer = relationship("CustomerModel", foreign_keys=[customer_id], lazy="joined")
    document = relationship("InvoiceDocumentModel", foreign_keys=[document_id], lazy="joined")

    __table_args__ = (
        UniqueConstraint("company_id", "invoice_number", name="uq_invoices_company_number"),
        CheckConstraint("total_amount > 0", name="chk_invoice_total_positive"),
        CheckConstraint("paid_amount >= 0", name="chk_invoice_paid_non_negative"),
        CheckConstraint("outstanding_amount >= 0", name="chk_invoice_outstanding_non_negative"),
        CheckConstraint(
            "paid_amount + outstanding_amount = total_amount",
            name="chk_invoice_balance",
        ),
        Index("idx_invoices_company_status", "company_id", "status"),
        Index("idx_invoices_company_customer", "company_id", "customer_id"),
        Index("idx_invoices_company_dates", "company_id", "issue_date", "due_date"),
        Index("idx_invoices_company_archived", "company_id", "is_archived"),
    )
