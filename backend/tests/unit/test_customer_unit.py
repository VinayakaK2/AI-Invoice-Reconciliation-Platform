"""Unit tests for Customer domain entities, repositories, and value objects."""

import uuid
from datetime import datetime, timezone
import pytest
from app.modules.company.domain.entities import Company
from app.modules.company.infrastructure.repositories import CompanyRepository
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


def test_customer_entity_predicates():
    """Verify Customer domain entity lifecycle predicates and tenant ownership."""
    company_a = uuid.uuid4()
    company_b = uuid.uuid4()
    cust_id = uuid.uuid4()

    customer = Customer(
        id=cust_id,
        company_id=company_a,
        name="Acme Technologies",
        tax_id="29ABCDE1234F1Z5",
        email="billing@acme.com",
    )

    assert customer.belongs_to(company_a) is True
    assert customer.belongs_to(company_b) is False
    assert customer.is_archived is False

    customer.archive()
    assert customer.is_archived is True

    customer.unarchive()
    assert customer.is_archived is False


def test_customer_repository_crud(db_session):
    """Verify CustomerRepository creates, updates, and retrieves customer records."""
    comp_repo = CompanyRepository(db_session)
    cust_repo = CustomerRepository(db_session)

    company = comp_repo.create(Company(id=uuid.uuid4(), name="Parent Corp"))
    cust_id = uuid.uuid4()

    customer = Customer(
        id=cust_id,
        company_id=company.id,
        name="Delta Logistics",
        tax_id="27ABCDE5678F1Z2",
        email="ops@delta.com",
        phone="+91 9876543210",
        notes="Important customer",
    )

    created = cust_repo.create(customer)
    assert created.id == cust_id
    assert created.name == "Delta Logistics"
    assert created.is_archived is False

    # Get by ID
    fetched = cust_repo.get_by_id(cust_id, company.id)
    assert fetched is not None
    assert fetched.name == "Delta Logistics"

    # Get by Name (case-insensitive)
    by_name = cust_repo.get_by_name("delta logistics", company.id)
    assert by_name is not None
    assert by_name.id == cust_id

    # Update
    fetched.notes = "Updated notes"
    updated = cust_repo.update(fetched)
    assert updated.notes == "Updated notes"

    # Delete
    deleted = cust_repo.delete(cust_id, company.id)
    assert deleted is True
    assert cust_repo.get_by_id(cust_id, company.id) is None


def test_customer_alias_repository(db_session):
    """Verify CustomerAliasRepository creates, lists, and deletes aliases."""
    comp_repo = CompanyRepository(db_session)
    cust_repo = CustomerRepository(db_session)
    alias_repo = CustomerAliasRepository(db_session)

    company = comp_repo.create(Company(id=uuid.uuid4(), name="Parent Corp"))
    customer = cust_repo.create(
        Customer(
            id=uuid.uuid4(),
            company_id=company.id,
            name="Reliance Industries Limited",
        )
    )

    alias_1 = alias_repo.create(
        CustomerAlias(
            id=uuid.uuid4(),
            company_id=company.id,
            customer_id=customer.id,
            alias_name="RELIANCE IND",
        )
    )
    alias_2 = alias_repo.create(
        CustomerAlias(
            id=uuid.uuid4(),
            company_id=company.id,
            customer_id=customer.id,
            alias_name="RIL",
        )
    )

    # List aliases for customer
    aliases = alias_repo.list_by_customer(customer.id, company.id)
    assert len(aliases) == 2
    names = {a.alias_name for a in aliases}
    assert names == {"RELIANCE IND", "RIL"}

    # Get by Name (case-insensitive)
    found = alias_repo.get_by_name("ril", company.id)
    assert found is not None
    assert found.id == alias_2.id

    # Delete alias
    assert alias_repo.delete(alias_1.id, company.id) is True
    remaining = alias_repo.list_by_customer(customer.id, company.id)
    assert len(remaining) == 1


def test_customer_payment_identifier_repository(db_session):
    """Verify CustomerPaymentIdentifierRepository creates, lists, and deletes identifiers."""
    comp_repo = CompanyRepository(db_session)
    cust_repo = CustomerRepository(db_session)
    ident_repo = CustomerPaymentIdentifierRepository(db_session)

    company = comp_repo.create(Company(id=uuid.uuid4(), name="Parent Corp"))
    customer = cust_repo.create(
        Customer(
            id=uuid.uuid4(),
            company_id=company.id,
            name="Tata Consultancy Services",
        )
    )

    bank_id = ident_repo.create(
        CustomerPaymentIdentifier(
            id=uuid.uuid4(),
            company_id=company.id,
            customer_id=customer.id,
            identifier_type=IdentifierType.BANK_ACCOUNT,
            identifier_value="918273645012",
        )
    )
    upi_id = ident_repo.create(
        CustomerPaymentIdentifier(
            id=uuid.uuid4(),
            company_id=company.id,
            customer_id=customer.id,
            identifier_type=IdentifierType.UPI_VPA,
            identifier_value="tcs@hdfcbank",
        )
    )

    # List
    idents = ident_repo.list_by_customer(customer.id, company.id)
    assert len(idents) == 2

    # Lookup by type and value
    found = ident_repo.get_by_type_and_value("UPI_VPA", "tcs@hdfcbank", company.id)
    assert found is not None
    assert found.id == upi_id.id

    # Delete
    assert ident_repo.delete(bank_id.id, company.id) is True
    remaining = ident_repo.list_by_customer(customer.id, company.id)
    assert len(remaining) == 1
