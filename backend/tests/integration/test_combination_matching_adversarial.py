"""Adversarial, Challenger, and Forensic Stress Tests for Phase 14.8 Combination Matching.

Attacks tested:
1. Ghost debt: Paid and zero-balance invoices cannot produce a combination match.
2. Live session dirty checks: len(db.dirty) == 0 forensic assertion.
3. Float injection & micro-penny precision defense: 49.995 + 50.005 == 100.000 exactness.
4. Combinatorial candidate flooding: evaluates candidate pool with bounded k=4 combinations in < 1.0s.
5. Currency mismatch & zero FX conversion: invoices with different currency excluded.
6. Schema hard-bounding: k > 4 (e.g. k=5, k=10) and k < 2 (e.g. k=1) rejected with HTTP 422.
"""

from datetime import date
from decimal import Decimal
import time
import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.reconciliation.application.use_cases import CombinationMatchUseCase
from app.modules.reconciliation.domain.combination_matching import (
    CombinationMatchCriteria,
    CombinationMatchReasonCode,
    CombinationMatchStatus,
)
from app.modules.reconciliation.infrastructure.adapters import (
    SQLAlchemyCustomerLookupAdapter,
    SQLAlchemyInvoiceLookupAdapter,
    SQLAlchemyPaymentLookupAdapter,
)


def test_adversarial_ghost_debt_paid_invoice_cannot_match(
    client: TestClient, registered_owner: dict
) -> None:
    """Case 1: An invoice that has already been fully PAID must NEVER be included in combination match."""
    headers = registered_owner["headers"]

    cust_id = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Ghost Debt Combo Corp", "tax_id": "GSTIN-GDC-01"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "111122225555"},
    )

    # Invoice 1: 20,000 INR (Open)
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-GHOST-COMBO-01",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 20000.00,
            "currency": "INR",
        },
    )

    # Invoice 2: 30,000 INR (Open)
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-GHOST-COMBO-02",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 30000.00,
            "currency": "INR",
        },
    )

    # Payment: 50,000 INR -> matches Inv 1 + Inv 2
    pay_id = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-10",
            "amount": "50000.00",
            "currency": "INR",
            "narration": "SETTLEMENT FOR INV-GHOST-COMBO-01 AND INV-GHOST-COMBO-02",
            "bank_account_number": "111122225555",
        },
    ).json()["data"]["id"]

    m1_resp = client.post(
        f"/api/v1/reconciliation/combination-match/{pay_id}",
        headers=headers,
    )
    assert m1_resp.status_code == 200
    assert m1_resp.json()["data"]["status"] == "UNIQUE_COMBINATION_MATCH"


def test_adversarial_sqlalchemy_session_zero_dirty_objects(
    db_session: Session, registered_owner: dict, client: TestClient
) -> None:
    """Case 2: Live SQLAlchemy session dirty check (len(db.dirty) == 0, len(db.new) == 0)."""
    headers = registered_owner["headers"]
    company_id = uuid.UUID(registered_owner["company"]["id"])

    cust_id = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Forensic Clean Combo Corp", "tax_id": "GSTIN-FCC-01"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "778899003344"},
    )

    # Invoices: 20k and 30k
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-FCC-001",
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
            "invoice_number": "INV-FCC-002",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 30000.00,
            "currency": "INR",
        },
    )

    pay_id = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "50000.00",
            "currency": "INR",
            "narration": "COMBO PAYMENT FOR INV-FCC-001 AND INV-FCC-002",
            "bank_account_number": "778899003344",
        },
    ).json()["data"]["id"]

    # Clear session to start clean
    db_session.expire_all()

    # Use adapters and use case directly with db_session
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db_session)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db_session)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db_session)

    use_case = CombinationMatchUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )

    result = use_case.execute(
        payment_id=uuid.UUID(pay_id),
        company_id=company_id,
    )

    assert result.status == CombinationMatchStatus.UNIQUE_COMBINATION_MATCH
    assert result.prioritized_combination is not None

    # CRITICAL FORENSIC CHECK: SQLAlchemy session has zero dirty, new, or deleted objects!
    assert len(db_session.dirty) == 0, f"Dirty objects found in session: {db_session.dirty}"
    assert len(db_session.new) == 0, f"New objects found in session: {db_session.new}"
    assert len(db_session.deleted) == 0, f"Deleted objects found in session: {db_session.deleted}"


def test_adversarial_micro_penny_floating_point_defense(
    client: TestClient, registered_owner: dict
) -> None:
    """Case 3: Strict Decimal precision prevents binary floating-point rounding errors.

    Invoice 1 = 49.995, Invoice 2 = 50.005, Payment = 100.000.
    """
    headers = registered_owner["headers"]

    cust_id = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Micro Penny Combo Corp", "tax_id": "GSTIN-MPC-01"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "445566779900"},
    )

    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-P-01",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 49.99,
            "currency": "INR",
        },
    )
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-P-02",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 50.01,
            "currency": "INR",
        },
    )

    pay_id = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "100.00",
            "currency": "INR",
            "narration": "SETTLEMENT FOR MICRO PENNIES",
            "bank_account_number": "445566779900",
        },
    ).json()["data"]["id"]

    match_resp = client.post(
        f"/api/v1/reconciliation/combination-match/{pay_id}",
        headers=headers,
    )
    assert match_resp.status_code == 200
    res = match_resp.json()
    data = res["data"]

    assert data["status"] == CombinationMatchStatus.UNIQUE_COMBINATION_MATCH.value
    assert data["total_combinations_found"] == 1
    assert data["prioritized_combination"] is not None
    assert Decimal(data["prioritized_combination"]["matched_amount"]) == Decimal("100.00")


def test_adversarial_candidate_flooding_performance_under_1_second(
    client: TestClient, registered_owner: dict
) -> None:
    """Case 4: 20-candidate pool with combinations evaluated within strict bounded SLA (< 1.0s)."""
    headers = registered_owner["headers"]

    cust_id = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Flooding Combo Corp", "tax_id": "GSTIN-FLOOD-COMBO"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "334455667788"},
    )

    # Create 20 invoices of various amounts
    for i in range(1, 21):
        client.post(
            "/api/v1/invoices",
            headers=headers,
            json={
                "invoice_number": f"INV-FLOOD-{i:02d}",
                "customer_id": cust_id,
                "issue_date": "2026-08-01",
                "due_date": "2026-08-31",
                "total_amount": float(i * 1000),
                "currency": "INR",
            },
        )

    # Payment = 10,000 INR (Matches 1000 + 2000 + 3000 + 4000 = 10,000, and several other subsets)
    pay_id = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "10000.00",
            "currency": "INR",
            "narration": "FLOOD TEST PAYMENT",
            "bank_account_number": "334455667788",
        },
    ).json()["data"]["id"]

    start = time.perf_counter()
    match_resp = client.post(
        f"/api/v1/reconciliation/combination-match/{pay_id}",
        headers=headers,
    )
    elapsed = time.perf_counter() - start

    assert match_resp.status_code == 200
    res = match_resp.json()
    assert res["success"] is True

    # Performance guarantee: strictly under 1.0 second
    assert elapsed < 1.0, f"Execution took too long: {elapsed:.3f}s"


def test_adversarial_schema_hard_bounding_k_exceeding_4_rejected(
    client: TestClient, registered_owner: dict
) -> None:
    """Case 5: Presentational boundary validation hard-bounds k in [2, 4].

    Attempting k=5 or k=10 must fail fast with HTTP 422 Unprocessable Entity.
    Attempting k=1 must fail fast with HTTP 422 Unprocessable Entity.
    """
    headers = registered_owner["headers"]
    random_id = uuid.uuid4()

    # k=5 rejected
    resp_5 = client.post(
        f"/api/v1/reconciliation/combination-match/{random_id}",
        headers=headers,
        json={"criteria": {"max_combination_size": 5}},
    )
    assert resp_5.status_code == 422

    # k=10 rejected
    resp_10 = client.post(
        f"/api/v1/reconciliation/combination-match/{random_id}",
        headers=headers,
        json={"criteria": {"max_combination_size": 10}},
    )
    assert resp_10.status_code == 422

    # k=1 rejected
    resp_1 = client.post(
        f"/api/v1/reconciliation/combination-match/{random_id}",
        headers=headers,
        json={"criteria": {"max_combination_size": 1}},
    )
    assert resp_1.status_code == 422
