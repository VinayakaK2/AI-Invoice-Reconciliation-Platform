"""Integration tests for Phase 14.4 Candidate Filtering REST API and pipeline.

Verifies:
- End-to-end HTTP endpoint POST /api/v1/reconciliation/candidates/{payment_id}/filtered
- Zero financial accounting state mutation (Rule 4)
- Live SQLAlchemy session dirty check (len(db.dirty) == 0)
- Graceful handling of empty, currency-mismatched, and unresolved universes
"""

from decimal import Decimal
import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.reconciliation.application.use_cases import (
    FilterCandidateInvoicesUseCase,
)
from app.modules.reconciliation.domain.candidate_filters import (
    CandidateFilterCriteria,
    FilterExclusionReason,
)
from app.modules.reconciliation.infrastructure.adapters import (
    SQLAlchemyCustomerLookupAdapter,
    SQLAlchemyInvoiceLookupAdapter,
    SQLAlchemyPaymentLookupAdapter,
)


def test_filter_candidates_api_e2e(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that filtering endpoint correctly retains and excludes candidates with machine-readable reasons."""
    headers = registered_owner["headers"]

    # 1. Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Filter Logistics Private Limited", "tax_id": "GSTIN777888"},
    )
    assert cust_resp.status_code == 201
    cust_id = cust_resp.json()["data"]["id"]

    # 2. Register Bank Account for Customer
    ident_resp = client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "998877112233"},
    )
    assert ident_resp.status_code == 201

    # 3. Create Invoices:
    # Inv 1: Valid causal invoice (10,000 INR)
    inv1_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-FL-001",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 10000.00,
            "currency": "INR",
        },
    )
    assert inv1_resp.status_code == 201
    inv1_id = inv1_resp.json()["data"]["id"]

    # Inv 2: Future non-causal invoice (issued after payment date 2026-08-31)
    inv2_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-FL-002",
            "customer_id": cust_id,
            "issue_date": "2026-09-15",
            "due_date": "2026-09-30",
            "total_amount": 12000.00,
            "currency": "INR",
        },
    )
    assert inv2_resp.status_code == 201
    inv2_id = inv2_resp.json()["data"]["id"]

    # Inv 3: High amount invoice (50,000 INR)
    inv3_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-FL-003",
            "customer_id": cust_id,
            "issue_date": "2026-08-05",
            "due_date": "2026-08-25",
            "total_amount": 50000.00,
            "currency": "INR",
        },
    )
    assert inv3_resp.status_code == 201
    inv3_id = inv3_resp.json()["data"]["id"]

    # 4. Create Payment matching Customer
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-31",
            "amount": "10000.00",
            "currency": "INR",
            "narration": "NEFT TRANSFER FROM FILTER LOGISTICS",
            "bank_account_number": "998877112233",
        },
    )
    assert pay_resp.status_code == 201
    payment_id = pay_resp.json()["data"]["id"]

    # 5. Call Filter Endpoint with criteria: require_causality=True, max_amount=20000.00
    filter_resp = client.post(
        f"/api/v1/reconciliation/candidates/{payment_id}/filtered",
        headers=headers,
        json={
            "require_causality": True,
            "max_amount": 20000.00,
            "max_candidates": 30,
        },
    )
    assert filter_resp.status_code == 200
    res = filter_resp.json()
    assert res["success"] is True

    data = res["data"]
    assert data["payment_id"] == payment_id
    assert data["customer_id"] == cust_id
    assert data["status_code"] == "SUCCESS"
    assert data["total_evaluated"] == 3
    assert data["total_retained"] == 1
    assert data["total_excluded"] == 2

    # Retained candidate should be Inv 1
    retained = data["retained_candidates"]
    assert len(retained) == 1
    assert retained[0]["invoice_id"] == inv1_id
    assert retained[0]["invoice_number"] == "INV-FL-001"
    assert retained[0]["rank"] == 1

    # Excluded candidates should contain Inv 2 (NON_CAUSAL_DATE) and Inv 3 (AMOUNT_ABOVE_MAXIMUM)
    excluded = data["excluded_candidates"]
    assert len(excluded) == 2
    excluded_map = {e["invoice_id"]: e for e in excluded}

    assert inv2_id in excluded_map
    assert FilterExclusionReason.NON_CAUSAL_DATE.value in excluded_map[inv2_id]["exclusion_reasons"]

    assert inv3_id in excluded_map
    assert FilterExclusionReason.AMOUNT_ABOVE_MAXIMUM.value in excluded_map[inv3_id]["exclusion_reasons"]

    # Check breakdown
    assert data["exclusion_breakdown"][FilterExclusionReason.NON_CAUSAL_DATE.value] == 1
    assert data["exclusion_breakdown"][FilterExclusionReason.AMOUNT_ABOVE_MAXIMUM.value] == 1


def test_filter_candidates_zero_financial_state_mutation(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that repeated candidate filtering runs cause 0 financial mutations."""
    headers = registered_owner["headers"]

    # Setup customer, invoice, payment
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Zero Mutation Customer", "tax_id": "GSTIN-ZM-01"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "443322110099"},
    )

    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-ZM-001",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 15000.00,
            "currency": "INR",
        },
    )
    inv_id = inv_resp.json()["data"]["id"]

    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "15000.00",
            "currency": "INR",
            "narration": "NEFT TRANSFER FOR INV-ZM-001",
            "bank_account_number": "443322110099",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    # Initial state snapshots
    pay_before = client.get(f"/api/v1/payments/{payment_id}", headers=headers).json()["data"]
    inv_before = client.get(f"/api/v1/invoices/{inv_id}", headers=headers).json()["data"]

    # Call filtering endpoint 5 times
    for _ in range(5):
        resp = client.post(
            f"/api/v1/reconciliation/candidates/{payment_id}/filtered",
            headers=headers,
            json={"require_causality": True},
        )
        assert resp.status_code == 200

    # Post state snapshots
    pay_after = client.get(f"/api/v1/payments/{payment_id}", headers=headers).json()["data"]
    inv_after = client.get(f"/api/v1/invoices/{inv_id}", headers=headers).json()["data"]

    # Assert 0 financial mutation
    assert pay_before["status"] == pay_after["status"] == "UNRECONCILED"
    assert pay_before["amount"] == pay_after["amount"] == "15000.00"
    assert pay_before["unallocated_amount"] == pay_after["unallocated_amount"] == "15000.00"
    assert pay_before["allocated_amount"] == pay_after["allocated_amount"] == "0.00"

    assert inv_before["status"] == inv_after["status"] == "PENDING"
    assert inv_before["total_amount"] == inv_after["total_amount"] == "15000.00"
    assert inv_before["outstanding_amount"] == inv_after["outstanding_amount"] == "15000.00"
    assert inv_before["paid_amount"] == inv_after["paid_amount"] == "0.00"


def test_filter_candidates_sqlalchemy_dirty_check(
    db_session: Session, registered_owner: dict, client: TestClient
) -> None:
    """Verify at the SQLAlchemy session level that filtering leaves session completely clean."""
    headers = registered_owner["headers"]
    company_id = uuid.UUID(registered_owner["company"]["id"])

    # Setup customer and payment via API
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Dirty Check Customer", "tax_id": "GSTIN-DC-01"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "556677889900"},
    )

    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-DC-001",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 5000.00,
            "currency": "INR",
        },
    )

    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "5000.00",
            "currency": "INR",
            "narration": "NEFT TRANSFER FOR INV-DC-001",
            "bank_account_number": "556677889900",
        },
    )
    payment_id = uuid.UUID(pay_resp.json()["data"]["id"])

    # Initialize adapters with live db_session
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db_session)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db_session)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db_session)

    use_case = FilterCandidateInvoicesUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )

    # Execute Use Case
    result = use_case.execute(
        payment_id=payment_id,
        company_id=company_id,
        criteria=CandidateFilterCriteria(),
    )

    assert result.status_code == "SUCCESS"
    assert result.total_retained == 1

    # CRITICAL INVARIANT: Zero session dirtiness
    assert len(db_session.dirty) == 0, f"Dirty objects found: {db_session.dirty}"
    assert len(db_session.new) == 0, f"New objects found: {db_session.new}"
    assert len(db_session.deleted) == 0, f"Deleted objects found: {db_session.deleted}"


def test_filter_candidates_empty_and_edge_universes(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify clean 200 OK responses with diagnostic status codes across edge cases."""
    headers = registered_owner["headers"]

    # Customer with zero invoices
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Empty Invoice Customer", "tax_id": "GSTIN-EMPTY-01"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "123123123123"},
    )

    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "5000.00",
            "currency": "INR",
            "narration": "NEFT TRANSFER FOR EMPTY INVOICE CUSTOMER",
            "bank_account_number": "123123123123",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    # Case A: No open invoices for customer
    resp_empty = client.post(
        f"/api/v1/reconciliation/candidates/{payment_id}/filtered",
        headers=headers,
    )
    assert resp_empty.status_code == 200
    data_empty = resp_empty.json()["data"]
    assert data_empty["status_code"] == "NO_ELIGIBLE_INVOICES"
    assert data_empty["total_retained"] == 0
    assert data_empty["total_excluded"] == 0
    assert data_empty["retained_candidates"] == []

    # Case B: Currency mismatch (invoice in USD, payment in INR)
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-USD-001",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 5000.00,
            "currency": "USD",
        },
    )

    resp_curr = client.post(
        f"/api/v1/reconciliation/candidates/{payment_id}/filtered",
        headers=headers,
    )
    assert resp_curr.status_code == 200
    data_curr = resp_curr.json()["data"]
    assert data_curr["status_code"] == "CURRENCY_MISMATCH"
    assert data_curr["currency_mismatches_detected"] == 1
    assert data_curr["total_retained"] == 0

    # Case C: Unresolved customer (payment with unidentifiable narration)
    unresolved_pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "5000.00",
            "currency": "INR",
            "narration": "UNKNOWN CASH DESK CREDIT 9999",
        },
    )
    unresolved_id = unresolved_pay_resp.json()["data"]["id"]

    resp_unres = client.post(
        f"/api/v1/reconciliation/candidates/{unresolved_id}/filtered",
        headers=headers,
    )
    assert resp_unres.status_code == 200
    data_unres = resp_unres.json()["data"]
    assert data_unres["status_code"] == "CUSTOMER_UNRESOLVED"
    assert data_unres["customer_id"] is None
    assert data_unres["total_retained"] == 0
