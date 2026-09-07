"""Company domain entities and value objects.

Defines the tenant workspace entity for the AI Invoice Reconciliation Platform.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID


@dataclass
class Company:
    """Core tenant root entity isolating all financial records and workspaces."""
    id: UUID
    name: str
    base_currency: str = "INR"
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
