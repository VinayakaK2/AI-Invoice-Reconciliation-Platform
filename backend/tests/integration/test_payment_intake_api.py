"""Integration tests for Phase 14.1 Payment Intake API endpoints and zero-mutation guarantees.

Verifies:
- POST /api/v1/reconciliation/intake/{payment_id}
- POST /api/v1/reconciliation/intake-batch
- Rule 4: Zero financial state mutation (0 dirty session objects, byte-for-byte invariant persistence)
- Masking of sensitive banking coordinates in presentation response.
"""

from decimal import Decimal
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.modules.payment.infrastructure.models import PaymentModel


def test_payment_intake_endpoint_eligible(client: TestClient, registered_owner: dict):
    """Verify single payment intake endpoint classifies standard unreconciled payment as ELIGIBLE."""
    headers = registered_owner["headers"]

    # 1. Create a payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-08",
            "amount": "35000.00",
            "currency": "INR",
            "narration": "NEFT CLR ACME TECH",
            "bank_account_number": "123456789012",
        },
    )
    assert pay_resp.status_code == 201
    payment_id = pay_resp.json()["data"]["id"]

    # 2. Call Payment Intake endpoint
    intake_resp = client.post(
        f"/api/v1/reconciliation/intake/{payment_id}",
        headers=headers,
    )
    assert intake_resp.status_code == 200
    data = intake_resp.json()["data"]

    assert data["payment_id"] == payment_id
    assert data["status"] == "ELIGIBLE"
    assert data["is_eligible"] is True
    assert data["reason_code"] == "READY_FOR_RECONCILIATION"
    assert data["original_amount"] == "35000.00"
    assert data["allocated_amount"] == "0.00"
    assert data["unallocated_amount"] == "35000.00"
    assert data["effective_amount"] == "35000.00"
    assert data["currency"] == "INR"
    assert data["is_deterministic"] is True
    # Verify coordinate masking: only last 4 digits preserved
    assert data["bank_account_number"] == "********9012"


def test_payment_intake_endpoint_partially_reconciled(
    client: TestClient, registered_owner: dict, db_session: Session
):
    """Verify payment with PARTIALLY_RECONCILED status exposes remaining unallocated balance as effective_amount."""
    headers = registered_owner["headers"]

    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-07",
            "amount": "50000.00",
            "currency": "INR",
            "narration": "PARTIAL SETTLEMENT",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    # Simulate partial allocation directly in database
    p_model = db_session.query(PaymentModel).filter(PaymentModel.id == payment_id).first()
    p_model.allocated_amount = Decimal("20000.00")
    p_model.unallocated_amount = Decimal("30000.00")
    p_model.status = "PARTIALLY_RECONCILED"
    db_session.commit()

    intake_resp = client.post(
        f"/api/v1/reconciliation/intake/{payment_id}",
        headers=headers,
    )
    assert intake_resp.status_code == 200
    data = intake_resp.json()["data"]

    assert data["status"] == "ELIGIBLE"
    assert data["is_eligible"] is True
    assert data["reason_code"] == "PARTIAL_BALANCE_AVAILABLE"
    assert data["effective_amount"] == "30000.00"
    assert data["allocated_amount"] == "20000.00"
    assert data["unallocated_amount"] == "30000.00"


def test_payment_intake_endpoint_already_processed(
    client: TestClient, registered_owner: dict, db_session: Session
):
    """Verify payment with RECONCILED status is classified as ALREADY_PROCESSED."""
    headers = registered_owner["headers"]

    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-01",
            "amount": "10000.00",
            "currency": "INR",
            "narration": "FULL SETTLEMENT",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    # Simulate fully reconciled payment
    p_model = db_session.query(PaymentModel).filter(PaymentModel.id == payment_id).first()
    p_model.allocated_amount = Decimal("10000.00")
    p_model.unallocated_amount = Decimal("0.00")
    p_model.status = "RECONCILED"
    db_session.commit()

    intake_resp = client.post(
        f"/api/v1/reconciliation/intake/{payment_id}",
        headers=headers,
    )
    assert intake_resp.status_code == 200
    data = intake_resp.json()["data"]

    assert data["status"] == "ALREADY_PROCESSED"
    assert data["is_eligible"] is False
    assert data["reason_code"] == "FULLY_RECONCILED"
    assert data["effective_amount"] == "0.00"


def test_payment_intake_endpoint_ignored(client: TestClient, registered_owner: dict):
    """Verify payment marked IGNORED is classified as INELIGIBLE."""
    headers = registered_owner["headers"]

    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-05",
            "amount": "8000.00",
            "currency": "INR",
            "narration": "DIRECTOR LOAN",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    # Mark as IGNORED using payment domain endpoint
    ignore_resp = client.post(
        f"/api/v1/payments/{payment_id}/ignore",
        headers=headers,
        json={"reason": "Non-trade receipt"},
    )
    assert ignore_resp.status_code == 200

    # Call intake
    intake_resp = client.post(
        f"/api/v1/reconciliation/intake/{payment_id}",
        headers=headers,
    )
    assert intake_resp.status_code == 200
    data = intake_resp.json()["data"]

    assert data["status"] == "INELIGIBLE"
    assert data["is_eligible"] is False
    assert data["reason_code"] == "PAYMENT_MARKED_IGNORED"


def test_payment_intake_batch_endpoint(client: TestClient, registered_owner: dict):
    """Verify batch intake endpoint processes multiple payments and counts eligible totals."""
    headers = registered_owner["headers"]

    # Create 2 eligible payments and 1 ignored payment
    p1 = client.post(
        "/api/v1/payments",
        headers=headers,
        json={"transaction_date": "2026-09-08", "amount": "1000.00", "currency": "INR", "narration": "P1"},
    ).json()["data"]["id"]

    p2 = client.post(
        "/api/v1/payments",
        headers=headers,
        json={"transaction_date": "2026-09-08", "amount": "2000.00", "currency": "INR", "narration": "P2"},
    ).json()["data"]["id"]

    p3 = client.post(
        "/api/v1/payments",
        headers=headers,
        json={"transaction_date": "2026-09-08", "amount": "3000.00", "currency": "INR", "narration": "P3"},
    ).json()["data"]["id"]

    client.post(f"/api/v1/payments/{p3}/ignore", headers=headers, json={"reason": "Test ignore"})

    # Batch evaluate with explicit payment_ids
    resp = client.post(
        "/api/v1/reconciliation/intake-batch",
        headers=headers,
        json={"payment_ids": [p1, p2, p3]},
    )
    assert resp.status_code == 200
    batch_data = resp.json()["data"]

    assert batch_data["total_evaluated"] == 3
    assert batch_data["total_eligible"] == 2

    # Batch evaluate without payment_ids (defaults to unreconciled payments)
    resp_all = client.post(
        "/api/v1/reconciliation/intake-batch",
        headers=headers,
        json={"limit": 50},
    )
    assert resp_all.status_code == 200
    assert resp_all.json()["data"]["total_evaluated"] >= 2


def test_payment_intake_zero_financial_mutation_guarantee(
    client: TestClient, registered_owner: dict, db_session: Session
):
    """Rule 4 Invariant: Verify payment intake evaluation leaves financial state 100% untouched."""
    headers = registered_owner["headers"]

    # 1. Create a payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-09-08",
            "amount": "42000.00",
            "currency": "INR",
            "narration": "IMMUTABILITY VERIFICATION",
        },
    )
    payment_id = pay_resp.json()["data"]["id"]

    # Capture initial database state
    p_initial = db_session.query(PaymentModel).filter(PaymentModel.id == payment_id).first()
    db_session.refresh(p_initial)
    init_state = {
        "amount": p_initial.amount,
        "allocated_amount": p_initial.allocated_amount,
        "unallocated_amount": p_initial.unallocated_amount,
        "status": p_initial.status,
        "updated_at": p_initial.updated_at,
    }

    # 2. Call Payment Intake endpoint
    intake_resp = client.post(
        f"/api/v1/reconciliation/intake/{payment_id}",
        headers=headers,
    )
    assert intake_resp.status_code == 200

    # 3. Verify database state is byte-for-byte identical
    db_session.expire_all()
    p_post = db_session.query(PaymentModel).filter(PaymentModel.id == payment_id).first()
    post_state = {
        "amount": p_post.amount,
        "allocated_amount": p_post.allocated_amount,
        "unallocated_amount": p_post.unallocated_amount,
        "status": p_post.status,
        "updated_at": p_post.updated_at,
    }

    assert init_state == post_state, f"Financial state mutated! Before: {init_state}, After: {post_state}"
    assert len(db_session.dirty) == 0, f"Dirty objects detected: {db_session.dirty}"
    assert len(db_session.new) == 0, f"New objects detected: {db_session.new}"
    assert len(db_session.deleted) == 0, f"Deleted objects detected: {db_session.deleted}"
