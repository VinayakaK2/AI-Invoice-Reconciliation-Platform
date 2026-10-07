"""Integration tests for Phase 14.5 Exact 1:1 Matching REST API.

Verifies:
- End-to-end HTTP endpoint POST /api/v1/reconciliation/exact-match/{payment_id}
- Batch endpoint POST /api/v1/reconciliation/exact-match-batch
- Zero financial accounting state mutation (Rule 4)
- Live database verification that payment and invoice balances remain untouched
- Unambiguous, ambiguous, and non-match API scenarios
- Graceful handling of unresolved customer contexts
"""

from decimal import Decimal
import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.reconciliation.domain.exact_matching import (
    ExactMatchReasonCode,
    ExactMatchStatus,
)


def test_exact_match_api_e2e_success(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that exact matching endpoint returns EXACT_MATCH for an eligible 1:1 match."""
    headers = registered_owner["headers"]

    # 1. Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Alpha Corp India", "tax_id": "GSTIN-ALPHA-01"},
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

    # 3. Create Invoice: 25,000.00 INR
    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-ALPHA-001",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 25000.00,
            "currency": "INR",
        },
    )
    assert inv_resp.status_code == 201
    inv_id = inv_resp.json()["data"]["id"]

    # 4. Create Payment matching Customer and Amount: 25,000.00 INR
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "25000.00",
            "currency": "INR",
            "narration": "NEFT TRANSFER FOR INV-ALPHA-001 FROM ALPHA CORP",
            "bank_account_number": "112233445566",
        },
    )
    assert pay_resp.status_code == 201
    payment_id = pay_resp.json()["data"]["id"]

    # 5. Call Exact Match Endpoint
    match_resp = client.post(
        f"/api/v1/reconciliation/exact-match/{payment_id}",
        headers=headers,
    )
    assert match_resp.status_code == 200
    res = match_resp.json()
    assert res["success"] is True

    data = res["data"]
    assert data["payment_id"] == payment_id
    assert data["customer_id"] == cust_id
    assert data["status"] == ExactMatchStatus.EXACT_MATCH.value
    assert data["total_exact_candidates_found"] == 1
    assert data["matched_candidate"] is not None

    cand = data["matched_candidate"]
    assert cand["invoice_id"] == inv_id
    assert cand["invoice_number"] == "INV-ALPHA-001"
    assert cand["matched_amount"] == "25000.00"
    assert cand["invoice_outstanding_after"] == "0.00"
    assert cand["payment_unallocated_after"] == "0.00"
    assert cand["is_reference_match"] is True
    assert len(cand["evidence_signals"]) >= 3


def test_exact_match_zero_financial_state_mutation(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that evaluating exact matching causes ZERO database mutation (Rule 4)."""
    headers = registered_owner["headers"]

    # Setup Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Zero Mutation Corp", "tax_id": "GSTIN-ZM-99"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "998811223344"},
    )

    # Setup Invoice
    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-ZM-999",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 18000.00,
            "currency": "INR",
        },
    )
    inv_id = inv_resp.json()["data"]["id"]

    # Setup Payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-20",
            "amount": "18000.00",
            "currency": "INR",
            "narration": "NEFT TRANSFER FOR INV-ZM-999",
            "bank_account_number": "998811223344",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    # Snapshots before
    pay_before = client.get(f"/api/v1/payments/{payment_id}", headers=headers).json()["data"]
    inv_before = client.get(f"/api/v1/invoices/{inv_id}", headers=headers).json()["data"]

    # Call Exact Match endpoint 5 times repeatedly
    for _ in range(5):
        resp = client.post(
            f"/api/v1/reconciliation/exact-match/{payment_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "EXACT_MATCH"

    # Snapshots after
    pay_after = client.get(f"/api/v1/payments/{payment_id}", headers=headers).json()["data"]
    inv_after = client.get(f"/api/v1/invoices/{inv_id}", headers=headers).json()["data"]

    # Assert 0 balance or status mutation
    assert pay_before["status"] == pay_after["status"] == "UNRECONCILED"
    assert pay_before["amount"] == pay_after["amount"] == "18000.00"
    assert pay_before["unallocated_amount"] == pay_after["unallocated_amount"] == "18000.00"
    assert pay_before["allocated_amount"] == pay_after["allocated_amount"] == "0.00"

    assert inv_before["status"] == inv_after["status"] == "PENDING"
    assert inv_before["total_amount"] == inv_after["total_amount"] == "18000.00"
    assert inv_before["outstanding_amount"] == inv_after["outstanding_amount"] == "18000.00"
    assert inv_before["paid_amount"] == inv_after["paid_amount"] == "0.00"


def test_exact_match_api_no_match(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that an amount mismatch yields NO_EXACT_MATCH."""
    headers = registered_owner["headers"]

    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Beta Enterprises", "tax_id": "GSTIN-BETA-01"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "887766554433"},
    )

    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-BETA-001",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 50000.00,
            "currency": "INR",
        },
    )

    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-10",
            "amount": "30000.00",  # Underpayment: 30k vs 50k
            "currency": "INR",
            "narration": "PARTIAL PAYMENT FROM BETA ENTERPRISES",
            "bank_account_number": "887766554433",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    resp = client.post(
        f"/api/v1/reconciliation/exact-match/{payment_id}",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["status"] == ExactMatchStatus.NO_EXACT_MATCH.value
    assert data["matched_candidate"] is None
    assert data["total_exact_candidates_found"] == 0
    assert data["reason_code"] == ExactMatchReasonCode.PARTIAL_PAYMENT_DETECTED.value


def test_exact_match_api_ambiguous_match(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that multiple open invoices with identical outstanding amounts yield AMBIGUOUS_EXACT_MATCH."""
    headers = registered_owner["headers"]

    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Gamma Solutions", "tax_id": "GSTIN-GAMMA-01"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "776655443322"},
    )

    # Two invoices with identical amounts: 10,000.00 INR
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-GAMMA-001",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 10000.00,
            "currency": "INR",
        },
    )
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-GAMMA-002",
            "customer_id": cust_id,
            "issue_date": "2026-08-05",
            "due_date": "2026-08-31",
            "total_amount": 10000.00,
            "currency": "INR",
        },
    )

    # Payment with NO invoice reference
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "10000.00",
            "currency": "INR",
            "narration": "NEFT PAYMENT FROM GAMMA SOLUTIONS",
            "bank_account_number": "776655443322",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    resp = client.post(
        f"/api/v1/reconciliation/exact-match/{payment_id}",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["status"] == ExactMatchStatus.AMBIGUOUS_EXACT_MATCH.value
    assert data["matched_candidate"] is None
    assert data["total_exact_candidates_found"] == 2
    assert len(data["competing_candidates"]) == 2
    assert data["reason_code"] == ExactMatchReasonCode.MULTIPLE_EXACT_AMOUNT_MATCHES.value


def test_exact_match_api_unresolved_customer(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that an unknown payer yields NO_EXACT_MATCH with CUSTOMER_UNRESOLVED."""
    headers = registered_owner["headers"]

    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "10000.00",
            "currency": "INR",
            "narration": "NEFT TRANSFER UNKNOWN SENDER NO IDENTIFIER 00000",
            "bank_account_number": "000000000000",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    resp = client.post(
        f"/api/v1/reconciliation/exact-match/{payment_id}",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert data["status"] == ExactMatchStatus.NO_EXACT_MATCH.value
    assert data["reason_code"] == ExactMatchReasonCode.CUSTOMER_UNRESOLVED.value
    assert data["matched_candidate"] is None


def test_exact_match_batch_api(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify batch exact matching across multiple unreconciled payments."""
    headers = registered_owner["headers"]

    # Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Batch Match Ltd", "tax_id": "GSTIN-BM-01"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "334455667788"},
    )

    # Invoice: 14,000 INR
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-BM-001",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 14000.00,
            "currency": "INR",
        },
    )

    # Payment 1: 14,000 INR (Exact Match!)
    p1_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "14000.00",
            "currency": "INR",
            "narration": "NEFT TRANSFER FOR INV-BM-001",
            "bank_account_number": "334455667788",
        },
    )
    p1_id = p1_resp.json()["data"]["id"]

    # Payment 2: 99,000 INR (No Match)
    p2_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "99000.00",
            "currency": "INR",
            "narration": "NEFT TRANSFER FROM BATCH MATCH LTD",
            "bank_account_number": "334455667788",
        },
    )
    p2_id = p2_resp.json()["data"]["id"]

    # Call batch endpoint
    batch_resp = client.post(
        "/api/v1/reconciliation/exact-match-batch",
        headers=headers,
        json={"payment_ids": [p1_id, p2_id]},
    )
    assert batch_resp.status_code == 200
    res = batch_resp.json()
    assert res["success"] is True

    data = res["data"]
    assert data["total_evaluated"] == 2
    assert data["exact_matches_found"] == 1
    assert data["no_matches_found"] == 1
    assert len(data["results"]) == 2
