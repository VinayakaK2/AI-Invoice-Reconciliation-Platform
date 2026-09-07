"""Repository implementation for Company workspace persistence."""

from typing import Optional
from uuid import UUID
from sqlalchemy.orm import Session
from app.modules.company.domain.entities import Company
from app.modules.company.infrastructure.models import CompanyModel


class CompanyRepository:
    """Repository handling database operations for Company entities."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, company: Company) -> Company:
        """Persist a new company record in the database."""
        model = CompanyModel(
            id=company.id,
            name=company.name,
            base_currency=company.base_currency,
            is_active=company.is_active,
            created_at=company.created_at,
            updated_at=company.updated_at,
        )
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return self._to_entity(model)

    def get_by_id(self, company_id: UUID) -> Optional[Company]:
        """Fetch a company by its primary key ID."""
        model = self.db.query(CompanyModel).filter(CompanyModel.id == company_id).first()
        return self._to_entity(model) if model else None

    def update(self, company: Company) -> Company:
        """Update an existing company record."""
        model = self.db.query(CompanyModel).filter(CompanyModel.id == company.id).first()
        if not model:
            raise ValueError(f"Company {company.id} not found for update")

        model.name = company.name
        model.base_currency = company.base_currency
        model.is_active = company.is_active
        model.updated_at = company.updated_at

        self.db.commit()
        self.db.refresh(model)
        return self._to_entity(model)

    @staticmethod
    def _to_entity(model: CompanyModel) -> Company:
        """Convert an ORM model to a domain entity."""
        return Company(
            id=model.id,
            name=model.name,
            base_currency=model.base_currency,
            is_active=model.is_active,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
