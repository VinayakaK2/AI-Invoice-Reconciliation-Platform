"""Customer domain entities and value objects.

Defines pure Python business models for Customers, Aliases, and Payment Identifiers.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID


class IdentifierType(str, Enum):
    """Supported payment identifier types for counterparty bank resolution."""
    BANK_ACCOUNT = "BANK_ACCOUNT"
    VIRTUAL_ACCOUNT = "VIRTUAL_ACCOUNT"
    UPI_VPA = "UPI_VPA"


@dataclass
class Customer:
    """Core domain customer counterparty entity scoped to a specific company tenant."""
    id: UUID
    company_id: UUID
    name: str
    tax_id: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None
    is_archived: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def archive(self) -> None:
        """Mark customer as archived while preserving historical identity."""
        self.is_archived = True
        self.updated_at = datetime.now(timezone.utc)

    def unarchive(self) -> None:
        """Restore customer from archived status."""
        self.is_archived = False
        self.updated_at = datetime.now(timezone.utc)

    def belongs_to(self, company_id: UUID) -> bool:
        """Verify tenant ownership."""
        return self.company_id == company_id


@dataclass
class CustomerAlias:
    """Alternate name or spelling for a customer found in bank transaction narrations."""
    id: UUID
    company_id: UUID
    customer_id: UUID
    alias_name: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CustomerPaymentIdentifier:
    """Known bank account, virtual account, or UPI VPA mapping to a customer."""
    id: UUID
    company_id: UUID
    customer_id: UUID
    identifier_type: IdentifierType
    identifier_value: str
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
