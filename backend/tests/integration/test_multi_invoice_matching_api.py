"""Integration tests for Phase 14.7 Multi-Invoice Matching REST API.

Verifies:
- End-to-end HTTP endpoint POST /api/v1/reconciliation/multi-invoice-match/{payment_id}
- Batch endpoint POST /api/v1/reconciliation/multi-invoice-match-batch
- Zero financial accounting state mutation (Rule 4)
- Live database verification that payment and invoice balances remain untouched
- Unambiguous, ambiguous, and non-match API scenarios
- Graceful handling of unresolved customer contexts
"""

from decimal import Decimal
import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.reconciliation.domain.multi_invoice_matching import (
    MultiInvoiceMatchReasonCode,
    MultiInvoiceMatchStatus,
)


def test_multi_invoice_match_api_e2e_success(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that multi-invoice matching endpoint returns MULTI_INVOICE_MATCH for matching combination."""
    headers = registered_owner["headers"]

    # 1. Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Multi Client India", "tax_id": "GSTIN-MULTI-01"},
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

    # 3. Create Invoices: 20,000.00 and 30,000.00 INR
    inv_resp1 = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-MULTI-001",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 20000.00,
            "currency": "INR",
        },
    )
    assert inv_resp1.status_code == 201
    inv_id1 = inv_resp1.json()["data"]["id"]

    inv_resp2 = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-MULTI-002",
            "customer_id": cust_id,
            "issue_date": "2026-08-05",
            "due_date": "2026-08-31",
            "total_amount": 30000.00,
            "currency": "INR",
        },
    )
    assert inv_resp2.status_code == 201
    inv_id2 = inv_resp2.json()["data"]["id"]

    # 4. Create Payment: 50,000.00 INR
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "50000.00",
            "currency": "INR",
            "narration": "SETTLEMENT FOR INV-MULTI-001 AND INV-MULTI-002 FROM MULTI CLIENT",
            "bank_account_number": "990011223344",
        },
    )
    assert pay_resp.status_code == 201
    payment_id = pay_resp.json()["data"]["id"]

    # 5. Call Multi-Invoice Match Endpoint
    match_resp = client.post(
        f"/api/v1/reconciliation/multi-invoice-match/{payment_id}",
        headers=headers,
    )
    assert match_resp.status_code == 200
    res = match_resp.json()
    assert res["success"] is True

    data = res["data"]
    assert data["payment_id"] == payment_id
    assert data["customer_id"] == cust_id
    assert data["status"] == MultiInvoiceMatchStatus.MULTI_INVOICE_MATCH.value
    assert data["total_combinations_found"] == 1
    assert data["matched_combination"] is not None

    combo = data["matched_combination"]
    assert set(combo["invoice_ids"]) == {inv_id1, inv_id2}
    assert set(combo["invoice_numbers"]) == {"INV-MULTI-001", "INV-MULTI-002"}
    assert combo["matched_amount"] == "50000.00"
    assert combo["combination_size"] == 2
    assert combo["has_reference_match"] is True
    assert combo["payment_unallocated_after"] == "0.00"
    assert combo["invoices_outstanding_after"] == ["0.00", "0.00"]


def test_multi_invoice_match_zero_financial_state_mutation(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that evaluating multi-invoice matching causes ZERO database mutation (Rule 4)."""
    headers = registered_owner["headers"]

    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Zero Mutation Multi Corp", "tax_id": "GSTIN-ZMM-99"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "887711223344"},
    )

    # Invoices: 25,000.00 and 25,000.00
    inv_resp1 = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-ZMM-001",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 25000.00,
            "currency": "INR",
        },
    )
    inv_id1 = inv_resp1.json()["data"]["id"]

    inv_resp2 = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-ZMM-002",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 25000.00,
            "currency": "INR",
        },
    )
    inv_id2 = inv_resp2.json()["data"]["id"]

    # Payment: 50,000.00
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "50000.00",
            "currency": "INR",
            "narration": "SETTLEMENT FOR INV-ZMM-001 AND INV-ZMM-002",
            "bank_account_number": "887711223344",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    # Invoke multi-invoice matching 5 consecutive times
    for _ in range(5):
        resp = client.post(
            f"/api/v1/reconciliation/multi-invoice-match/{payment_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == MultiInvoiceMatchStatus.MULTI_INVOICE_MATCH.value

    # Verify Invoice 1 DB State
    inv1_check = client.get(f"/api/v1/invoices/{inv_id1}", headers=headers)
    assert inv1_check.status_code == 200
    inv1_data = inv1_check.json()["data"]
    assert Decimal(str(inv1_data["outstanding_amount"])) == Decimal("25000.00")
    assert Decimal(str(inv1_data["paid_amount"])) == Decimal("0.00")
    assert inv1_data["status"] == "PENDING"

    # Verify Invoice 2 DB State
    inv2_check = client.get(f"/api/v1/invoices/{inv_id2}", headers=headers)
    assert inv2_check.status_code == 200
    inv2_data = inv2_check.json()["data"]
    assert Decimal(str(inv2_data["outstanding_amount"])) == Decimal("25000.00")
    assert Decimal(str(inv2_data["paid_amount"])) == Decimal("0.00")
    assert inv2_data["status"] == "PENDING"

    # Verify Payment DB State
    pay_check = client.get(f"/api/v1/payments/{payment_id}", headers=headers)
    assert pay_check.status_code == 200
    pay_data = pay_check.json()["data"]
    assert Decimal(str(pay_data["unallocated_amount"])) == Decimal("50000.00")
    assert Decimal(str(pay_data["allocated_amount"])) == Decimal("0.00")
    assert pay_data["status"] == "UNRECONCILED"


def test_multi_invoice_match_api_ambiguous_match(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that multiple combinations summing to payment amount yield AMBIGUOUS_MULTI_INVOICE_MATCH."""
    headers = registered_owner["headers"]

    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Ambiguous Multi Client", "tax_id": "GSTIN-AMB-MULTI"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "556677889900"},
    )

    # Combo 1: 20k + 30k = 50k
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-AMB-1",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 20000.00,
            "currency": "INR",
        },
    )
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-AMB-2",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 30000.00,
            "currency": "INR",
        },
    )

    # Combo 2: 15k + 35k = 50k
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-AMB-3",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 15000.00,
            "currency": "INR",
        },
    )
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-AMB-4",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 35000.00,
            "currency": "INR",
        },
    )

    # Payment: 50,000.00
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "50000.00",
            "currency": "INR",
            "narration": "PAYMENT FROM AMBIGUOUS MULTI CLIENT",
            "bank_account_number": "556677889900",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    match_resp = client.post(
        f"/api/v1/reconciliation/multi-invoice-match/{payment_id}",
        headers=headers,
    )
    assert match_resp.status_code == 200
    data = match_resp.json()["data"]

    assert data["status"] == MultiInvoiceMatchStatus.AMBIGUOUS_MULTI_INVOICE_MATCH.value
    assert data["matched_combination"] is None
    assert len(data["competing_combinations"]) == 2
    assert data["total_combinations_found"] == 2
    assert data["reason_code"] == MultiInvoiceMatchReasonCode.MULTIPLE_MULTI_INVOICE_MATCHES.value


def test_multi_invoice_match_api_no_match_single_exact(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that a payment matching a single invoice exactly returns NO_MULTI_INVOICE_MATCH with EXACT_MATCH_DETECTED."""
    headers = registered_owner["headers"]

    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Single Exact Multi Test", "tax_id": "GSTIN-SEM-01"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "112244668800"},
    )

    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-SEM-001",
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
            "narration": "EXACT SETTLEMENT INV-SEM-001",
            "bank_account_number": "112244668800",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    match_resp = client.post(
        f"/api/v1/reconciliation/multi-invoice-match/{payment_id}",
        headers=headers,
    )
    assert match_resp.status_code == 200
    data = match_resp.json()["data"]

    assert data["status"] == MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH.value
    assert data["reason_code"] == MultiInvoiceMatchReasonCode.EXACT_MATCH_DETECTED.value
    assert data["matched_combination"] is None


def test_multi_invoice_match_api_batch(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify batch multi-invoice matching evaluation across unreconciled payments."""
    headers = registered_owner["headers"]

    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Batch Multi Client", "tax_id": "GSTIN-BATCH-MULTI"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "443322110099"},
    )

    # Invoices: 10k, 20k, 30k
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-BM-1",
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
            "invoice_number": "INV-BM-2",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 20000.00,
            "currency": "INR",
        },
    )
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-BM-3",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 30000.00,
            "currency": "INR",
        },
    )

    # Payment 1: 30,000.00 (matches 10k + 20k)
    pay1_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "30000.00",
            "currency": "INR",
            "narration": "BATCH SETTLEMENT INV-BM-1 AND INV-BM-2",
            "bank_account_number": "443322110099",
        },
    )
    pay1_id = pay1_resp.json()["data"]["id"]

    # Payment 2: 99,999.00 (no combination sums to this)
    pay2_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "99999.00",
            "currency": "INR",
            "narration": "UNMATCHED AMOUNT",
            "bank_account_number": "443322110099",
        },
    )
    pay2_id = pay2_resp.json()["data"]["id"]

    batch_resp = client.post(
        "/api/v1/reconciliation/multi-invoice-match-batch",
        headers=headers,
        json={"payment_ids": [pay1_id, pay2_id]},
    )
    assert batch_resp.status_code == 200
    bdata = batch_resp.json()["data"]

    assert bdata["total_evaluated"] == 2
    assert bdata["multi_invoice_matches_found"] == 1
    assert bdata["no_matches_found"] == 1
    assert len(bdata["results"]) == 2


def test_multi_invoice_match_api_unresolved_customer(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that a payment with unresolved customer returns NO_MULTI_INVOICE_MATCH with CUSTOMER_UNRESOLVED."""
    headers = registered_owner["headers"]

    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "50000.00",
            "currency": "INR",
            "narration": "UNKNOWN COUNTERPARTY TRANSFER",
            "bank_account_number": "000000000000",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    match_resp = client.post(
        f"/api/v1/reconciliation/multi-invoice-match/{payment_id}",
        headers=headers,
    )
    assert match_resp.status_code == 200
    data = match_resp.json()["data"]

    assert data["status"] == MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH.value
    assert data["reason_code"] == MultiInvoiceMatchReasonCode.CUSTOMER_UNRESOLVED.value
    assert data["matched_combination"] is None
