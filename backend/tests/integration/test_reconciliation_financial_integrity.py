"""Integration tests verifying strict financial state immutability during customer identification."""

from datetime import date
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.customer.infrastructure.models import CustomerModel, CustomerPaymentIdentifierModel
from app.modules.invoice.infrastructure.models import InvoiceModel
from app.modules.payment.infrastructure.models import PaymentModel
from app.modules.reconciliation.application.use_cases import IdentifyPaymentCustomerUseCase
from app.modules.reconciliation.infrastructure.adapters import (
    SQLAlchemyCustomerLookupAdapter,
    SQLAlchemyPaymentLookupAdapter,
)


def test_identification_does_not_mutate_payment_financial_state(
    client: TestClient, registered_owner: dict
):
    """Verify customer identification leaves payment balances and status strictly untouched."""
    headers = registered_owner["headers"]

    # 1. Create customer
    cust = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Financial Immutability Co"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "555566667777"},
    )

    # 2. Create Payment candidate
    pay = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "25000.00",
            "currency": "INR",
            "narration": "NEFT PAYMENT",
            "bank_account_number": "555566667777",
        },
    ).json()["data"]["id"]

    # Verify initial payment state
    p_before = client.get(f"/api/v1/payments/{pay}", headers=headers).json()["data"]
    assert p_before["allocated_amount"] == "0.00"
    assert p_before["unallocated_amount"] == "25000.00"
    assert p_before["status"] == "UNRECONCILED"

    # 3. Run customer identification
    id_resp = client.post(f"/api/v1/reconciliation/identify/{pay}", headers=headers)
    assert id_resp.status_code == 200
    assert id_resp.json()["data"]["status"] == "IDENTIFIED"

    # 4. Verify payment state after identification
    p_after = client.get(f"/api/v1/payments/{pay}", headers=headers).json()["data"]
    assert p_after["allocated_amount"] == "0.00"
    assert p_after["unallocated_amount"] == "25000.00"
    assert p_after["status"] == "UNRECONCILED"


def test_identification_does_not_mutate_invoice_financial_state(
    client: TestClient, registered_owner: dict
):
    """Verify customer identification leaves outstanding invoice balances strictly untouched."""
    headers = registered_owner["headers"]

    # 1. Create customer
    cust = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Invoice Immutability Customer"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "888899990000"},
    )

    # 2. Create Invoice
    inv = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-IMMUTABLE-001",
            "customer_id": cust,
            "issue_date": "2026-09-01",
            "due_date": "2026-09-30",
            "total_amount": "50000.00",
            "currency": "INR",
        },
    ).json()["data"]
    inv_id = inv["id"]

    # Verify initial invoice state
    inv_before = client.get(f"/api/v1/invoices/{inv_id}", headers=headers).json()["data"]
    assert inv_before["paid_amount"] == "0.00"
    assert inv_before["outstanding_amount"] == "50000.00"
    assert inv_before["status"] == "PENDING"

    # 3. Create payment and run identification
    pay = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "50000.00",
            "currency": "INR",
            "narration": "INVOICE IMMUTABLE PAYMENT",
            "bank_account_number": "888899990000",
        },
    ).json()["data"]["id"]

    id_resp = client.post(f"/api/v1/reconciliation/identify/{pay}", headers=headers)
    assert id_resp.status_code == 200

    # 4. Verify invoice state remains 100% unchanged
    inv_after = client.get(f"/api/v1/invoices/{inv_id}", headers=headers).json()["data"]
    assert inv_after["paid_amount"] == "0.00"
    assert inv_after["outstanding_amount"] == "50000.00"
    assert inv_after["status"] == "PENDING"


def test_session_dirty_checking_guarantee(db_session: Session, registered_owner: dict):
    """Verify database session remains pristine (no dirty, new, or deleted objects) during identification."""
    comp_id = registered_owner["company"]["id"]

    # Seed customer and payment directly via session
    cust = CustomerModel(
        company_id=comp_id,
        name="Session Dirty Checking Corp",
    )
    db_session.add(cust)
    db_session.flush()

    ident = CustomerPaymentIdentifierModel(
        company_id=comp_id,
        customer_id=cust.id,
        identifier_type="BANK_ACCOUNT",
        identifier_value="1234509876",
        is_active=True,
    )
    db_session.add(ident)

    pay = PaymentModel(
        company_id=comp_id,
        transaction_date=date(2026, 9, 7),
        amount=Decimal("1000.00"),
        allocated_amount=Decimal("0.00"),
        unallocated_amount=Decimal("1000.00"),
        currency="INR",
        narration="TEST",
        bank_account_number="1234509876",
        status="UNRECONCILED",
    )
    db_session.add(pay)
    db_session.commit()

    # Pre-condition: Clean session
    assert len(db_session.dirty) == 0
    assert len(db_session.new) == 0
    assert len(db_session.deleted) == 0

    # Execute Use Case
    use_case = IdentifyPaymentCustomerUseCase(
        payment_lookup_port=SQLAlchemyPaymentLookupAdapter(db_session),
        customer_lookup_port=SQLAlchemyCustomerLookupAdapter(db_session),
    )
    result = use_case.execute(payment_id=pay.id, company_id=comp_id)

    assert result.status.value == "IDENTIFIED"

    # Post-condition: Zero mutations in session
    assert len(db_session.dirty) == 0
    assert len(db_session.new) == 0
    assert len(db_session.deleted) == 0
