"""Integration tests for multi-tenant isolation, IDOR defense, and sensitive data masking."""

from uuid import uuid4
from fastapi.testclient import TestClient


def test_idor_cross_tenant_payment_access_fails_closed(
    client: TestClient, registered_owner: dict, second_company_owner: dict
):
    """Verify that Company B accessing Company A's payment returns fail-closed 404 Not Found."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company A creates a payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={
            "transaction_date": "2026-09-07",
            "amount": "10000.00",
            "currency": "INR",
            "narration": "CONFIDENTIAL PAYMENT CO_A",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    # Company B attempts to identify Company A's payment
    idor_resp = client.post(
        f"/api/v1/reconciliation/identify/{payment_id}",
        headers=headers_b,
    )
    # Must fail-closed with 404 Not Found to prevent existence disclosure
    assert idor_resp.status_code == 404
    body = idor_resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "PAYMENT_NOT_FOUND"


def test_cross_tenant_customer_coordinate_isolation(
    client: TestClient, registered_owner: dict, second_company_owner: dict
):
    """Verify Company A's payment cannot match against Company B's customer coordinates."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company B registers customer with specific bank account
    cust_b = client.post(
        "/api/v1/customers",
        headers=headers_b,
        json={"name": "Company B VIP Customer"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_b}/identifiers",
        headers=headers_b,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "777788889999"},
    )

    # Company A creates payment with the same bank account "777788889999"
    pay_a = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={
            "transaction_date": "2026-09-07",
            "amount": "5000.00",
            "currency": "INR",
            "narration": "TXN CO A",
            "bank_account_number": "777788889999",
        },
    ).json()["data"]["id"]

    # Company A identifies its payment
    id_resp = client.post(
        f"/api/v1/reconciliation/identify/{pay_a}",
        headers=headers_a,
    )
    assert id_resp.status_code == 200
    data = id_resp.json()["data"]
    # Must be UNKNOWN because Company B's customer is invisible across tenant boundary
    assert data["status"] == "UNKNOWN"
    assert data["primary_candidate"] is None
    assert len(data["candidates"]) == 0


def test_cross_tenant_customer_alias_isolation(
    client: TestClient, registered_owner: dict, second_company_owner: dict
):
    """Verify Company A's payment cannot match against Company B's customer alias."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company B registers customer with alias "SecretAgent"
    cust_b = client.post(
        "/api/v1/customers",
        headers=headers_b,
        json={"name": "Secret Agent LLC"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_b}/aliases",
        headers=headers_b,
        json={"alias_name": "SecretAgent"},
    )

    # Company A creates payment with payer_raw_name "SecretAgent"
    pay_a = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={
            "transaction_date": "2026-09-07",
            "amount": "1200.00",
            "currency": "INR",
            "narration": "TRANSFER",
            "payer_raw_name": "SecretAgent",
        },
    ).json()["data"]["id"]

    id_resp = client.post(
        f"/api/v1/reconciliation/identify/{pay_a}",
        headers=headers_a,
    )
    assert id_resp.status_code == 200
    data = id_resp.json()["data"]
    assert data["status"] == "UNKNOWN"
    assert data["primary_candidate"] is None


def test_unauthenticated_requests_rejected(client: TestClient):
    """Verify unauthenticated requests return 401 Unauthorized."""
    random_id = uuid4()
    resp1 = client.post(f"/api/v1/reconciliation/identify/{random_id}")
    assert resp1.status_code == 401

    resp2 = client.post("/api/v1/reconciliation/identify-batch", json={})
    assert resp2.status_code == 401


def test_sensitive_coordinate_masking_in_api(client: TestClient, registered_owner: dict):
    """Verify banking coordinates and UPI VPAs are masked in responses."""
    headers = registered_owner["headers"]

    cust = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Mask Test Customer"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "112233445566"},
    )

    pay = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "800.00",
            "currency": "INR",
            "narration": "MASK TEST",
            "bank_account_number": "112233445566",
        },
    ).json()["data"]["id"]

    resp = client.post(f"/api/v1/reconciliation/identify/{pay}", headers=headers)
    assert resp.status_code == 200
    signals = resp.json()["data"]["evidence_signals"]
    for sig in signals:
        if sig["evidence_type"] == "EXACT_BANK_ACCOUNT":
            assert sig["matched_value"] == "********5566"
            assert "11223344" not in sig["matched_value"]
