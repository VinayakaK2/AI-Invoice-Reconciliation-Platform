"""Integration tests for Phase 13.2 Candidate Invoice Generation REST API."""

from decimal import Decimal
import uuid
from fastapi.testclient import TestClient


def test_generate_candidates_with_identified_customer(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that a payment with auto-identified customer retrieves and ranks candidate invoices."""
    headers = registered_owner["headers"]

    # 1. Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Acrobat Logistics Private Limited", "tax_id": "GSTIN123456"},
    )
    assert cust_resp.status_code == 201
    cust_id = cust_resp.json()["data"]["id"]

    # 2. Register Bank Account for Customer
    ident_resp = client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "112233445566"},
    )
    assert ident_resp.status_code == 201

    # 3. Create Open Invoices for Customer
    inv1_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-2026-AC1",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 10000.00,
            "currency": "INR",
        },
    )
    assert inv1_resp.status_code == 201
    inv1_id = inv1_resp.json()["data"]["id"]

    inv2_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-2026-AC2",
            "customer_id": cust_id,
            "issue_date": "2026-08-15",
            "due_date": "2026-09-15",
            "total_amount": 25000.00,
            "currency": "INR",
        },
    )
    assert inv2_resp.status_code == 201

    # 4. Create Payment matching Customer's bank account & exact amount for inv1
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-31",
            "amount": "10000.00",
            "currency": "INR",
            "narration": "NEFT TRANSFER FOR INV-2026-AC1",
            "bank_account_number": "112233445566",
        },
    )
    assert pay_resp.status_code == 201
    payment_id = pay_resp.json()["data"]["id"]

    # 5. Call Candidate Generation API
    cand_resp = client.post(
        f"/api/v1/reconciliation/candidates/{payment_id}",
        headers=headers,
    )
    assert cand_resp.status_code == 200
    res = cand_resp.json()
    assert res["success"] is True

    data = res["data"]
    assert data["payment_id"] == payment_id
    assert data["customer_id"] == cust_id
    assert data["status_code"] == "SUCCESS"
    assert data["total_eligible_invoices"] == 2
    assert len(data["candidates"]) == 2

    top_candidate = data["candidates"][0]
    assert top_candidate["invoice_id"] == inv1_id
    assert top_candidate["invoice_number"] == "INV-2026-AC1"
    assert top_candidate["is_exact_amount_match"] is True
    assert top_candidate["is_reference_match"] is True
    assert top_candidate["rank"] == 1
    assert top_candidate["retrieval_priority"] >= 90.0


def test_generate_candidates_unresolved_customer(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that an unknown payment returns CUSTOMER_UNRESOLVED and 0 candidate invoices."""
    headers = registered_owner["headers"]

    # Create Payment with no identifiable customer data
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-01",
            "amount": "5000.00",
            "currency": "INR",
            "narration": "CASH DEPOSIT UNKNOWN BRANCH",
        },
    )
    assert pay_resp.status_code == 201
    payment_id = pay_resp.json()["data"]["id"]

    # Call Candidate Generation API
    cand_resp = client.post(
        f"/api/v1/reconciliation/candidates/{payment_id}",
        headers=headers,
    )
    assert cand_resp.status_code == 200
    data = cand_resp.json()["data"]
    assert data["status_code"] == "CUSTOMER_UNRESOLVED"
    assert data["customer_id"] is None
    assert data["total_eligible_invoices"] == 0
    assert len(data["candidates"]) == 0
    assert "customer identification returned UNKNOWN" in data["truncation_reason"]


def test_generate_candidates_explicit_customer_override(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that specifying override_customer_id evaluates invoices for that customer directly."""
    headers = registered_owner["headers"]

    # 1. Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Overridden Partner Enterprises"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    # 2. Create Invoice for Customer
    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-2026-OVR1",
            "customer_id": cust_id,
            "issue_date": "2026-09-01",
            "due_date": "2026-09-15",
            "total_amount": 7500.00,
            "currency": "INR",
        },
    )
    assert inv_resp.status_code == 201
    inv_id = inv_resp.json()["data"]["id"]

    # 3. Create Payment with generic narration (would otherwise be UNKNOWN)
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-10",
            "amount": "7500.00",
            "currency": "INR",
            "narration": "GENERIC TRANSFER",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    # 4. Request candidates with customer override
    cand_resp = client.post(
        f"/api/v1/reconciliation/candidates/{payment_id}",
        headers=headers,
        json={"override_customer_id": cust_id},
    )
    assert cand_resp.status_code == 200
    data = cand_resp.json()["data"]
    assert data["status_code"] == "SUCCESS"
    assert data["customer_id"] == cust_id
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["invoice_id"] == inv_id


def test_generate_candidates_currency_mismatch(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that open invoices in a different currency produce CURRENCY_MISMATCH status."""
    headers = registered_owner["headers"]

    # Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Global Export Corp"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    # Create Invoice in USD
    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-USD-001",
            "customer_id": cust_id,
            "issue_date": "2026-09-01",
            "due_date": "2026-09-30",
            "total_amount": 500.00,
            "currency": "USD",
        },
    )
    assert inv_resp.status_code == 201

    # Create Payment in INR
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-05",
            "amount": "40000.00",
            "currency": "INR",
            "narration": "INR TRANSFER",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    # Request candidates with customer override
    cand_resp = client.post(
        f"/api/v1/reconciliation/candidates/{payment_id}",
        headers=headers,
        json={"override_customer_id": cust_id},
    )
    assert cand_resp.status_code == 200
    data = cand_resp.json()["data"]
    assert data["status_code"] == "CURRENCY_MISMATCH"
    assert data["currency_mismatches_detected"] == 1
    assert len(data["candidates"]) == 0
    assert "none match payment currency 'INR'" in data["truncation_reason"]


def test_generate_candidates_cross_tenant_idor_protection(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Verify that Company B cannot query candidate invoices for Company A's payment (404 Fail-closed)."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company A creates payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={
            "transaction_date": "2026-09-01",
            "amount": "1000.00",
            "currency": "INR",
            "narration": "COMPANY A SECRET PAYMENT",
        },
    )
    payment_id_a = pay_resp.json()["data"]["id"]

    # Company B attempts to access Company A's payment candidates
    idor_resp = client.post(
        f"/api/v1/reconciliation/candidates/{payment_id_a}",
        headers=headers_b,
    )
    assert idor_resp.status_code == 404


def test_candidate_generation_financial_state_immutability(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify 0.00% financial state mutation: candidate evaluation does NOT mutate any payment or invoice state."""
    headers = registered_owner["headers"]

    # Setup Customer, Invoice, and Payment
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Immutable Dynamics Ltd"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-IMMUT-001",
            "customer_id": cust_id,
            "issue_date": "2026-09-01",
            "due_date": "2026-09-30",
            "total_amount": 50000.00,
            "currency": "INR",
        },
    )
    inv_id = inv_resp.json()["data"]["id"]

    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-15",
            "amount": "50000.00",
            "currency": "INR",
            "narration": "FOR INV-IMMUT-001",
        },
    )
    pay_id = pay_resp.json()["data"]["id"]

    # 1. Fetch pre-evaluation states
    pre_pay = client.get(f"/api/v1/payments/{pay_id}", headers=headers).json()["data"]
    pre_inv = client.get(f"/api/v1/invoices/{inv_id}", headers=headers).json()["data"]

    # 2. Invoke Candidate Generation multiple times
    for _ in range(3):
        cand_resp = client.post(
            f"/api/v1/reconciliation/candidates/{pay_id}",
            headers=headers,
            json={"override_customer_id": cust_id},
        )
        assert cand_resp.status_code == 200

    # 3. Fetch post-evaluation states
    post_pay = client.get(f"/api/v1/payments/{pay_id}", headers=headers).json()["data"]
    post_inv = client.get(f"/api/v1/invoices/{inv_id}", headers=headers).json()["data"]

    # 4. Assert absolute zero mutation
    assert pre_pay["status"] == post_pay["status"] == "UNRECONCILED"
    assert Decimal(str(pre_pay["amount"])) == Decimal(str(post_pay["amount"])) == Decimal("50000.00")
    assert Decimal(str(pre_pay["allocated_amount"])) == Decimal(str(post_pay["allocated_amount"])) == Decimal("0.00")
    assert Decimal(str(pre_pay["unallocated_amount"])) == Decimal(str(post_pay["unallocated_amount"])) == Decimal("50000.00")

    assert pre_inv["status"] == post_inv["status"] == "PENDING"
    assert Decimal(str(pre_inv["total_amount"])) == Decimal(str(post_inv["total_amount"])) == Decimal("50000.00")
    assert Decimal(str(pre_inv["paid_amount"])) == Decimal(str(post_inv["paid_amount"])) == Decimal("0.00")
    assert Decimal(str(pre_inv["outstanding_amount"])) == Decimal(str(post_inv["outstanding_amount"])) == Decimal("50000.00")
