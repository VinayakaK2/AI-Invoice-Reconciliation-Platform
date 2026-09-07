"""SQLAlchemy 2.0 ORM persistence models for Company workspaces."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, String
from app.core.database import Base
from app.shared.infrastructure.db_types import GUID


class CompanyModel(Base):
    """Database persistence model for tenant companies."""

    __tablename__ = "companies"

    id = Column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    name = Column(String(255), nullable=False)
    base_currency = Column(String(3), nullable=False, default="INR")
    is_active = Column(Boolean, nullable=False, default=True)
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
