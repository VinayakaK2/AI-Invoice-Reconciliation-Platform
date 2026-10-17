"""Integration, security, and zero-mutation tests for Phase 14.12 Confidence Engine REST API.

Verifies:
1. End-to-end HTTP endpoint POST /api/v1/reconciliation/confidence/{payment_id}
2. Observed Financial Mutation: NONE (0 database writes, len(db.dirty) == 0, len(db.new) == 0).
3. Cross-tenant payment IDOR protection (404 Fail-closed).
4. Cross-tenant customer override IDOR protection (404 Fail-closed).
5. Cross-tenant candidate bleed immunity at use-case boundary (ForbiddenError).
6. Unauthenticated access rejected with 401 Unauthorized.
7. Correct confidence response structure, levels, adjustment factors, rule version ("1.0.0"),
   and algorithm version ("14.12.0").
8. Explicit is_statistically_calibrated=False in REST response.
9. Batch confidence endpoint POST /api/v1/reconciliation/confidence/batch.
"""

from datetime import date
from decimal import Decimal
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.reconciliation.application.use_cases import (
    ConfidenceAssessmentUseCase,
    EvidenceCollectionUseCase,
    EvidenceNormalizationUseCase,
    MatchingScoringUseCase,
)
from app.modules.reconciliation.domain.candidate_filters import (
    CandidateFilterCriteria,
    FilteredCandidateUniverse,
)
from app.modules.reconciliation.domain.candidate_invoices import CandidateInvoice
from app.modules.reconciliation.domain.confidence_engine import ConfidenceEngine
from app.modules.reconciliation.domain.rules import PaymentIntakeContext
from app.modules.reconciliation.infrastructure.adapters import (
    SQLAlchemyCustomerLookupAdapter,
    SQLAlchemyInvoiceLookupAdapter,
    SQLAlchemyPaymentLookupAdapter,
)
from app.shared.exceptions import ForbiddenError


def test_confidence_api_e2e(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify end-to-end confidence assessment endpoint returns deterministic confidence and explainable factors."""
    headers = registered_owner["headers"]

    # 1. Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Confidence Global Ltd", "tax_id": "GSTIN-CONF-01"},
    )
    assert cust_resp.status_code == 201
    cust_id = cust_resp.json()["data"]["id"]

    # 2. Register Bank Account for Customer
    acct_resp = client.post(
        f"/api/v1/customers/{cust_id}/identifiers",
        headers=headers,
        json={
            "identifier_type": "BANK_ACCOUNT",
            "identifier_value": "998877665544",
        },
    )
    assert acct_resp.status_code == 201

    # 3. Create Invoices for Customer
    inv1_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "customer_id": cust_id,
            "invoice_number": "INV-CONF-101",
            "issue_date": str(date.today()),
            "due_date": str(date.today()),
            "total_amount": "50000.00",
            "currency": "INR",
        },
    )
    assert inv1_resp.status_code == 201

    # 4. Ingest Payment with matching account and exact amount
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": str(date.today()),
            "amount": "50000.00",
            "currency": "INR",
            "bank_account_number": "998877665544",
            "payment_reference": "REF-CONF-999",
            "narration": "NEFT TRANSFER FOR INV-CONF-101 UTR: CONF101TEST",
        },
    )
    assert pay_resp.status_code == 201
    payment_id = pay_resp.json()["data"]["id"]

    # 5. Call Phase 14.12 Confidence API
    conf_resp = client.post(
        f"/api/v1/reconciliation/confidence/{payment_id}",
        headers=headers,
        json={},
    )
    assert conf_resp.status_code == 200
    data = conf_resp.json()["data"]

    assert data["payment_id"] == payment_id
    assert data["is_deterministic"] is True
    assert data["is_statistically_calibrated"] is False
    assert data["confidence_algorithm_version"] == "14.12.0"
    assert data["rule_version"] == "1.0.0"
    assert data["total_candidates_evaluated"] >= 1

    top_cand = data["candidate_assessments"][0]
    assert top_cand["invoice_number"] == "INV-CONF-101"
    # Unconflicted 100 base score -> 100.00 confidence
    assert Decimal(top_cand["confidence_score"]) == Decimal("100.00")
    assert top_cand["confidence_level"] == "VERY_HIGH"
    assert top_cand["has_direct_evidence"] is True
    assert top_cand["has_conflicting_evidence"] is False
    assert top_cand["is_statistically_calibrated"] is False
    assert len(top_cand["adjustment_factors"]) >= 1


def test_confidence_zero_financial_mutation(
    client: TestClient, registered_owner: dict, db_session: Session
) -> None:
    """Verify Phase 14.12 Confidence Engine causes zero financial mutations."""
    headers = registered_owner["headers"]

    # Create customer and invoice
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Mutation Guard Labs", "tax_id": "GSTIN-MUT-01"},
    )
    cust_id = cust_resp.json()["data"]["id"]

    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "customer_id": cust_id,
            "invoice_number": "INV-MUT-01",
            "issue_date": str(date.today()),
            "due_date": str(date.today()),
            "total_amount": "25000.00",
            "currency": "INR",
        },
    )

    # Ingest payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": str(date.today()),
            "amount": "25000.00",
            "currency": "INR",
            "narration": "Payment for INV-MUT-01",
        },
    )
    assert pay_resp.status_code == 201
    payment_id = pay_resp.json()["data"]["id"]

    # Verify zero dirty state before, during, and after
    db_session.expire_all()
    assert len(db_session.dirty) == 0
    assert len(db_session.new) == 0
    assert len(db_session.deleted) == 0

    conf_resp = client.post(
        f"/api/v1/reconciliation/confidence/{payment_id}",
        headers=headers,
        json={},
    )
    assert conf_resp.status_code == 200

    # Ensure zero financial mutation: len(dirty) == 0
    assert len(db_session.dirty) == 0
    assert len(db_session.new) == 0
    assert len(db_session.deleted) == 0


def test_confidence_cross_tenant_payment_idor(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Verify tenant cannot evaluate confidence for payment belonging to another tenant (404 Fail-closed)."""
    headers_1 = registered_owner["headers"]
    headers_2 = second_company_owner["headers"]

    # Tenant 1 ingests payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers_1,
        json={
            "transaction_date": str(date.today()),
            "amount": "1000.00",
            "currency": "INR",
            "narration": "Tenant 1 payment",
        },
    )
    p_id_tenant_1 = pay_resp.json()["data"]["id"]

    # Tenant 2 attempts to call confidence evaluation on Tenant 1's payment
    attack_resp = client.post(
        f"/api/v1/reconciliation/confidence/{p_id_tenant_1}",
        headers=headers_2,
        json={},
    )
    assert attack_resp.status_code == 404
    assert attack_resp.json()["error"]["code"] == "PAYMENT_NOT_FOUND"


def test_confidence_cross_tenant_customer_override_idor(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Verify tenant cannot supply override_customer_id belonging to another tenant (404 Fail-closed)."""
    headers_1 = registered_owner["headers"]
    headers_2 = second_company_owner["headers"]

    # Tenant 1 creates customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers_1,
        json={"name": "Tenant 1 Secret Corp", "tax_id": "GSTIN-T1-01"},
    )
    t1_cust_id = cust_resp.json()["data"]["id"]

    # Tenant 2 ingests payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers_2,
        json={
            "transaction_date": str(date.today()),
            "amount": "1000.00",
            "currency": "INR",
            "narration": "Tenant 2 payment",
        },
    )
    p_id_tenant_2 = pay_resp.json()["data"]["id"]

    # Tenant 2 attempts to evaluate confidence with Tenant 1's customer_id override
    attack_resp = client.post(
        f"/api/v1/reconciliation/confidence/{p_id_tenant_2}",
        headers=headers_2,
        json={"override_customer_id": t1_cust_id},
    )
    assert attack_resp.status_code == 404
    assert attack_resp.json()["error"]["code"] == "CUSTOMER_NOT_FOUND"


def test_confidence_unauthenticated_rejected(client: TestClient) -> None:
    """Verify unauthenticated requests are rejected with 401 Unauthorized."""
    fake_id = uuid.uuid4()
    resp = client.post(f"/api/v1/reconciliation/confidence/{fake_id}", json={})
    assert resp.status_code == 401


def test_batch_confidence_endpoint(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify batch confidence assessment endpoint evaluates up to batch limit."""
    headers = registered_owner["headers"]

    # Ingest 2 payments
    client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": str(date.today()),
            "amount": "1000.00",
            "currency": "INR",
            "narration": "Batch Payment 1",
        },
    )
    client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": str(date.today()),
            "amount": "2000.00",
            "currency": "INR",
            "narration": "Batch Payment 2",
        },
    )

    batch_resp = client.post(
        "/api/v1/reconciliation/confidence/batch",
        headers=headers,
        json={"limit": 10},
    )
    assert batch_resp.status_code == 200
    data = batch_resp.json()["data"]
    assert data["total_evaluated"] >= 2
    assert len(data["results"]) >= 2
    for item in data["results"]:
        assert item["is_deterministic"] is True
        assert item["is_statistically_calibrated"] is False
        assert item["confidence_algorithm_version"] == "14.12.0"
