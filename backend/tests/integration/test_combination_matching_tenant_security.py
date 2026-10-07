"""Security and Multi-Tenant Isolation integration tests for Phase 14.8 Combination Matching.

Verifies:
- Strict fail-closed IDOR defense (404 Not Found on cross-tenant payment access)
- Strict fail-closed IDOR defense (404 Not Found on cross-tenant customer override)
- Cross-tenant candidate bleed immunity (identical invoice numbers and amounts never match cross-tenant)
- Unauthenticated rejection (401 Unauthorized)
"""

from decimal import Decimal
import uuid
from fastapi.testclient import TestClient


def test_combination_match_cross_tenant_payment_idor(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Verify that Company B cannot evaluate combination match for Company A's payment (404 Fail-closed)."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company A creates a payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={
            "transaction_date": "2026-09-01",
            "amount": "50000.00",
            "currency": "INR",
            "narration": "COMPANY A CONFIDENTIAL COMBINATION SETTLEMENT",
        },
    )
    assert pay_resp.status_code == 201
    payment_id_a = pay_resp.json()["data"]["id"]

    # Company B attempts to call combination match on Company A's payment
    idor_resp = client.post(
        f"/api/v1/reconciliation/combination-match/{payment_id_a}",
        headers=headers_b,
    )

    # Must return 404 Not Found (fail-closed IDOR defense)
    assert idor_resp.status_code == 404
    err = idor_resp.json()
    assert err["success"] is False
    assert err["error"]["code"] == "PAYMENT_NOT_FOUND"


def test_combination_match_cross_tenant_customer_override_idor(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Verify that Company A cannot inject Company B's customer ID as an override (404 Fail-closed)."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company B creates a customer
    cust_b_resp = client.post(
        "/api/v1/customers",
        headers=headers_b,
        json={"name": "Company B Exclusive Client", "tax_id": "GSTIN-B-COMBO"},
    )
    assert cust_b_resp.status_code == 201
    cust_id_b = cust_b_resp.json()["data"]["id"]

    # Company A creates a payment
    pay_a_resp = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={
            "transaction_date": "2026-09-01",
            "amount": "30000.00",
            "currency": "INR",
            "narration": "COMPANY A PAYMENT ATTEMPTING OVERRIDE",
        },
    )
    assert pay_a_resp.status_code == 201
    payment_id_a = pay_a_resp.json()["data"]["id"]

    # Company A calls combination match passing Company B's customer_id as override
    override_resp = client.post(
        f"/api/v1/reconciliation/combination-match/{payment_id_a}",
        headers=headers_a,
        json={"override_customer_id": cust_id_b},
    )

    # Must reject with 404 Not Found (customer does not exist in Company A's tenant)
    assert override_resp.status_code == 404
    err = override_resp.json()
    assert err["success"] is False
    assert err["error"]["code"] == "CUSTOMER_NOT_FOUND"


def test_combination_match_cross_tenant_candidate_bleed_immunity(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Verify that Company A's payment NEVER matches Company B's invoices even with identical numbers/amounts."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company B creates customer and invoices totaling 50,000 INR
    cust_b_id = client.post(
        "/api/v1/customers",
        headers=headers_b,
        json={"name": "Bleed Defense Target", "tax_id": "GSTIN-BLEED-COMBO"},
    ).json()["data"]["id"]

    client.post(
        "/api/v1/invoices",
        headers=headers_b,
        json={
            "invoice_number": "INV-BLEED-COMBO-01",
            "customer_id": cust_b_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 20000.00,
            "currency": "INR",
        },
    )
    client.post(
        "/api/v1/invoices",
        headers=headers_b,
        json={
            "invoice_number": "INV-BLEED-COMBO-02",
            "customer_id": cust_b_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 30000.00,
            "currency": "INR",
        },
    )

    # Company A creates payment with exact same amount and narration referencing Company B invoices
    pay_a_id = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={
            "transaction_date": "2026-08-15",
            "amount": "50000.00",
            "currency": "INR",
            "narration": "PAYMENT FOR INV-BLEED-COMBO-01 AND INV-BLEED-COMBO-02",
        },
    ).json()["data"]["id"]

    # Company A calls combination match
    match_resp = client.post(
        f"/api/v1/reconciliation/combination-match/{pay_a_id}",
        headers=headers_a,
    )
    assert match_resp.status_code == 200
    res = match_resp.json()
    data = res["data"]

    # Must NOT match Company B's invoices! Zero tenant bleed!
    assert data["status"] == "NO_COMBINATION_MATCH"
    assert data["total_combinations_found"] == 0
    assert data["prioritized_combination"] is None


def test_combination_match_unauthenticated_rejected(client: TestClient) -> None:
    """Verify unauthenticated access to combination match endpoints returns 401 Unauthorized."""
    random_id = uuid.uuid4()
    resp = client.post(f"/api/v1/reconciliation/combination-match/{random_id}")
    assert resp.status_code == 401

    batch_resp = client.post(
        "/api/v1/reconciliation/combination-match-batch",
        json={"payment_ids": [str(random_id)]},
    )
    assert batch_resp.status_code == 401
