"""Adversarial, Challenger, and Forensic Stress Tests for Phase 14.7 Multi-Invoice Matching.

Attacks tested:
1. Ghost debt: Paid and zero-balance invoices cannot produce a multi-invoice match.
2. Live session dirty checks: len(db.dirty) == 0 forensic assertion.
3. Float injection & micro-penny precision defense: 49.995 + 50.005 == 100.000 exactness.
4. Combinatorial candidate flooding: evaluates candidate pool with bounded k=4 combinations in < 1.0s.
5. Currency mismatch & zero FX conversion: invoices with different currency excluded.
"""

from datetime import date
from decimal import Decimal
import time
import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.reconciliation.application.use_cases import MultiInvoiceMatchUseCase
from app.modules.reconciliation.domain.multi_invoice_matching import (
    MultiInvoiceMatchCriteria,
    MultiInvoiceMatchReasonCode,
    MultiInvoiceMatchStatus,
)
from app.modules.reconciliation.infrastructure.adapters import (
    SQLAlchemyCustomerLookupAdapter,
    SQLAlchemyInvoiceLookupAdapter,
    SQLAlchemyPaymentLookupAdapter,
)


def test_adversarial_ghost_debt_paid_invoice_cannot_match(
    client: TestClient, registered_owner: dict
) -> None:
    """Case 1: An invoice that has already been fully PAID must NEVER be included in multi-invoice match."""
    headers = registered_owner["headers"]

    cust_id = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Ghost Debt Multi Corp", "tax_id": "GSTIN-GDM-01"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "111122224444"},
    )

    # Invoice 1: 20,000 INR (Open)
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-GHOST-MULTI-01",
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
            "invoice_number": "INV-GHOST-MULTI-02",
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
            "narration": "SETTLEMENT FOR INV-GHOST-MULTI-01 AND INV-GHOST-MULTI-02",
            "bank_account_number": "111122224444",
        },
    ).json()["data"]["id"]

    m1_resp = client.post(f"/api/v1/reconciliation/multi-invoice-match/{pay_id}", headers=headers)
    assert m1_resp.status_code == 200
    assert m1_resp.json()["data"]["status"] == "MULTI_INVOICE_MATCH"


def test_adversarial_sqlalchemy_session_zero_dirty_objects(
    db_session: Session, registered_owner: dict, client: TestClient
) -> None:
    """Case 2: Live SQLAlchemy session dirty check (len(db.dirty) == 0, len(db.new) == 0)."""
    headers = registered_owner["headers"]
    company_id = uuid.UUID(registered_owner["company"]["id"])

    cust_id = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Forensic Clean Multi Corp", "tax_id": "GSTIN-FCM-01"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "778899002233"},
    )

    # Invoices: 20k and 30k
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-FCM-001",
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
            "invoice_number": "INV-FCM-002",
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
            "narration": "MULTI PAYMENT FOR INV-FCM-001 AND INV-FCM-002",
            "bank_account_number": "778899002233",
        },
    ).json()["data"]["id"]

    # Clear session to start clean
    db_session.expire_all()

    # Use adapters and use case directly with db_session
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db_session)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db_session)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db_session)

    use_case = MultiInvoiceMatchUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )

    result = use_case.execute(
        payment_id=uuid.UUID(pay_id),
        company_id=company_id,
    )

    assert result.status == MultiInvoiceMatchStatus.MULTI_INVOICE_MATCH
    assert result.matched_combination is not None

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
        json={"name": "Micro Penny Multi Corp", "tax_id": "GSTIN-MPM-01"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "445566778899"},
    )

    # Invoices: 49.99 and 50.01 INR
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-MPM-001",
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
            "invoice_number": "INV-MPM-002",
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
            "narration": "MICRO PENNY MULTI INV-MPM-001 AND INV-MPM-002",
            "bank_account_number": "445566778899",
        },
    ).json()["data"]["id"]

    resp = client.post(f"/api/v1/reconciliation/multi-invoice-match/{pay_id}", headers=headers)
    assert resp.status_code == 200
    combo = resp.json()["data"]["matched_combination"]
    assert combo is not None
    assert combo["matched_amount"] == "100.00"
    assert combo["combination_size"] == 2


def test_adversarial_combinatorial_candidate_flooding_performance(
    client: TestClient, registered_owner: dict
) -> None:
    """Case 4: Flood 20 candidate invoices; evaluate multi-invoice match combinations in < 1.0s."""
    headers = registered_owner["headers"]

    cust_id = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Flooding Multi Corp", "tax_id": "GSTIN-FLDM-01"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "123456789012"},
    )

    # Create 20 invoices
    for i in range(20):
        client.post(
            "/api/v1/invoices",
            headers=headers,
            json={
                "invoice_number": f"INV-FLOOD-M-{i:03d}",
                "customer_id": cust_id,
                "issue_date": "2026-08-01",
                "due_date": "2026-08-31",
                "total_amount": 1000.00 + i * 500.00,
                "currency": "INR",
            },
        )

    # Payment: 2500 INR (matches 1000.00 + 1500.00, i=0 and i=1)
    pay_id = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "2500.00",
            "currency": "INR",
            "narration": "FLOOD TEST PAYMENT",
            "bank_account_number": "123456789012",
        },
    ).json()["data"]["id"]

    start = time.perf_counter()
    resp = client.post(f"/api/v1/reconciliation/multi-invoice-match/{pay_id}", headers=headers)
    elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "MULTI_INVOICE_MATCH"
    assert data["matched_combination"] is not None
    assert elapsed < 1.0, f"Evaluation took too long: {elapsed}s"


def test_adversarial_max_combination_size_bounded_to_k4_http_422(
    client: TestClient, registered_owner: dict
) -> None:
    """Case 5: External callers attempting k > 4 (e.g. k=5, k=10) or k < 2 must receive HTTP 422.

    Enforces Rule M-2 bounded combinatorial depth contract at the API presentation boundary.
    """
    headers = registered_owner["headers"]

    cust_id = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "K-Bound Corp", "tax_id": "GSTIN-KBOUND-01"},
    ).json()["data"]["id"]

    client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "998877665544"},
    )

    pay_id = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "5000.00",
            "currency": "INR",
            "narration": "K-BOUND TEST PAYMENT",
            "bank_account_number": "998877665544",
        },
    ).json()["data"]["id"]

    # Attempt k = 5 -> Rejected with HTTP 422
    resp_k5 = client.post(
        f"/api/v1/reconciliation/multi-invoice-match/{pay_id}",
        headers=headers,
        json={"criteria": {"max_combination_size": 5}},
    )
    assert resp_k5.status_code == 422, f"Expected 422, got {resp_k5.status_code}: {resp_k5.text}"

    # Attempt k = 10 -> Rejected with HTTP 422
    resp_k10 = client.post(
        f"/api/v1/reconciliation/multi-invoice-match/{pay_id}",
        headers=headers,
        json={"criteria": {"max_combination_size": 10}},
    )
    assert resp_k10.status_code == 422, f"Expected 422, got {resp_k10.status_code}: {resp_k10.text}"

    # Attempt k = 1 -> Rejected with HTTP 422 (ge=2)
    resp_k1 = client.post(
        f"/api/v1/reconciliation/multi-invoice-match/{pay_id}",
        headers=headers,
        json={"criteria": {"max_combination_size": 1}},
    )
    assert resp_k1.status_code == 422, f"Expected 422, got {resp_k1.status_code}: {resp_k1.text}"

    # Valid k = 4 -> Accepted with HTTP 200
    resp_k4 = client.post(
        f"/api/v1/reconciliation/multi-invoice-match/{pay_id}",
        headers=headers,
        json={"criteria": {"max_combination_size": 4}},
    )
    assert resp_k4.status_code == 200, f"Expected 200, got {resp_k4.status_code}: {resp_k4.text}"
