"""Application use cases coordinating Customer Management workflows."""

import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from uuid import UUID
from app.core.logging import logger
from app.modules.customer.domain.entities import (
    Customer,
    CustomerAlias,
    CustomerPaymentIdentifier,
    IdentifierType,
)
from app.modules.customer.infrastructure.repositories import (
    CustomerAliasRepository,
    CustomerPaymentIdentifierRepository,
    CustomerRepository,
)
from app.modules.customer.presentation.schemas import (
    CustomerAliasCreateRequest,
    CustomerCreateRequest,
    CustomerPaymentIdentifierCreateRequest,
    CustomerUpdateRequest,
)
from app.shared.exceptions import ConflictError, NotFoundError, ValidationError


class CreateCustomerUseCase:
    """Handles creation of a new customer counterparty within a company workspace."""

    def __init__(self, customer_repo: CustomerRepository) -> None:
        self.customer_repo = customer_repo

    def execute(self, company_id: UUID, request: CustomerCreateRequest) -> Customer:
        """Validate uniqueness within tenant and persist new customer."""
        clean_name = " ".join(request.name.strip().split())
        if not clean_name:
            raise ValidationError("Customer name cannot be empty or whitespace only.")

        # Check for duplicate customer name in tenant
        existing = self.customer_repo.get_by_name(clean_name, company_id)
        if existing:
            raise ConflictError(
                message=f"A customer named '{clean_name}' already exists in your company.",
                code="CUSTOMER_ALREADY_EXISTS",
            )

        customer = Customer(
            id=uuid.uuid4(),
            company_id=company_id,
            name=clean_name,
            tax_id=request.tax_id.strip() if request.tax_id else None,
            email=str(request.email).lower().strip() if request.email else None,
            phone=request.phone.strip() if request.phone else None,
            notes=request.notes.strip() if request.notes else None,
            is_archived=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        saved = self.customer_repo.create(customer)
        logger.info(
            f"Created customer '{saved.name}' in company {company_id}",
            extra={"company_id": str(company_id), "customer_id": str(saved.id)},
        )
        return saved


class GetCustomerDetailUseCase:
    """Fetches customer entity along with associated aliases and payment identifiers."""

    def __init__(
        self,
        customer_repo: CustomerRepository,
        alias_repo: CustomerAliasRepository,
        identifier_repo: CustomerPaymentIdentifierRepository,
    ) -> None:
        self.customer_repo = customer_repo
        self.alias_repo = alias_repo
        self.identifier_repo = identifier_repo

    def execute(
        self,
        customer_id: UUID,
        company_id: UUID,
    ) -> Tuple[Customer, List[CustomerAlias], List[CustomerPaymentIdentifier]]:
        """Retrieve customer details ensuring strict tenant isolation."""
        customer = self.customer_repo.get_by_id(customer_id, company_id)
        if not customer:
            raise NotFoundError("Customer", customer_id)

        aliases = self.alias_repo.list_by_customer(customer_id, company_id)
        identifiers = self.identifier_repo.list_by_customer(customer_id, company_id)
        return customer, aliases, identifiers


class ListCustomersUseCase:
    """Lists and paginates customers belonging to caller's company workspace."""

    def __init__(self, customer_repo: CustomerRepository) -> None:
        self.customer_repo = customer_repo

    def execute(
        self,
        company_id: UUID,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Customer], int]:
        """Fetch paginated list of customers for company."""
        return self.customer_repo.list_customers(
            company_id=company_id,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )


class SearchCustomersUseCase:
    """Performs multi-signal database search across names, aliases, tax IDs, and identifiers."""

    def __init__(self, customer_repo: CustomerRepository) -> None:
        self.customer_repo = customer_repo

    def execute(
        self,
        company_id: UUID,
        query_str: str,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Customer], int]:
        """Execute multi-signal search scoped to tenant company."""
        return self.customer_repo.search_customers(
            company_id=company_id,
            query_str=query_str,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )


class UpdateCustomerUseCase:
    """Updates customer details while preserving tenant uniqueness constraints."""

    def __init__(self, customer_repo: CustomerRepository) -> None:
        self.customer_repo = customer_repo

    def execute(
        self,
        customer_id: UUID,
        company_id: UUID,
        request: CustomerUpdateRequest,
    ) -> Customer:
        """Update customer fields and commit."""
        customer = self.customer_repo.get_by_id(customer_id, company_id)
        if not customer:
            raise NotFoundError("Customer", customer_id)

        if request.name is not None:
            clean_name = " ".join(request.name.strip().split())
            if not clean_name:
                raise ValidationError("Customer name cannot be empty.")
            # If changing name, check for collision
            if clean_name.lower() != customer.name.lower():
                existing = self.customer_repo.get_by_name(clean_name, company_id)
                if existing and existing.id != customer.id:
                    raise ConflictError(
                        message=f"Another customer named '{clean_name}' already exists in your company.",
                        code="CUSTOMER_NAME_CONFLICT",
                    )
            customer.name = clean_name

        if request.tax_id is not None:
            customer.tax_id = request.tax_id.strip() if request.tax_id else None

        if request.email is not None:
            customer.email = str(request.email).lower().strip() if request.email else None

        if request.phone is not None:
            customer.phone = request.phone.strip() if request.phone else None

        if request.notes is not None:
            customer.notes = request.notes.strip() if request.notes else None

        updated = self.customer_repo.update(customer)
        logger.info(
            f"Updated customer {customer_id} in company {company_id}",
            extra={"company_id": str(company_id), "customer_id": str(customer_id)},
        )
        return updated


class ArchiveCustomerUseCase:
    """Safely transitions customer state to archived without destroying historical references."""

    def __init__(self, customer_repo: CustomerRepository) -> None:
        self.customer_repo = customer_repo

    def execute(self, customer_id: UUID, company_id: UUID) -> Customer:
        """Archive customer."""
        customer = self.customer_repo.get_by_id(customer_id, company_id)
        if not customer:
            raise NotFoundError("Customer", customer_id)

        customer.archive()
        updated = self.customer_repo.update(customer)
        logger.info(f"Archived customer {customer_id} in company {company_id}")
        return updated


class UnarchiveCustomerUseCase:
    """Restores an archived customer to active status."""

    def __init__(self, customer_repo: CustomerRepository) -> None:
        self.customer_repo = customer_repo

    def execute(self, customer_id: UUID, company_id: UUID) -> Customer:
        """Restore customer."""
        customer = self.customer_repo.get_by_id(customer_id, company_id)
        if not customer:
            raise NotFoundError("Customer", customer_id)

        customer.unarchive()
        updated = self.customer_repo.update(customer)
        logger.info(f"Unarchived customer {customer_id} in company {company_id}")
        return updated


class DeleteCustomerUseCase:
    """Safely deletes customer if no financial records exist."""

    def __init__(self, customer_repo: CustomerRepository) -> None:
        self.customer_repo = customer_repo

    def execute(self, customer_id: UUID, company_id: UUID) -> bool:
        """Delete customer record."""
        customer = self.customer_repo.get_by_id(customer_id, company_id)
        if not customer:
            raise NotFoundError("Customer", customer_id)

        success = self.customer_repo.delete(customer_id, company_id)
        logger.info(f"Deleted customer {customer_id} from company {company_id}")
        return success


class AddCustomerAliasUseCase:
    """Adds an alternate name or statement alias for a customer with duplicate protection."""

    def __init__(
        self,
        customer_repo: CustomerRepository,
        alias_repo: CustomerAliasRepository,
    ) -> None:
        self.customer_repo = customer_repo
        self.alias_repo = alias_repo

    def execute(
        self,
        customer_id: UUID,
        company_id: UUID,
        request: CustomerAliasCreateRequest,
    ) -> CustomerAlias:
        """Normalize alias and associate with customer."""
        customer = self.customer_repo.get_by_id(customer_id, company_id)
        if not customer:
            raise NotFoundError("Customer", customer_id)

        clean_alias = " ".join(request.alias_name.strip().split())
        if not clean_alias:
            raise ValidationError("Alias name cannot be empty.")

        # Check duplicate alias within company
        existing = self.alias_repo.get_by_name(clean_alias, company_id)
        if existing:
            raise ConflictError(
                message=f"Alias '{clean_alias}' is already registered in your company.",
                code="ALIAS_ALREADY_EXISTS",
            )

        alias = CustomerAlias(
            id=uuid.uuid4(),
            company_id=company_id,
            customer_id=customer_id,
            alias_name=clean_alias,
            created_at=datetime.now(timezone.utc),
        )
        saved = self.alias_repo.create(alias)
        logger.info(f"Added alias '{clean_alias}' to customer {customer_id}")
        return saved


class RemoveCustomerAliasUseCase:
    """Deletes an alternate name alias from a customer."""

    def __init__(
        self,
        customer_repo: CustomerRepository,
        alias_repo: CustomerAliasRepository,
    ) -> None:
        self.customer_repo = customer_repo
        self.alias_repo = alias_repo

    def execute(self, customer_id: UUID, alias_id: UUID, company_id: UUID) -> bool:
        """Delete alias scoped to customer and tenant."""
        customer = self.customer_repo.get_by_id(customer_id, company_id)
        if not customer:
            raise NotFoundError("Customer", customer_id)

        alias = self.alias_repo.get_by_id(alias_id, company_id)
        if not alias or alias.customer_id != customer_id:
            raise NotFoundError("CustomerAlias", alias_id)

        return self.alias_repo.delete(alias_id, company_id)


class AddPaymentIdentifierUseCase:
    """Associates a known bank account, virtual account, or UPI VPA with a customer."""

    def __init__(
        self,
        customer_repo: CustomerRepository,
        identifier_repo: CustomerPaymentIdentifierRepository,
    ) -> None:
        self.customer_repo = customer_repo
        self.identifier_repo = identifier_repo

    def execute(
        self,
        customer_id: UUID,
        company_id: UUID,
        request: CustomerPaymentIdentifierCreateRequest,
    ) -> CustomerPaymentIdentifier:
        """Validate type, normalize value, check uniqueness in tenant, and save."""
        customer = self.customer_repo.get_by_id(customer_id, company_id)
        if not customer:
            raise NotFoundError("Customer", customer_id)

        # Validate type
        try:
            ident_type = IdentifierType(request.identifier_type.upper().strip())
        except ValueError:
            valid_types = [t.value for t in IdentifierType]
            raise ValidationError(
                f"Invalid identifier_type '{request.identifier_type}'. Must be one of {valid_types}."
            )

        clean_value = request.identifier_value.strip()
        if not clean_value:
            raise ValidationError("Identifier value cannot be empty.")

        # Check duplicate identifier in tenant
        existing = self.identifier_repo.get_by_type_and_value(
            identifier_type=ident_type.value,
            identifier_value=clean_value,
            company_id=company_id,
        )
        if existing:
            raise ConflictError(
                message=f"{ident_type.value} '{clean_value}' is already mapped to a customer in your company.",
                code="IDENTIFIER_ALREADY_EXISTS",
            )

        identifier = CustomerPaymentIdentifier(
            id=uuid.uuid4(),
            company_id=company_id,
            customer_id=customer_id,
            identifier_type=ident_type,
            identifier_value=clean_value,
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        saved = self.identifier_repo.create(identifier)
        logger.info(
            f"Added {ident_type.value} identifier '{clean_value}' to customer {customer_id}"
        )
        return saved


class RemovePaymentIdentifierUseCase:
    """Deletes a payment identifier from a customer."""

    def __init__(
        self,
        customer_repo: CustomerRepository,
        identifier_repo: CustomerPaymentIdentifierRepository,
    ) -> None:
        self.customer_repo = customer_repo
        self.identifier_repo = identifier_repo

    def execute(self, customer_id: UUID, identifier_id: UUID, company_id: UUID) -> bool:
        """Delete payment identifier scoped to customer and tenant."""
        customer = self.customer_repo.get_by_id(customer_id, company_id)
        if not customer:
            raise NotFoundError("Customer", customer_id)

        identifier = self.identifier_repo.get_by_id(identifier_id, company_id)
        if not identifier or identifier.customer_id != customer_id:
            raise NotFoundError("CustomerPaymentIdentifier", identifier_id)

        return self.identifier_repo.delete(identifier_id, company_id)
