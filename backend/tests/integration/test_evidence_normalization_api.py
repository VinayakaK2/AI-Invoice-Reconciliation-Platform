"""Integration, security, and zero-mutation tests for Phase 14.10 Evidence Normalization REST API.

Verifies:
1. End-to-end HTTP endpoint POST /api/v1/reconciliation/evidence-normalization/{payment_id}
2. Observed Financial Mutation: NONE (0 database writes, len(db.dirty) == 0, len(db.new) == 0).
3. Cross-tenant payment IDOR protection (404 Fail-closed).
4. Cross-tenant customer override IDOR protection (404 Fail-closed).
5. Cross-tenant candidate bleed immunity at use-case boundary (ForbiddenError).
6. Unauthenticated access rejected with 401 Unauthorized.
7. Masking of sensitive bank account numbers in normalized API responses.
8. Canonical representations, rule versions ("1.0.0"), and algorithm versions ("14.10.0") returned properly.
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
)
from app.modules.reconciliation.domain.candidate_filters import (
    CandidateFilterCriteria,
    FilteredCandidateUniverse,
)
from app.modules.reconciliation.domain.candidate_invoices import CandidateInvoice
from app.modules.reconciliation.domain.rules import PaymentIntakeContext
from app.modules.reconciliation.infrastructure.adapters import (
    SQLAlchemyCustomerLookupAdapter,
    SQLAlchemyInvoiceLookupAdapter,
    SQLAlchemyPaymentLookupAdapter,
)
from app.shared.exceptions import ForbiddenError


def test_evidence_normalization_api_e2e(
    client: TestClient, registered_owner: dict
) -> None:
    """Verify end-to-end evidence normalization endpoint returns canonical structured objects."""
    headers = registered_owner["headers"]

    # 1. Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": "Normalized Tech Labs", "tax_id": "GSTIN-NORM-01"},
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

    # 3. Create Invoice
    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-NORM-001",
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
            "bank_account_number": "112233445566",
            "payment_reference": "REF-NORM-999",
            "narration": "NEFT TRANSFER FOR INV-NORM-001 UTR: ICICIR52026081599",
        },
    )
    assert pay_resp.status_code == 201
    pay_id = pay_resp.json()["data"]["id"]

    # 5. Call Evidence Normalization API
    resp = client.post(
        f"/api/v1/reconciliation/evidence-normalization/{pay_id}",
        headers=headers,
        json={},
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["success"] is True
    data = res["data"]

    # Verify top-level structure
    assert data["payment_id"] == pay_id
    assert data["rule_version"] == "1.0.0"
    assert data["algorithm_version"] == "14.10.0"
    assert data["is_deterministic"] is True
    assert data["total_evidence_items"] > 0
    assert data["total_direct_items"] >= 2  # account match & amount match
    assert len(data["candidate_bundles"]) == 1

    # Verify payment evidence
    pay_ev = data["payment_evidence"]
    assert len(pay_ev["items"]) > 0
    types = [i["evidence_type"] for i in pay_ev["items"]]
    assert "BANK_ACCOUNT_IDENTIFIER" in types
    assert "UTR_IDENTIFIER" in types

    # Verify candidate bundle
    bundle = data["candidate_bundles"][0]
    assert bundle["invoice_id"] == inv_id
    assert bundle["invoice_number"] == "INV-NORM-001"
    assert bundle["has_conflicting_evidence"] is False

    cand_types = [i["evidence_type"] for i in bundle["items"]]
    assert "AMOUNT_EXACT_EQUALITY" in cand_types
    assert "CURRENCY_CONSISTENCY" in cand_types
    assert "DATE_CAUSALITY" in cand_types

    # Verify banking coordinates masking
    for item in pay_ev["items"]:
        if item["evidence_type"] == "BANK_ACCOUNT_IDENTIFIER":
            assert "1122" not in item["matched_value"]
            assert "5566" in item["matched_value"]


def test_evidence_normalization_zero_financial_mutation(
    db_session: Session, registered_owner: dict, client: TestClient
) -> None:
    """Verify Phase 14.10 Evidence Normalization causes zero database mutations (Rule 4)."""
    headers = registered_owner["headers"]

    # Ingest Payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers,
        json={
            "transaction_date": "2026-08-10",
            "amount": "25000.00",
            "currency": "INR",
            "narration": "TEST MUTATION CHECK",
        },
    )
    assert pay_resp.status_code == 201
    pay_id = pay_resp.json()["data"]["id"]

    # Clear session and check dirty set before
    db_session.expire_all()
    assert len(db_session.dirty) == 0
    assert len(db_session.new) == 0
    assert len(db_session.deleted) == 0

    # Call normalization endpoint
    resp = client.post(
        f"/api/v1/reconciliation/evidence-normalization/{pay_id}",
        headers=headers,
    )
    assert resp.status_code == 200

    # Verify zero mutations
    assert len(db_session.dirty) == 0
    assert len(db_session.new) == 0
    assert len(db_session.deleted) == 0


def test_evidence_normalization_cross_tenant_payment_idor(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Cross-tenant payment IDOR attempt must fail-closed with HTTP 404."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company A creates payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={
            "transaction_date": "2026-08-01",
            "amount": "10000.00",
            "currency": "INR",
            "narration": "COMPANY A PAYMENT",
        },
    )
    assert pay_resp.status_code == 201
    pay_id = pay_resp.json()["data"]["id"]

    # Company B user attempts to normalize Company A's payment
    resp = client.post(
        f"/api/v1/reconciliation/evidence-normalization/{pay_id}",
        headers=headers_b,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PAYMENT_NOT_FOUND"


def test_evidence_normalization_cross_tenant_customer_override_idor(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Cross-tenant customer override must fail-closed with HTTP 404."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company B creates a customer
    cust_b_resp = client.post(
        "/api/v1/customers",
        headers=headers_b,
        json={"name": "Company B Secret Customer", "tax_id": "GSTIN-B-NORM"},
    )
    assert cust_b_resp.status_code == 201
    cust_b_id = cust_b_resp.json()["data"]["id"]

    # Company A creates payment
    pay_resp = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={
            "transaction_date": "2026-08-01",
            "amount": "15000.00",
            "currency": "INR",
            "narration": "COMPANY A PAYMENT",
        },
    )
    assert pay_resp.status_code == 201
    pay_id = pay_resp.json()["data"]["id"]

    # Company A attempts to evaluate with Company B's customer override
    resp = client.post(
        f"/api/v1/reconciliation/evidence-normalization/{pay_id}",
        headers=headers_a,
        json={"override_customer_id": cust_b_id},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CUSTOMER_NOT_FOUND"


def test_evidence_normalization_unauthenticated_rejected(client: TestClient) -> None:
    """Unauthenticated calls must be rejected with HTTP 401."""
    random_id = uuid.uuid4()
    resp = client.post(f"/api/v1/reconciliation/evidence-normalization/{random_id}")
    assert resp.status_code == 401


def test_evidence_normalization_use_case_adversarial_cross_tenant_candidate(
    db_session: Session, registered_owner: dict, second_company_owner: dict
) -> None:
    """Adversarial cross-tenant candidate injection at use-case boundary raises ForbiddenError."""
    company_a_id = uuid.UUID(registered_owner["company"]["id"])
    company_b_id = uuid.UUID(second_company_owner["company"]["id"])

    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db_session)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db_session)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db_session)

    collection_use_case = EvidenceCollectionUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )
    normalization_use_case = EvidenceNormalizationUseCase(
        evidence_collection_use_case=collection_use_case,
    )

    pay_id = uuid.uuid4()
    pay_ctx = PaymentIntakeContext(
        payment_id=pay_id,
        company_id=company_a_id,
        amount=Decimal("50000.00"),
        currency="INR",
        payment_date=date(2026, 8, 15),
        narration="TEST",
    )

    class MockPaymentLookupPort:
        def get_payment_intake_context(self, payment_id, company_id):
            if payment_id == pay_id and company_id == company_a_id:
                return pay_ctx
            return None

    collection_use_case.payment_lookup_port = MockPaymentLookupPort()

    cand_b = CandidateInvoice(
        invoice_id=uuid.uuid4(),
        invoice_number="INV-LEAK-NORM-01",
        total_amount=Decimal("50000.00"),
        paid_amount=Decimal("0.00"),
        outstanding_amount=Decimal("50000.00"),
        currency="INR",
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 31),
        status="PENDING",
        retrieval_priority=80.0,
        evidence_signals=[],
        is_exact_amount_match=True,
        is_partial_amount_match=False,
        is_reference_match=False,
    )
    object.__setattr__(cand_b, "company_id", company_b_id)

    adv_universe = FilteredCandidateUniverse(
        payment_id=pay_id,
        company_id=company_a_id,
        customer_id=None,
        retained_candidates=[cand_b],
        excluded_candidates=[],
        filter_criteria=CandidateFilterCriteria(),
        total_evaluated=1,
        total_retained=1,
        total_excluded=0,
        currency_mismatches_detected=0,
        exclusion_breakdown={},
    )

    # Calling use case with cross-tenant candidate in filtered universe fails closed
    with pytest.raises(ForbiddenError) as exc_info:
        collection_use_case.execute(
            payment_id=pay_id,
            company_id=company_a_id,
            filtered_universe=adv_universe,
        )

    assert "belongs to company" in str(exc_info.value)
    assert "not authenticated company" in str(exc_info.value)
