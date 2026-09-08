"""Integration tests for counterparty identification REST APIs."""

from fastapi.testclient import TestClient


def test_reconciliation_status_probe(client: TestClient):
    """Verify reconciliation module status endpoint returns initialized."""
    resp = client.get("/api/v1/reconciliation/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["module"] == "reconciliation"
    assert data["status"] == "initialized"


def test_identify_single_payment_bank_account_match(client: TestClient, registered_owner: dict):
    """Verify end-to-end identification via direct customer bank account coordinate."""
    headers = registered_owner["headers"]

    # 1. Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Delta Industrial Supplies Ltd", "tax_id": "GSTIN998877"},
    )
    assert cust_resp.status_code == 201
    cust_id = cust_resp.json()["data"]["id"]

    # 2. Add Bank Account Identifier to Customer
    ident_resp = client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={
            "identifier_type": "BANK_ACCOUNT",
            "identifier_value": "998877665544",
        },
    )
    assert ident_resp.status_code == 201

    # 3. Create Incoming Payment with matching bank account
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "45000.00",
            "currency": "INR",
            "narration": "NEFT TRANSFER FROM CLIENT",
            "bank_account_number": "998877665544",
            "payer_raw_name": "UNKNOWN",
        },
    )
    assert pay_resp.status_code == 201
    payment_id = pay_resp.json()["data"]["id"]

    # 4. Invoke Identification Endpoint
    id_resp = client.post(
        f"/api/v1/reconciliation/identify/{payment_id}",
        headers=headers,
    )
    assert id_resp.status_code == 200
    res = id_resp.json()
    assert res["success"] is True
    data = res["data"]
    assert data["status"] == "IDENTIFIED"
    assert data["total_evidence_score"] == 100.0
    assert data["primary_candidate"]["customer_id"] == cust_id
    assert data["primary_candidate"]["customer_name"] == "Delta Industrial Supplies Ltd"
    # Banking coordinates must be masked in response
    assert any(s["matched_value"] == "********5544" for s in data["evidence_signals"])


def test_identify_single_payment_alias_match(client: TestClient, registered_owner: dict):
    """Verify end-to-end identification via registered customer statement alias."""
    headers = registered_owner["headers"]

    # 1. Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Tata Consultancy Services Ltd"},
    )
    assert cust_resp.status_code == 201
    cust_id = cust_resp.json()["data"]["id"]

    # 2. Add Alias
    alias_resp = client.post(
        f"/api/v1/customers/{cust_id}/aliases",
        headers=headers,
        json={"alias_name": "TCS"},
    )
    assert alias_resp.status_code == 201

    # 3. Create Incoming Payment with payer_raw_name = "TCS"
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "120000.00",
            "currency": "INR",
            "narration": "TCS BILL PAYMENT",
            "payer_raw_name": "TCS",
        },
    )
    assert pay_resp.status_code == 201
    payment_id = pay_resp.json()["data"]["id"]

    # 4. Invoke Identification Endpoint
    id_resp = client.post(
        f"/api/v1/reconciliation/identify/{payment_id}",
        headers=headers,
    )
    assert id_resp.status_code == 200
    data = id_resp.json()["data"]
    assert data["status"] == "IDENTIFIED"
    assert data["primary_candidate"]["customer_id"] == cust_id
    assert data["primary_candidate"]["composite_score"] == 85.0


def test_batch_identify_unreconciled_payments(client: TestClient, registered_owner: dict):
    """Verify batch identification across unreconciled payments in workspace."""
    headers = registered_owner["headers"]

    # Create Customer with UPI ID
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Omni Retail Pvt Ltd"},
    )
    cust_id = cust_resp.json()["data"]["id"]
    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "UPI_VPA", "identifier_value": "omni@icici"},
    )

    # Payment 1: matches customer UPI in narration
    p1 = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "1500.00",
            "currency": "INR",
            "narration": "UPI-12345-omni@icici",
        },
    ).json()["data"]["id"]

    # Payment 2: unknown cash receipt
    p2 = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "200.00",
            "currency": "INR",
            "narration": "CASH COUNTER DEPOSIT",
        },
    ).json()["data"]["id"]

    # Batch identification
    resp = client.post(
        "/api/v1/reconciliation/identify-batch",
        headers=headers,
        json={"limit": 10},
    )
    assert resp.status_code == 200
    batch_data = resp.json()["data"]
    assert batch_data["total_evaluated"] >= 2
    res_map = {r["payment_id"]: r for r in batch_data["results"]}

    assert res_map[p1]["status"] == "IDENTIFIED"
    assert res_map[p1]["primary_candidate"]["customer_id"] == cust_id
    assert res_map[p2]["status"] == "UNKNOWN"


def test_batch_identify_explicit_payment_ids(client: TestClient, registered_owner: dict):
    """Verify batch identification with explicit payment IDs list."""
    headers = registered_owner["headers"]

    p1 = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "500.00",
            "currency": "INR",
            "narration": "REF1",
        },
    ).json()["data"]["id"]

    resp = client.post(
        "/api/v1/reconciliation/identify-batch",
        headers=headers,
        json={"payment_ids": [p1]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_evaluated"] == 1
    assert data["results"][0]["payment_id"] == p1


def test_batch_identify_limit_validation(client: TestClient, registered_owner: dict):
    """Verify requesting more than 100 payments triggers validation error."""
    headers = registered_owner["headers"]
    resp = client.post(
        "/api/v1/reconciliation/identify-batch",
        headers=headers,
        json={"limit": 150},
    )
    assert resp.status_code == 422
