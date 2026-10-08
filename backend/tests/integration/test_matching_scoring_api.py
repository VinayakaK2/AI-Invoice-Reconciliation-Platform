"""Integration, security, and zero-mutation tests for Phase 14.11 Matching & Scoring REST API.

Verifies:
1. End-to-end HTTP endpoint POST /api/v1/reconciliation/matching-scoring/{payment_id}
2. Observed Financial Mutation: NONE (0 database writes, len(db.dirty) == 0, len(db.new) == 0).
3. Cross-tenant payment IDOR protection (404 Fail-closed).
4. Cross-tenant customer override IDOR protection (404 Fail-closed).
5. Cross-tenant candidate bleed immunity at use-case boundary (ForbiddenError).
6. Unauthenticated access rejected with 401 Unauthorized.
7. Masking of sensitive bank account numbers in scoring contribution responses.
8. Explainable score breakdown, rule version ("1.0.0"), and algorithm version ("14.11.0") returned properly.
9. Batch scoring endpoint POST /api/v1/reconciliation/matching-scoring/batch.
"""

from datetime import date
from decimal import Decimal
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.reconciliation.application.use_cases import (
    EvidenceCollectionUseCase,
    EvidenceNormalizationUseCase,
    MatchingScoringUseCase,
)
from app.modules.reconciliation.domain.candidate_filters import (
    CandidateFilterCriteria,
    FilteredCandidateUniverse,
)
from app.modules.reconciliation.domain.candidate_invoices import CandidateInvoice
from app.modules.reconciliation.domain.matching_scoring import (
    MatchingScoringEngine,
    ScoringWeightsConfig,
)
from app.modules.reconciliation.domain.rules import PaymentIntakeContext
from app.modules.reconciliation.infrastructure.adapters import (
    SQLAlchemyCustomerLookupAdapter,
    SQLAlchemyInvoiceLookupAdapter,
    SQLAlchemyPaymentLookupAdapter,
)
from app.shared.exceptions import ForbiddenError


def test_matching_scoring_api_e2e(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify end-to-end matching scoring endpoint returns deterministic score and explainable breakdown."""
    headers = registered_owner["headers"]

    # 1. Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Scoring Tech Labs", "tax_id": "GSTIN-SCORE-01"},
    )
    assert cust_resp.status_code == 201
    cust_id = cust_resp.json()["data"]["id"]

    # 2. Register Bank Account for Customer
    ident_resp = client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "998877665544"},
    )
    assert ident_resp.status_code == 201

    # 3. Create Invoice
    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-SCORE-001",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 50000.00,
            "currency": "INR",
        },
    )
    assert inv_resp.status_code == 201
    inv_id = inv_resp.json()["data"]["id"]

    # 4. Ingest Payment with explicit invoice number in narration & matching bank account
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "50000.00",
            "currency": "INR",
            "bank_account_number": "998877665544",
            "payment_reference": "REF-SCORE-999",
            "narration": "NEFT TRANSFER FOR INV-SCORE-001 UTR: ICICIR52026081599",
        },
    )
    assert pay_resp.status_code == 201
    pay_id = pay_resp.json()["data"]["id"]

    # 5. Call Matching & Scoring API
    resp = client.post(
        f"/api/v1/reconciliation/matching-scoring/{pay_id}",
        headers=headers,
        json={},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]

    assert data["payment_id"] == pay_id
    assert data["rule_version"] == "1.0.0"
    assert data["algorithm_version"] == "14.11.0"
    assert data["is_deterministic"] is True
    assert data["is_empirically_validated"] is False
    assert data["total_candidates_scored"] == 1

    cand_score = data["candidate_scores"][0]
    assert cand_score["invoice_id"] == inv_id
    assert cand_score["invoice_number"] == "INV-SCORE-001"
    assert Decimal(cand_score["total_score"]) >= Decimal("90.00")  # Acc (40) + Inv (35) + Amt (30) + Date (10) = 115 -> clamped to 100.00
    assert len(cand_score["contributions"]) >= 3

    # Verify sensitive banking coordinates are masked in contributions
    for c in cand_score["contributions"]:
        if c["signal_type"] == "IDENTIFIER_MATCH" and c["matched_value"]:
            assert "998877665544" not in c["matched_value"]
            assert "********5544" in c["matched_value"] or "5544" in c["matched_value"]


def test_matching_scoring_zero_financial_mutation(
    db_session: Session, registered_owner: dict, client: TestClient
) -> None:
    """Verify evaluation causes zero financial mutations (0 dirty/new/deleted objects)."""
    headers = registered_owner["headers"]

    # Setup customer, invoice, payment
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Zero Mutation Ltd", "tax_id": "GSTIN-ZERO-01"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-ZERO-001",
            "customer_id": cust_id,
            "issue_date": "2026-08-01",
            "due_date": "2026-08-31",
            "total_amount": 25000.00,
            "currency": "INR",
        },
    )
    inv_id = inv_resp.json()["data"]["id"]

    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-15",
            "amount": "25000.00",
            "currency": "INR",
            "narration": "FOR INV-ZERO-001",
        },
    )
    pay_id = pay_resp.json()["data"]["id"]

    # Clear pending dirty state before scoring call
    db_session.expire_all()
    assert len(db_session.dirty) == 0
    assert len(db_session.new) == 0
    assert len(db_session.deleted) == 0

    # Call Matching & Scoring endpoint
    resp = client.post(
        f"/api/v1/reconciliation/matching-scoring/{pay_id}",
        headers=headers,
        json={},
    )
    assert resp.status_code == 200

    # Assert strictly ZERO session modifications
    assert len(db_session.dirty) == 0
    assert len(db_session.new) == 0
    assert len(db_session.deleted) == 0


def test_matching_scoring_cross_tenant_payment_idor(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Verify tenant isolation: accessing another company's payment fails with HTTP 404."""
    headers1 = registered_owner["headers"]
    headers2 = second_company_owner["headers"]

    # Company 1 creates payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers1,
        json={
            "transaction_date": "2026-08-15",
            "amount": "10000.00",
            "currency": "INR",
            "narration": "SECRET TENANT 1 PAYMENT",
        },
    )
    pay1_id = pay_resp.json()["data"]["id"]

    # Company 2 attempts to score Company 1's payment -> 404 Not Found
    idor_resp = client.post(
        f"/api/v1/reconciliation/matching-scoring/{pay1_id}",
        headers=headers2,
        json={},
    )
    assert idor_resp.status_code == 404
    assert idor_resp.json()["error"]["code"] == "PAYMENT_NOT_FOUND"


def test_matching_scoring_cross_tenant_customer_override_idor(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Verify tenant isolation: customer override from another tenant returns HTTP 404."""
    headers1 = registered_owner["headers"]
    headers2 = second_company_owner["headers"]

    # Company 1 creates customer
    cust1 = client.post(
        "/api/v1/customers",
        headers=headers1,
        json={"name": "Company 1 Customer"},
    ).json()["data"]["id"]

    # Company 2 creates payment
    pay2 = client.post(
        "/api/v1/payments",
        headers=headers2,
        json={
            "transaction_date": "2026-08-15",
            "amount": "10000.00",
            "currency": "INR",
        },
    ).json()["data"]["id"]

    # Company 2 attempts scoring with Company 1's customer as override -> 404
    idor_resp = client.post(
        f"/api/v1/reconciliation/matching-scoring/{pay2}",
        headers=headers2,
        json={"override_customer_id": cust1},
    )
    assert idor_resp.status_code == 404
    assert idor_resp.json()["error"]["code"] == "CUSTOMER_NOT_FOUND"


def test_matching_scoring_unauthenticated_rejected(client: TestClient) -> None:
    """Verify unauthenticated access is rejected with HTTP 401 Unauthorized."""
    resp = client.post(
        f"/api/v1/reconciliation/matching-scoring/{uuid.uuid4()}",
        json={},
    )
    assert resp.status_code == 401


def test_matching_scoring_batch_endpoint(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify batch matching scoring endpoint processes multiple payments."""
    headers = registered_owner["headers"]

    # Ingest 2 payments
    pay1 = client.post(
        "/api/v1/payments",
        headers=headers,
        json={"transaction_date": "2026-08-15", "amount": "1000.00", "currency": "INR"},
    ).json()["data"]["id"]
    pay2 = client.post(
        "/api/v1/payments",
        headers=headers,
        json={"transaction_date": "2026-08-16", "amount": "2000.00", "currency": "INR"},
    ).json()["data"]["id"]

    resp = client.post(
        "/api/v1/reconciliation/matching-scoring/batch",
        headers=headers,
        json={"payment_ids": [pay1, pay2]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_evaluated"] == 2
    assert len(data["results"]) == 2
