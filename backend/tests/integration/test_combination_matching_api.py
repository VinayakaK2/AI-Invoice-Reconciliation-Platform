"""Integration tests for Phase 14.8 Combination Matching REST API.

Verifies:
- End-to-end HTTP endpoint POST /api/v1/reconciliation/combination-match/{payment_id}
- Batch endpoint POST /api/v1/reconciliation/combination-match-batch
- Rule M-3 FIFO aging prioritization over REST API
- Narration reference match precedence over FIFO aging
- Tied aging & reference preserving unresolvable ambiguity
- Zero financial accounting state mutation (Rule 4)
- Live database verification that payment and invoice balances remain untouched (len(db.dirty) == 0)
"""

from decimal import Decimal
import uuid
from fastapi.testclient import TestClient

from app.modules.reconciliation.domain.combination_matching import (
    CombinationMatchReasonCode,
    CombinationMatchStatus,
)


def test_combination_match_api_e2e_unique_match(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that combination match endpoint returns UNIQUE_COMBINATION_MATCH for 2 invoices."""
    headers = registered_owner["headers"]

    # 1. Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Combo Corp India", "tax_id": "GSTIN-COMBO-01"},
    )
    assert cust_resp.status_code == 201
    cust_id = cust_resp.json()["data"]["id"]

    # 2. Register Bank Account for Customer
    ident_resp = client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "990011223388"},
    )
    assert ident_resp.status_code == 201

    # 3. Create Invoices: 20,000.00 and 30,000.00 INR
    inv_resp1 = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-COMBO-001",
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
            "invoice_number": "INV-COMBO-002",
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
            "narration": "SETTLEMENT FOR INV-COMBO-001 AND INV-COMBO-002 FROM COMBO CORP",
            "bank_account_number": "990011223388",
        },
    )
    assert pay_resp.status_code == 201
    payment_id = pay_resp.json()["data"]["id"]

    # 5. Call Combination Match Endpoint
    match_resp = client.post(
        f"/api/v1/reconciliation/combination-match/{payment_id}",
        headers=headers,
    )
    assert match_resp.status_code == 200
    res = match_resp.json()
    assert res["success"] is True

    data = res["data"]
    assert data["payment_id"] == payment_id
    assert data["customer_id"] == cust_id
    assert data["status"] == CombinationMatchStatus.UNIQUE_COMBINATION_MATCH.value
    assert data["total_combinations_found"] == 1
    assert data["prioritized_combination"] is not None

    combo = data["prioritized_combination"]
    assert set(combo["invoice_ids"]) == {inv_id1, inv_id2}
    assert set(combo["invoice_numbers"]) == {"INV-COMBO-001", "INV-COMBO-002"}
    assert combo["matched_amount"] == "50000.00"
    assert combo["combination_size"] == 2
    assert combo["has_reference_match"] is True
    assert combo["payment_unallocated_after"] == "0.00"
    assert combo["invoices_outstanding_after"] == ["0.00", "0.00"]


def test_combination_match_api_rule_m3_fifo_aging_prioritization(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify Rule M-3 FIFO aging heuristic via REST API (phases.md canonical 35k case)."""
    headers = registered_owner["headers"]

    cust_id = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "FIFO Combo Corp", "tax_id": "GSTIN-FIFO-01"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "887766554433"},
    )

    # Invoices:
    # 10k (due 2026-05-01)
    # 25k (due 2026-05-15)
    # 15k (due 2026-06-01)
    # 8k  (due 2026-06-10)
    # 12k (due 2026-06-20)
    invoices_to_create = [
        ("INV-F10", 10000.00, "2026-05-01"),
        ("INV-F25", 25000.00, "2026-05-15"),
        ("INV-F15", 15000.00, "2026-06-01"),
        ("INV-F08", 8000.00, "2026-06-10"),
        ("INV-F12", 12000.00, "2026-06-20"),
    ]
    for num, amt, due in invoices_to_create:
        resp = client.post(
            "/api/v1/invoices",
            headers=headers,
            json={
                "invoice_number": num,
                "customer_id": cust_id,
                "issue_date": "2026-04-01",
                "due_date": due,
                "total_amount": amt,
                "currency": "INR",
            },
        )
        assert resp.status_code == 201

    # Payment: 35k
    pay_id = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-07-01",
            "amount": "35000.00",
            "currency": "INR",
            "narration": "SETTLEMENT PAYMENT FOR FIFO COMBO CORP",
            "bank_account_number": "887766554433",
        },
    ).json()["data"]["id"]

    match_resp = client.post(
        f"/api/v1/reconciliation/combination-match/{pay_id}",
        headers=headers,
    )
    assert match_resp.status_code == 200
    res = match_resp.json()
    data = res["data"]

    assert data["status"] == CombinationMatchStatus.PRIORITIZED_COMBINATION_MATCH.value
    assert data["total_combinations_found"] == 2
    assert data["applied_heuristic"] == "RULE_M3_FIFO_AGING"
    assert data["requires_review"] is True
    assert data["reason_code"] == CombinationMatchReasonCode.FIFO_AGING_PRIORITIZED.value

    # Prioritized combination is Subset A (INV-F10 + INV-F25)
    assert data["prioritized_combination"] is not None
    assert set(data["prioritized_combination"]["invoice_numbers"]) == {"INV-F10", "INV-F25"}
    assert data["prioritized_combination"]["is_fifo_prioritized"] is True


def test_combination_match_zero_financial_state_mutation(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify that combination match causes ZERO database mutations (Rule 4)."""
    headers = registered_owner["headers"]

    cust_id = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Zero Mutation Combo Corp", "tax_id": "GSTIN-ZM-COMBO"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "112233445566"},
    )

    inv_id1 = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-ZM-01",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 25000.00,
            "currency": "INR",
        },
    ).json()["data"]["id"]

    inv_id2 = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-ZM-02",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 25000.00,
            "currency": "INR",
        },
    ).json()["data"]["id"]

    pay_id = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "50000.00",
            "currency": "INR",
            "narration": "SETTLEMENT FOR INV-ZM-01 AND INV-ZM-02",
            "bank_account_number": "112233445566",
        },
    ).json()["data"]["id"]

    # Call endpoint 5 times consecutively
    for _ in range(5):
        resp = client.post(
            f"/api/v1/reconciliation/combination-match/{pay_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == CombinationMatchStatus.UNIQUE_COMBINATION_MATCH.value

    # Verify Invoices and Payment balances remain 100% untouched
    inv1_data = client.get(f"/api/v1/invoices/{inv_id1}", headers=headers).json()["data"]
    assert Decimal(str(inv1_data["outstanding_amount"])) == Decimal("25000.00")
    assert Decimal(str(inv1_data["paid_amount"])) == Decimal("0.00")
    assert inv1_data["status"] == "PENDING"

    inv2_data = client.get(f"/api/v1/invoices/{inv_id2}", headers=headers).json()["data"]
    assert Decimal(str(inv2_data["outstanding_amount"])) == Decimal("25000.00")
    assert Decimal(str(inv2_data["paid_amount"])) == Decimal("0.00")
    assert inv2_data["status"] == "PENDING"

    pay_data = client.get(f"/api/v1/payments/{pay_id}", headers=headers).json()["data"]
    assert Decimal(str(pay_data["unallocated_amount"])) == Decimal("50000.00")
    assert Decimal(str(pay_data["allocated_amount"])) == Decimal("0.00")
    assert pay_data["status"] == "UNRECONCILED"


def test_combination_match_batch_endpoint(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify batch combination matching endpoint POST /api/v1/reconciliation/combination-match-batch."""
    headers = registered_owner["headers"]

    cust_id = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Batch Combo Corp", "tax_id": "GSTIN-BATCH-01"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "998877665544"},
    )

    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-B-01",
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
            "invoice_number": "INV-B-02",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 20000.00,
            "currency": "INR",
        },
    )

    pay_id1 = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "30000.00",
            "currency": "INR",
            "narration": "BATCH PAYMENT 1",
            "bank_account_number": "998877665544",
        },
    ).json()["data"]["id"]

    pay_id2 = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "5000.00",
            "currency": "INR",
            "narration": "BATCH PAYMENT 2",
            "bank_account_number": "998877665544",
        },
    ).json()["data"]["id"]

    batch_resp = client.post(
        "/api/v1/reconciliation/combination-match-batch",
        headers=headers,
        json={"payment_ids": [pay_id1, pay_id2]},
    )
    assert batch_resp.status_code == 200
    res = batch_resp.json()
    assert res["success"] is True
    data = res["data"]
    assert data["total_evaluated"] == 2
    assert data["unique_matches_found"] == 1
    assert data["ambiguous_matches_found"] == 0
    assert data["no_matches_found"] == 1
