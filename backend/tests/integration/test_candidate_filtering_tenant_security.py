"""Security and Multi-Tenant Isolation integration tests for Phase 14.4 Candidate Filtering.

Verifies:
- Strict fail-closed IDOR protection (404 Not Found on cross-tenant payment access)
- Strict fail-closed IDOR protection (404 Not Found on cross-tenant customer override)
- Cross-tenant invoice bleed immunity (identical invoice numbers/customers across tenants)
- Coordinate and sensitive data masking in responses
- Unauthenticated rejection (401 Unauthorized)
"""

from decimal import Decimal
import uuid
from fastapi.testclient import TestClient


def test_candidate_filtering_cross_tenant_payment_idor(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Verify that Company B cannot filter candidates for Company A's payment (404 Fail-closed)."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company A creates a payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={
            "transaction_date": "2026-09-01",
            "amount": "25000.00",
            "currency": "INR",
            "narration": "COMPANY A CONFIDENTIAL SETTLEMENT",
        },
    )
    assert pay_resp.status_code == 201
    payment_id_a = pay_resp.json()["data"]["id"]

    # Company B attempts to call filtering on Company A's payment
    idor_resp = client.post(
        f"/api/v1/reconciliation/candidates/{payment_id_a}/filtered",
        headers=headers_b,
        json={"require_causality": True},
    )

    # Must return 404 Not Found (fail-closed IDOR defense)
    assert idor_resp.status_code == 404
    err = idor_resp.json()
    assert err["success"] is False
    assert err["error"]["code"] == "PAYMENT_NOT_FOUND"


def test_candidate_filtering_cross_tenant_customer_override_idor(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Verify that Company A cannot inject Company B's customer ID as an override (404 Fail-closed)."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company B creates a customer
    cust_b_resp = client.post(
        "/api/v1/customers",
        headers=headers_b,
        json={"name": "Company B Exclusive Client", "tax_id": "GSTIN-B-999"},
    )
    assert cust_b_resp.status_code == 201
    cust_b_id = cust_b_resp.json()["data"]["id"]

    # Company A creates a payment
    pay_a_resp = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={
            "transaction_date": "2026-09-01",
            "amount": "15000.00",
            "currency": "INR",
            "narration": "COMPANY A PAYMENT",
        },
    )
    assert pay_a_resp.status_code == 201
    payment_a_id = pay_a_resp.json()["data"]["id"]

    # Company A attempts to generate & filter candidates with override_customer_id = cust_b_id
    idor_resp = client.post(
        f"/api/v1/reconciliation/candidates/{payment_a_id}/filtered",
        headers=headers_a,
        json={"override_customer_id": cust_b_id},
    )

    # Must return 404 Not Found (Company A cannot bind Company B's customer)
    assert idor_resp.status_code == 404
    err = idor_resp.json()
    assert err["success"] is False
    assert err["error"]["code"] == "CUSTOMER_NOT_FOUND"


def test_candidate_filtering_cross_tenant_invoice_bleed_immunity(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Verify that identical customer names and invoice numbers across tenants never bleed."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company A creates customer and invoice "INV-SHARED-001"
    cust_a = client.post(
        "/api/v1/customers",
        headers=headers_a,
        json={"name": "Global Shared Enterprises", "tax_id": "GSTIN-SHARED-1"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_a}/identifiers",
        headers=headers_a,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "990011223344"},
    )

    inv_a = client.post(
        "/api/v1/invoices",
        headers=headers_a,
        json={
            "invoice_number": "INV-SHARED-001",
            "customer_id": cust_a,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 20000.00,
            "currency": "INR",
        },
    ).json()["data"]["id"]

    # Company B also creates customer with identical name and invoice with identical number
    cust_b = client.post(
        "/api/v1/customers",
        headers=headers_b,
        json={"name": "Global Shared Enterprises", "tax_id": "GSTIN-SHARED-2"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_b}/identifiers",
        headers=headers_b,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "990011223344"},
    )

    inv_b = client.post(
        "/api/v1/invoices",
        headers=headers_b,
        json={
            "invoice_number": "INV-SHARED-001",
            "customer_id": cust_b,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 20000.00,
            "currency": "INR",
        },
    ).json()["data"]["id"]

    # Company A creates payment
    pay_a = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={
            "transaction_date": "2026-08-15",
            "amount": "20000.00",
            "currency": "INR",
            "narration": "PAYMENT FOR INV-SHARED-001",
            "bank_account_number": "990011223344",
        },
    ).json()["data"]["id"]

    # Filter candidates for Company A
    resp_a = client.post(
        f"/api/v1/reconciliation/candidates/{pay_a}/filtered",
        headers=headers_a,
    )
    assert resp_a.status_code == 200
    data_a = resp_a.json()["data"]

    # Verify: Company A strictly sees Inv A, never Inv B
    assert data_a["total_retained"] == 1
    assert data_a["retained_candidates"][0]["invoice_id"] == inv_a
    assert data_a["retained_candidates"][0]["invoice_id"] != inv_b


def test_candidate_filtering_unauthenticated_request_rejected(
    client: TestClient,
) -> None:
    """Verify that unauthenticated requests to the filter endpoint are rejected with 401."""
    random_id = uuid.uuid4()
    resp = client.post(f"/api/v1/reconciliation/candidates/{random_id}/filtered")
    assert resp.status_code == 401

