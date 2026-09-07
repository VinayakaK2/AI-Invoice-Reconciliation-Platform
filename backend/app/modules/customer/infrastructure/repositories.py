"""Repository implementations for Customers, Aliases, and Payment Identifiers."""

from datetime import datetime, timezone
from typing import List, Optional, Tuple
from uuid import UUID
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from app.modules.customer.domain.entities import (
    Customer,
    CustomerAlias,
    CustomerPaymentIdentifier,
    IdentifierType,
)
from app.modules.customer.infrastructure.models import (
    CustomerAliasModel,
    CustomerModel,
    CustomerPaymentIdentifierModel,
)


class CustomerRepository:
    """Repository handling database persistence for Customer counterparty entities."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, customer: Customer) -> Customer:
        """Persist a new customer within a company workspace."""
        model = CustomerModel(
            id=customer.id,
            company_id=customer.company_id,
            name=customer.name.strip(),
            tax_id=customer.tax_id.strip() if customer.tax_id else None,
            email=customer.email.lower().strip() if customer.email else None,
            phone=customer.phone.strip() if customer.phone else None,
            notes=customer.notes.strip() if customer.notes else None,
            is_archived=customer.is_archived,
            created_at=customer.created_at,
            updated_at=customer.updated_at,
        )
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return self._to_entity(model)

    def get_by_id(self, customer_id: UUID, company_id: UUID) -> Optional[Customer]:
        """Fetch customer by ID strictly within caller's company tenant."""
        model = (
            self.db.query(CustomerModel)
            .filter(
                CustomerModel.id == customer_id,
                CustomerModel.company_id == company_id,
            )
            .first()
        )
        return self._to_entity(model) if model else None

    def get_by_name(self, name: str, company_id: UUID) -> Optional[Customer]:
        """Fetch customer by exact name (case-insensitive) within tenant."""
        model = (
            self.db.query(CustomerModel)
            .filter(
                func.lower(CustomerModel.name) == name.lower().strip(),
                CustomerModel.company_id == company_id,
            )
            .first()
        )
        return self._to_entity(model) if model else None

    def update(self, customer: Customer) -> Customer:
        """Update existing customer record with tenant scoping."""
        model = (
            self.db.query(CustomerModel)
            .filter(
                CustomerModel.id == customer.id,
                CustomerModel.company_id == customer.company_id,
            )
            .first()
        )
        if not model:
            raise ValueError(f"Customer {customer.id} not found in company {customer.company_id}")

        model.name = customer.name.strip()
        model.tax_id = customer.tax_id.strip() if customer.tax_id else None
        model.email = customer.email.lower().strip() if customer.email else None
        model.phone = customer.phone.strip() if customer.phone else None
        model.notes = customer.notes.strip() if customer.notes else None
        model.is_archived = customer.is_archived
        model.updated_at = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(model)
        return self._to_entity(model)

    def delete(self, customer_id: UUID, company_id: UUID) -> bool:
        """Safely delete customer record if present and owned by tenant."""
        model = (
            self.db.query(CustomerModel)
            .filter(
                CustomerModel.id == customer_id,
                CustomerModel.company_id == company_id,
            )
            .first()
        )
        if not model:
            return False

        self.db.delete(model)
        self.db.commit()
        return True

    def list_customers(
        self,
        company_id: UUID,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Customer], int]:
        """List and paginate customers for a company with archive filter."""
        query = self.db.query(CustomerModel).filter(CustomerModel.company_id == company_id)
        if not include_archived:
            query = query.filter(CustomerModel.is_archived.is_(False))

        total = query.count()
        models = (
            query.order_by(CustomerModel.name.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [self._to_entity(m) for m in models], total

    def search_customers(
        self,
        company_id: UUID,
        query_str: str,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Customer], int]:
        """Multi-signal search across names, aliases, tax IDs, email, phone, and payment identifiers."""
        clean_q = query_str.strip()
        if not clean_q:
            return self.list_customers(company_id, include_archived=include_archived, limit=limit, offset=offset)

        like_pattern = f"%{clean_q}%"

        # Select for customers matching aliases
        alias_select = (
            select(CustomerAliasModel.customer_id)
            .where(
                CustomerAliasModel.company_id == company_id,
                CustomerAliasModel.alias_name.ilike(like_pattern),
            )
        )

        # Select for customers matching payment identifiers
        ident_select = (
            select(CustomerPaymentIdentifierModel.customer_id)
            .where(
                CustomerPaymentIdentifierModel.company_id == company_id,
                CustomerPaymentIdentifierModel.identifier_value.ilike(like_pattern),
            )
        )

        filters = [
            CustomerModel.name.ilike(like_pattern),
            CustomerModel.tax_id.ilike(like_pattern),
            CustomerModel.email.ilike(like_pattern),
            CustomerModel.phone.ilike(like_pattern),
            CustomerModel.id.in_(alias_select),
            CustomerModel.id.in_(ident_select),
        ]

        query = self.db.query(CustomerModel).filter(
            CustomerModel.company_id == company_id,
            or_(*filters),
        )
        if not include_archived:
            query = query.filter(CustomerModel.is_archived.is_(False))

        total = query.count()
        models = (
            query.order_by(CustomerModel.name.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [self._to_entity(m) for m in models], total

    @staticmethod
    def _to_entity(model: CustomerModel) -> Customer:
        """Convert ORM model to pure Customer domain entity."""
        return Customer(
            id=model.id,
            company_id=model.company_id,
            name=model.name,
            tax_id=model.tax_id,
            email=model.email,
            phone=model.phone,
            notes=model.notes,
            is_archived=model.is_archived,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class CustomerAliasRepository:
    """Repository handling database operations for Customer alternate name aliases."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, alias: CustomerAlias) -> CustomerAlias:
        """Persist a new customer alias."""
        model = CustomerAliasModel(
            id=alias.id,
            company_id=alias.company_id,
            customer_id=alias.customer_id,
            alias_name=alias.alias_name.strip(),
            created_at=alias.created_at,
        )
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return self._to_entity(model)

    def get_by_id(self, alias_id: UUID, company_id: UUID) -> Optional[CustomerAlias]:
        """Fetch alias by ID scoped to tenant company."""
        model = (
            self.db.query(CustomerAliasModel)
            .filter(
                CustomerAliasModel.id == alias_id,
                CustomerAliasModel.company_id == company_id,
            )
            .first()
        )
        return self._to_entity(model) if model else None

    def get_by_name(self, alias_name: str, company_id: UUID) -> Optional[CustomerAlias]:
        """Fetch alias by exact name (case-insensitive) scoped to tenant company."""
        model = (
            self.db.query(CustomerAliasModel)
            .filter(
                func.lower(CustomerAliasModel.alias_name) == alias_name.lower().strip(),
                CustomerAliasModel.company_id == company_id,
            )
            .first()
        )
        return self._to_entity(model) if model else None

    def list_by_customer(self, customer_id: UUID, company_id: UUID) -> List[CustomerAlias]:
        """List all aliases for a specific customer within tenant."""
        models = (
            self.db.query(CustomerAliasModel)
            .filter(
                CustomerAliasModel.customer_id == customer_id,
                CustomerAliasModel.company_id == company_id,
            )
            .order_by(CustomerAliasModel.alias_name.asc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def delete(self, alias_id: UUID, company_id: UUID) -> bool:
        """Delete an alias record scoped to tenant company."""
        model = (
            self.db.query(CustomerAliasModel)
            .filter(
                CustomerAliasModel.id == alias_id,
                CustomerAliasModel.company_id == company_id,
            )
            .first()
        )
        if not model:
            return False

        self.db.delete(model)
        self.db.commit()
        return True

    @staticmethod
    def _to_entity(model: CustomerAliasModel) -> CustomerAlias:
        """Convert ORM model to CustomerAlias domain entity."""
        return CustomerAlias(
            id=model.id,
            company_id=model.company_id,
            customer_id=model.customer_id,
            alias_name=model.alias_name,
            created_at=model.created_at,
        )


class CustomerPaymentIdentifierRepository:
    """Repository handling database operations for customer bank accounts and UPI IDs."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, identifier: CustomerPaymentIdentifier) -> CustomerPaymentIdentifier:
        """Persist a new customer payment identifier."""
        model = CustomerPaymentIdentifierModel(
            id=identifier.id,
            company_id=identifier.company_id,
            customer_id=identifier.customer_id,
            identifier_type=identifier.identifier_type.value if isinstance(identifier.identifier_type, IdentifierType) else identifier.identifier_type,
            identifier_value=identifier.identifier_value.strip(),
            is_active=identifier.is_active,
            created_at=identifier.created_at,
        )
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return self._to_entity(model)

    def get_by_id(self, identifier_id: UUID, company_id: UUID) -> Optional[CustomerPaymentIdentifier]:
        """Fetch payment identifier by ID scoped to tenant."""
        model = (
            self.db.query(CustomerPaymentIdentifierModel)
            .filter(
                CustomerPaymentIdentifierModel.id == identifier_id,
                CustomerPaymentIdentifierModel.company_id == company_id,
            )
            .first()
        )
        return self._to_entity(model) if model else None

    def get_by_type_and_value(
        self,
        identifier_type: str,
        identifier_value: str,
        company_id: UUID,
    ) -> Optional[CustomerPaymentIdentifier]:
        """Fetch payment identifier by type and value scoped to company tenant."""
        model = (
            self.db.query(CustomerPaymentIdentifierModel)
            .filter(
                CustomerPaymentIdentifierModel.company_id == company_id,
                CustomerPaymentIdentifierModel.identifier_type == identifier_type,
                func.lower(CustomerPaymentIdentifierModel.identifier_value) == identifier_value.lower().strip(),
            )
            .first()
        )
        return self._to_entity(model) if model else None

    def list_by_customer(self, customer_id: UUID, company_id: UUID) -> List[CustomerPaymentIdentifier]:
        """List all payment identifiers for a specific customer within tenant."""
        models = (
            self.db.query(CustomerPaymentIdentifierModel)
            .filter(
                CustomerPaymentIdentifierModel.customer_id == customer_id,
                CustomerPaymentIdentifierModel.company_id == company_id,
            )
            .order_by(CustomerPaymentIdentifierModel.created_at.asc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def delete(self, identifier_id: UUID, company_id: UUID) -> bool:
        """Delete a payment identifier record scoped to tenant company."""
        model = (
            self.db.query(CustomerPaymentIdentifierModel)
            .filter(
                CustomerPaymentIdentifierModel.id == identifier_id,
                CustomerPaymentIdentifierModel.company_id == company_id,
            )
            .first()
        )
        if not model:
            return False

        self.db.delete(model)
        self.db.commit()
        return True

    @staticmethod
    def _to_entity(model: CustomerPaymentIdentifierModel) -> CustomerPaymentIdentifier:
        """Convert ORM model to CustomerPaymentIdentifier domain entity."""
        return CustomerPaymentIdentifier(
            id=model.id,
            company_id=model.company_id,
            customer_id=model.customer_id,
            identifier_type=IdentifierType(model.identifier_type),
            identifier_value=model.identifier_value,
            is_active=model.is_active,
            created_at=model.created_at,
        )
