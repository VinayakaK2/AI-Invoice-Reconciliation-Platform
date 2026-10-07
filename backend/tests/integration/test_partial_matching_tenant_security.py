"""Security and Multi-Tenant Isolation integration tests for Phase 14.6 Partial 1:1 Matching.

Verifies:
- Strict fail-closed IDOR defense (404 Not Found on cross-tenant payment access)
- Strict fail-closed IDOR defense (404 Not Found on cross-tenant customer override)
- Cross-tenant candidate bleed immunity (identical invoice numbers and amounts never match cross-tenant)
- Unauthenticated rejection (401 Unauthorized)
"""

from decimal import Decimal
import uuid
from fastapi.testclient import TestClient


def test_partial_match_cross_tenant_payment_idor(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Verify that Company B cannot evaluate partial match for Company A's payment (404 Fail-closed)."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company A creates a payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={
            "transaction_date": "2026-09-01",
            "amount": "20000.00",
            "currency": "INR",
            "narration": "COMPANY A CONFIDENTIAL SETTLEMENT",
        },
    )
    assert pay_resp.status_code == 201
    payment_id_a = pay_resp.json()["data"]["id"]

    # Company B attempts to call partial match on Company A's payment
    idor_resp = client.post(
        f"/api/v1/reconciliation/partial-match/{payment_id_a}",
        headers=headers_b,
    )

    # Must return 404 Not Found (fail-closed IDOR defense)
    assert idor_resp.status_code == 404
    err = idor_resp.json()
    assert err["success"] is False
    assert err["error"]["code"] == "PAYMENT_NOT_FOUND"


def test_partial_match_cross_tenant_customer_override_idor(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Verify that Company A cannot inject Company B's customer ID as an override (404 Fail-closed)."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company B creates a customer
    cust_b_resp = client.post(
        "/api/v1/customers",
        headers=headers_b,
        json={"name": "Company B Exclusive Client", "tax_id": "GSTIN-B-777"},
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

    # Company A attempts to partial match with override_customer_id = cust_b_id
    idor_resp = client.post(
        f"/api/v1/reconciliation/partial-match/{payment_a_id}",
        headers=headers_a,
        json={"override_customer_id": cust_b_id},
    )

    # Must return 404 Not Found (Company A cannot bind Company B's customer)
    assert idor_resp.status_code == 404
    err = idor_resp.json()
    assert err["success"] is False
    assert err["error"]["code"] == "CUSTOMER_NOT_FOUND"


def test_partial_match_cross_tenant_invoice_bleed_immunity(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Verify that identical customer names, amounts, and invoice numbers across tenants never bleed."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Both companies create customer with identical name "Twin Client"
    cust_a = client.post(
        "/api/v1/customers",
        headers=headers_a,
        json={"name": "Twin Client", "tax_id": "GSTIN-TWIN-A"},
    ).json()["data"]["id"]

    cust_b = client.post(
        "/api/v1/customers",
        headers=headers_b,
        json={"name": "Twin Client", "tax_id": "GSTIN-TWIN-B"},
    ).json()["data"]["id"]

    # Both companies register identical bank account for their respective customer
    client.post(
        f"/api/v1/customers/{cust_a}/identifiers",
        headers=headers_a,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "999900001111"},
    )
    client.post(
        f"/api/v1/customers/{cust_b}/identifiers",
        headers=headers_b,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "999900001111"},
    )

    # Company A creates Invoice INV-SHARED: 100,000.00 INR
    client.post(
        "/api/v1/invoices",
        headers=headers_a,
        json={
            "invoice_number": "INV-SHARED-01",
            "customer_id": cust_a,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 100000.00,
            "currency": "INR",
        },
    )

    # Company B creates payment: 40,000.00 INR for INV-SHARED-01 (Company B has NO invoices yet)
    pay_b = client.post(
        "/api/v1/payments",
        headers=headers_b,
        json={
            "transaction_date": "2026-08-15",
            "amount": "40000.00",
            "currency": "INR",
            "narration": "PARTIAL SETTLEMENT INV-SHARED-01",
            "bank_account_number": "999900001111",
        },
    ).json()["data"]["id"]

    # Company B evaluates partial match: MUST NOT see Company A's invoice!
    match_b = client.post(
        f"/api/v1/reconciliation/partial-match/{pay_b}",
        headers=headers_b,
    )
    assert match_b.status_code == 200
    res_b = match_b.json()["data"]

    # Company B has no invoices, so NO_PARTIAL_MATCH with NO_RETAINED_CANDIDATES
    assert res_b["status"] == "NO_PARTIAL_MATCH"
    assert res_b["matched_candidate"] is None
    assert res_b["total_partial_candidates_found"] == 0
    assert res_b["reason_code"] == "NO_RETAINED_CANDIDATES"


def test_partial_match_unauthenticated_rejected(client: TestClient) -> None:
    """Verify that unauthenticated requests to partial match endpoints are rejected (401)."""
    fake_id = uuid.uuid4()
    resp = client.post(f"/api/v1/reconciliation/partial-match/{fake_id}")
    assert resp.status_code == 401

    batch_resp = client.post("/api/v1/reconciliation/partial-match-batch", json={})
    assert batch_resp.status_code == 401
