"""Integration tests for Phase 14.6 Partial 1:1 Matching REST API.

Verifies:
- End-to-end HTTP endpoint POST /api/v1/reconciliation/partial-match/{payment_id}
- Batch endpoint POST /api/v1/reconciliation/partial-match-batch
- Zero financial accounting state mutation (Rule 4)
- Live database verification that payment and invoice balances remain untouched
- Unambiguous, ambiguous, and non-match API scenarios
- Graceful handling of unresolved customer contexts
"""

from decimal import Decimal
import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.reconciliation.domain.partial_matching import (
    PartialMatchReasonCode,
    PartialMatchStatus,
)


def test_partial_match_api_e2e_success(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that partial matching endpoint returns PARTIAL_MATCH for an eligible underpayment."""
    headers = registered_owner["headers"]

    # 1. Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Partial Client India", "tax_id": "GSTIN-PARTIAL-01"},
    )
    assert cust_resp.status_code == 201
    cust_id = cust_resp.json()["data"]["id"]

    # 2. Register Bank Account for Customer
    ident_resp = client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "990011223344"},
    )
    assert ident_resp.status_code == 201

    # 3. Create Invoice: 100,000.00 INR
    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-PARTIAL-001",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 100000.00,
            "currency": "INR",
        },
    )
    assert inv_resp.status_code == 201
    inv_id = inv_resp.json()["data"]["id"]

    # 4. Create Partial Payment: 40,000.00 INR
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "40000.00",
            "currency": "INR",
            "narration": "PARTIAL SETTLEMENT FOR INV-PARTIAL-001 FROM PARTIAL CLIENT",
            "bank_account_number": "990011223344",
        },
    )
    assert pay_resp.status_code == 201
    payment_id = pay_resp.json()["data"]["id"]

    # 5. Call Partial Match Endpoint
    match_resp = client.post(
        f"/api/v1/reconciliation/partial-match/{payment_id}",
        headers=headers,
    )
    assert match_resp.status_code == 200
    res = match_resp.json()
    assert res["success"] is True

    data = res["data"]
    assert data["payment_id"] == payment_id
    assert data["customer_id"] == cust_id
    assert data["status"] == PartialMatchStatus.PARTIAL_MATCH.value
    assert data["total_partial_candidates_found"] == 1
    assert data["matched_candidate"] is not None

    cand = data["matched_candidate"]
    assert cand["invoice_id"] == inv_id
    assert cand["invoice_number"] == "INV-PARTIAL-001"
    assert cand["matched_amount"] == "40000.00"
    assert cand["invoice_outstanding_before"] == "100000.00"
    assert cand["invoice_outstanding_after"] == "60000.00"
    assert cand["payment_unallocated_before"] == "40000.00"
    assert cand["payment_unallocated_after"] == "0.00"
    assert cand["is_reference_match"] is True
    assert len(cand["evidence_signals"]) >= 3


def test_partial_match_zero_financial_state_mutation(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that evaluating partial matching causes ZERO database mutation (Rule 4)."""
    headers = registered_owner["headers"]

    # Setup Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Zero Mutation Partial Corp", "tax_id": "GSTIN-ZMP-99"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "887711223344"},
    )

    # Setup Invoice: 50,000.00 INR
    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-ZMP-999",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 50000.00,
            "currency": "INR",
        },
    )
    inv_id = inv_resp.json()["data"]["id"]

    # Setup Payment: 20,000.00 INR
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "20000.00",
            "currency": "INR",
            "narration": "PARTIAL SETTLEMENT INV-ZMP-999",
            "bank_account_number": "887711223344",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    # Invoke partial matching 5 consecutive times
    for _ in range(5):
        resp = client.post(
            f"/api/v1/reconciliation/partial-match/{payment_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == PartialMatchStatus.PARTIAL_MATCH.value

    # Verify Invoice DB State via GET endpoint
    inv_check = client.get(f"/api/v1/invoices/{inv_id}", headers=headers)
    assert inv_check.status_code == 200
    inv_data = inv_check.json()["data"]
    assert Decimal(str(inv_data["outstanding_amount"])) == Decimal("50000.00")
    assert Decimal(str(inv_data["paid_amount"])) == Decimal("0.00")
    assert inv_data["status"] == "PENDING"

    # Verify Payment DB State via GET endpoint
    pay_check = client.get(f"/api/v1/payments/{payment_id}", headers=headers)
    assert pay_check.status_code == 200
    pay_data = pay_check.json()["data"]
    assert Decimal(str(pay_data["unallocated_amount"])) == Decimal("20000.00")
    assert Decimal(str(pay_data["allocated_amount"])) == Decimal("0.00")
    assert pay_data["status"] == "UNRECONCILED"


def test_partial_match_api_ambiguous_match(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that multiple open invoices capable of receiving partial payment yield AMBIGUOUS_PARTIAL_MATCH."""
    headers = registered_owner["headers"]

    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Ambiguous Partial Client", "tax_id": "GSTIN-AMB-PART"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "556677889900"},
    )

    # Invoice A: 100,000.00
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-AMB-A",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 100000.00,
            "currency": "INR",
        },
    )

    # Invoice B: 80,000.00
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-AMB-B",
            "customer_id": cust_id,
            "issue_date": "2026-08-05",
            "due_date": "2026-08-31",
            "total_amount": 80000.00,
            "currency": "INR",
        },
    )

    # Payment: 30,000.00 (both invoices have outstanding > 30,000.00)
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "30000.00",
            "currency": "INR",
            "narration": "PAYMENT FROM AMBIGUOUS PARTIAL CLIENT",
            "bank_account_number": "556677889900",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    match_resp = client.post(
        f"/api/v1/reconciliation/partial-match/{payment_id}",
        headers=headers,
    )
    assert match_resp.status_code == 200
    data = match_resp.json()["data"]

    assert data["status"] == PartialMatchStatus.AMBIGUOUS_PARTIAL_MATCH.value
    assert data["matched_candidate"] is None
    assert len(data["competing_candidates"]) == 2
    assert data["total_partial_candidates_found"] == 2
    assert data["reason_code"] == PartialMatchReasonCode.MULTIPLE_PARTIAL_CANDIDATES.value


def test_partial_match_api_no_match_exact(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that an exact match amount returns NO_PARTIAL_MATCH with EXACT_MATCH_DETECTED."""
    headers = registered_owner["headers"]

    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Exact Client", "tax_id": "GSTIN-EX-01"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "443322110099"},
    )

    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-EX-001",
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
            "transaction_date": "2026-08-15",
            "amount": "50000.00",
            "currency": "INR",
            "narration": "EXACT SETTLEMENT INV-EX-001",
            "bank_account_number": "443322110099",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    match_resp = client.post(
        f"/api/v1/reconciliation/partial-match/{payment_id}",
        headers=headers,
    )
    assert match_resp.status_code == 200
    data = match_resp.json()["data"]

    assert data["status"] == PartialMatchStatus.NO_PARTIAL_MATCH.value
    assert data["matched_candidate"] is None
    assert data["reason_code"] == PartialMatchReasonCode.EXACT_MATCH_DETECTED.value


def test_partial_match_api_unresolved_customer(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that an unidentifiable payer returns NO_PARTIAL_MATCH with CUSTOMER_UNRESOLVED."""
    headers = registered_owner["headers"]

    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "15000.00",
            "currency": "INR",
            "narration": "UNIDENTIFIABLE MYSTERY WIRE",
            "bank_account_number": "999999999999",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    match_resp = client.post(
        f"/api/v1/reconciliation/partial-match/{payment_id}",
        headers=headers,
    )
    assert match_resp.status_code == 200
    data = match_resp.json()["data"]

    assert data["status"] == PartialMatchStatus.NO_PARTIAL_MATCH.value
    assert data["reason_code"] == PartialMatchReasonCode.CUSTOMER_UNRESOLVED.value


def test_partial_match_batch_api(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that batch partial matching endpoint evaluates multiple payments."""
    headers = registered_owner["headers"]

    # Create customer and invoice: 80,000.00 INR
    cust_id = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Batch Partial Customer", "tax_id": "GSTIN-BPC-01"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "332211009988"},
    )

    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-BPC-001",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 80000.00,
            "currency": "INR",
        },
    )

    # Create payment 1: 30,000.00 (partial)
    p1 = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-10",
            "amount": "30000.00",
            "currency": "INR",
            "narration": "BATCH PAYMENT 1",
            "bank_account_number": "332211009988",
        },
    ).json()["data"]["id"]

    # Create payment 2: 80,000.00 (exact -> no partial match)
    p2 = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-11",
            "amount": "80000.00",
            "currency": "INR",
            "narration": "BATCH PAYMENT 2",
            "bank_account_number": "332211009988",
        },
    ).json()["data"]["id"]

    # Call batch endpoint with payment_ids
    batch_resp = client.post(
        "/api/v1/reconciliation/partial-match-batch",
        headers=headers,
        json={"payment_ids": [p1, p2]},
    )
    assert batch_resp.status_code == 200
    b_data = batch_resp.json()["data"]

    assert b_data["total_evaluated"] == 2
    assert b_data["partial_matches_found"] == 1
    assert b_data["no_matches_found"] == 1
