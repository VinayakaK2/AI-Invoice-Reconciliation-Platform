"""Multi-tenant security (IDOR) integration tests for Phase 14.1 Payment Intake.

Verifies:
- Strict tenant boundary isolation (Company A cannot access Company B's payments).
- Fail-closed IDOR security: Cross-tenant access returns 404 Not Found (never 403 or 200).
- Unauthenticated requests are rejected with 401 Unauthorized.
- Batch intake strictly isolates queries to authenticated tenant context.
"""

from uuid import uuid4
from fastapi.testclient import TestClient


def test_cross_tenant_intake_returns_404(
    client: TestClient, registered_owner: dict, second_company_owner: dict
):
    """Fail-closed IDOR test: Company B must receive 404 when querying Company A's payment."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # 1. Company A creates a payment
    pay_a_resp = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={
            "transaction_date": "2026-09-08",
            "amount": "100000.00",
            "currency": "INR",
            "narration": "CONFIDENTIAL SETTLEMENT CO A",
        },
    )
    assert pay_a_resp.status_code == 201
    pay_a_id = pay_a_resp.json()["data"]["id"]

    # 2. Company B attempts to evaluate Company A's payment
    cross_resp = client.post(
        f"/api/v1/reconciliation/intake/{pay_a_id}",
        headers=headers_b,
    )

    # Must return 404 Not Found (NOT 403 Forbidden which leaks entity existence)
    assert cross_resp.status_code == 404
    err = cross_resp.json()["error"]
    assert "Payment" in err["message"]
    assert "not found" in err["message"].lower()


def test_cross_tenant_batch_intake_isolation(
    client: TestClient, registered_owner: dict, second_company_owner: dict
):
    """Verify batch intake only processes payments owned by the authenticated company."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # 1. Company A creates payment
    pay_a = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={"transaction_date": "2026-09-08", "amount": "15000.00", "currency": "INR", "narration": "CO A"},
    ).json()["data"]["id"]

    # 2. Company B creates payment
    pay_b = client.post(
        "/api/v1/payments",
        headers=headers_b,
        json={"transaction_date": "2026-09-08", "amount": "25000.00", "currency": "INR", "narration": "CO B"},
    ).json()["data"]["id"]

    # 3. Company B calls batch intake with both payment IDs [pay_a, pay_b]
    batch_resp = client.post(
        "/api/v1/reconciliation/intake-batch",
        headers=headers_b,
        json={"payment_ids": [pay_a, pay_b]},
    )
    assert batch_resp.status_code == 200
    data = batch_resp.json()["data"]

    # Company B should only evaluate pay_b; pay_a must be completely filtered out
    assert data["total_evaluated"] == 1
    assert data["results"][0]["payment_id"] == pay_b

    # 4. Company B calls batch intake requesting ONLY Company A's payment
    batch_cross_only = client.post(
        "/api/v1/reconciliation/intake-batch",
        headers=headers_b,
        json={"payment_ids": [pay_a]},
    )
    assert batch_cross_only.status_code == 200
    assert batch_cross_only.json()["data"]["total_evaluated"] == 0
    assert len(batch_cross_only.json()["data"]["results"]) == 0


def test_unauthenticated_intake_rejected_401(client: TestClient):
    """Verify unauthenticated requests are rejected with 401 Unauthorized."""
    random_id = uuid4()
    resp1 = client.post(f"/api/v1/reconciliation/intake/{random_id}")
    assert resp1.status_code == 401

    resp2 = client.post("/api/v1/reconciliation/intake-batch", json={})
    assert resp2.status_code == 401


def test_nonexistent_payment_returns_404(client: TestClient, registered_owner: dict):
    """Verify non-existent payment ID returns 404 Not Found within authenticated tenant."""
    headers = registered_owner["headers"]
    random_id = uuid4()

    resp = client.post(
        f"/api/v1/reconciliation/intake/{random_id}",
        headers=headers,
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["error"]["message"].lower()
