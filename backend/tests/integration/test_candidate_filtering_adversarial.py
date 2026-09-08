"""Adversarial and Anomaly test suite for Phase 14.4 Candidate Filtering.

Covers:
- Case 1: Ghost Debt Anomaly (Settled debt or 0 balance)
- Case 2: Causality Inversion (Time-travel future invoices)
- Case 3: Credit Note / Negative Debt Masquerade
- Case 4: Cross-Tenant Collision & Isolation
- Case 5: Foreign Currency Homograph Trap
- Case 6: Combinatorial Flooding & Bounding Stress
- Case 7: Duplicate Candidate Stream Injection
- Case 8: Archived Entity Pruning
- Case 9: Micro-Penny / Precision & Balance Conservation
- Case 10: Concurrent Settlement Session Dirty Invariant
"""

from datetime import date
from decimal import Decimal
import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.reconciliation.domain.candidate_filters import (
    CandidateFilterCriteria,
    CandidateFilterRuleEngine,
    FilterExclusionReason,
)
from app.modules.reconciliation.domain.candidate_invoices import (
    CandidateInvoice,
    CandidateInvoiceUniverse,
)
from app.modules.reconciliation.infrastructure.adapters import (
    SQLAlchemyCustomerLookupAdapter,
    SQLAlchemyInvoiceLookupAdapter,
    SQLAlchemyPaymentLookupAdapter,
)
from app.modules.reconciliation.application.use_cases import (
    FilterCandidateInvoicesUseCase,
)


def _make_candidate(
    invoice_id: uuid.UUID = None,
    invoice_number: str = "INV-ADV-001",
    total_amount: Decimal = Decimal("10000.00"),
    paid_amount: Decimal = Decimal("0.00"),
    outstanding_amount: Decimal = Decimal("10000.00"),
    currency: str = "INR",
    issue_date: date = date(2026, 8, 1),
    due_date: date = date(2026, 8, 31),
    status: str = "PENDING",
    is_exact_amount_match: bool = False,
    is_reference_match: bool = False,
    rank: int = 1,
) -> CandidateInvoice:
    return CandidateInvoice(
        invoice_id=invoice_id or uuid.uuid4(),
        invoice_number=invoice_number,
        total_amount=total_amount,
        paid_amount=paid_amount,
        outstanding_amount=outstanding_amount,
        currency=currency,
        issue_date=issue_date,
        due_date=due_date,
        status=status,
        retrieval_priority=50.0,
        evidence_signals=[],
        is_exact_amount_match=is_exact_amount_match,
        is_partial_amount_match=False,
        is_reference_match=is_reference_match,
        rank=rank,
    )


def test_adversarial_case_01_ghost_debt_anomaly() -> None:
    """Case 1: Invoices in non-receivable state or with zero outstanding debt are excluded."""
    engine = CandidateFilterRuleEngine()
    company_id = uuid.uuid4()
    criteria = CandidateFilterCriteria()

    # An invoice marked PAID
    cand_paid = _make_candidate()
    object.__setattr__(cand_paid, "status", "PAID")
    reasons = engine.evaluate_candidate_eligibility(
        candidate=cand_paid,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("1000.00"),
        criteria=criteria,
    )
    assert FilterExclusionReason.STATUS_INELIGIBLE in reasons



def test_adversarial_case_02_future_dated_invoice_causality() -> None:
    """Case 2: Payments cannot clear invoices issued 30 days in the future."""
    engine = CandidateFilterRuleEngine()
    company_id = uuid.uuid4()
    payment_date = date(2026, 8, 1)

    cand_future = _make_candidate(
        issue_date=date(2026, 8, 31),
        due_date=date(2026, 9, 30),
    )
    reasons = engine.evaluate_candidate_eligibility(
        candidate=cand_future,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=payment_date,
        payment_effective_amount=Decimal("10000.00"),
        criteria=CandidateFilterCriteria(require_causality=True, max_advance_days=0),
    )
    assert FilterExclusionReason.NON_CAUSAL_DATE in reasons


def test_adversarial_case_03_negative_balance_exclusion() -> None:
    """Case 3: Anomaly record with negative outstanding balance is excluded."""
    engine = CandidateFilterRuleEngine()
    company_id = uuid.uuid4()

    cand = _make_candidate()
    # Mock candidate with negative balance for rule evaluation
    object.__setattr__(cand, "outstanding_amount", Decimal("-500.00"))

    reasons = engine.evaluate_candidate_eligibility(
        candidate=cand,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("10000.00"),
        criteria=CandidateFilterCriteria(),
    )
    assert FilterExclusionReason.NEGATIVE_OUTSTANDING_BALANCE in reasons


def test_adversarial_case_04_cross_tenant_collision(
    client: TestClient, registered_owner: dict, second_company_owner: dict
) -> None:
    """Case 4: Identical invoice number and customer across tenants are strictly isolated."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Tenant A
    cust_a = client.post("/api/v1/customers", headers=headers_a, json={"name": "Titan Heavy Corp"}).json()["data"]["id"]
    inv_a = client.post(
        "/api/v1/invoices",
        headers=headers_a,
        json={"invoice_number": "INV-COLLISION-1", "customer_id": cust_a, "issue_date": "2026-08-01", "due_date": "2026-08-31", "total_amount": 10000.00, "currency": "INR"},
    ).json()["data"]["id"]

    # Tenant B
    cust_b = client.post("/api/v1/customers", headers=headers_b, json={"name": "Titan Heavy Corp"}).json()["data"]["id"]
    inv_b = client.post(
        "/api/v1/invoices",
        headers=headers_b,
        json={"invoice_number": "INV-COLLISION-1", "customer_id": cust_b, "issue_date": "2026-08-01", "due_date": "2026-08-31", "total_amount": 10000.00, "currency": "INR"},
    ).json()["data"]["id"]

    # Tenant A payment
    pay_a = client.post(
        "/api/v1/payments",
        headers=headers_a,
        json={"transaction_date": "2026-08-15", "amount": "10000.00", "currency": "INR", "narration": "TITAN PAYMENT"},
    ).json()["data"]["id"]

    resp = client.post(
        f"/api/v1/reconciliation/candidates/{pay_a}/filtered",
        headers=headers_a,
        json={"override_customer_id": cust_a},
    )
    assert resp.status_code == 200
    retained = resp.json()["data"]["retained_candidates"]
    assert len(retained) == 1
    assert retained[0]["invoice_id"] == inv_a
    assert retained[0]["invoice_id"] != inv_b


def test_adversarial_case_05_foreign_currency_homograph(
    client: TestClient, registered_owner: dict
) -> None:
    """Case 5: Customer with USD invoice matching numeric payment amount in INR is rejected."""
    headers = registered_owner["headers"]
    cust_id = client.post("/api/v1/customers", headers=headers, json={"name": "Forex Trader Corp"}).json()["data"]["id"]

    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={"invoice_number": "INV-FX-001", "customer_id": cust_id, "issue_date": "2026-08-01", "due_date": "2026-08-31", "total_amount": 50000.00, "currency": "USD"},
    )

    pay_id = client.post(
        "/api/v1/payments",
        headers=headers,
        json={"transaction_date": "2026-08-15", "amount": "50000.00", "currency": "INR", "narration": "FOR INV-FX-001"},
    ).json()["data"]["id"]

    resp = client.post(
        f"/api/v1/reconciliation/candidates/{pay_id}/filtered",
        headers=headers,
        json={"override_customer_id": cust_id},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status_code"] == "CURRENCY_MISMATCH"
    assert data["currency_mismatches_detected"] == 1
    assert len(data["retained_candidates"]) == 0


def test_adversarial_case_06_combinatorial_flooding_bounding() -> None:
    """Case 6: Candidate set with 50 open invoices is clamped to max 30 candidates."""
    engine = CandidateFilterRuleEngine()
    candidates = [
        _make_candidate(
            invoice_number=f"INV-FLOOD-{i:03d}",
            outstanding_amount=Decimal(f"{1000 * (i + 1)}.00"),
            total_amount=Decimal(f"{1000 * (i + 1)}.00"),
            rank=i + 1,
        )
        for i in range(50)
    ]
    universe = CandidateInvoiceUniverse(
        payment_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        candidates=candidates,
        total_eligible_invoices=50,
        truncated=False,
        candidate_limit=30,
        truncation_reason=None,
    )

    filtered = engine.filter_universe(
        universe=universe,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("10000.00"),
        criteria=CandidateFilterCriteria(max_candidates=30),
    )

    assert filtered.total_evaluated == 50
    assert filtered.total_retained == 30
    assert filtered.total_excluded == 20
    assert [c.rank for c in filtered.retained_candidates] == list(range(1, 31))
    assert filtered.exclusion_breakdown["TRUNCATED_BY_LIMIT"] == 20


def test_adversarial_case_07_duplicate_candidate_deduplication() -> None:
    """Case 7: Duplicate candidate invoice IDs injected into candidate stream are pruned."""
    engine = CandidateFilterRuleEngine()
    cand1 = _make_candidate(invoice_number="INV-DUP-1")
    cand2 = _make_candidate(invoice_number="INV-DUP-2")

    # Repeat cand1 three times
    universe = CandidateInvoiceUniverse(
        payment_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        candidates=[cand1, cand2, cand1, cand1],
        total_eligible_invoices=4,
        truncated=False,
        candidate_limit=30,
        truncation_reason=None,
    )

    filtered = engine.filter_universe(
        universe=universe,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("10000.00"),
    )

    assert filtered.total_retained == 2
    assert filtered.total_excluded == 2
    assert set(c.invoice_id for c in filtered.retained_candidates) == {cand1.invoice_id, cand2.invoice_id}
    assert filtered.exclusion_breakdown["DUPLICATE_CANDIDATE"] == 2



def test_adversarial_case_08_archived_entity_pruning(
    client: TestClient, registered_owner: dict
) -> None:
    """Case 8: Archived invoices are never retrieved or retained."""
    headers = registered_owner["headers"]
    cust_id = client.post("/api/v1/customers", headers=headers, json={"name": "Archived Test Corp"}).json()["data"]["id"]

    inv_id = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={"invoice_number": "INV-ARCH-001", "customer_id": cust_id, "issue_date": "2026-08-01", "due_date": "2026-08-31", "total_amount": 10000.00, "currency": "INR"},
    ).json()["data"]["id"]

    # Archive the invoice
    archive_resp = client.post(f"/api/v1/invoices/{inv_id}/archive", headers=headers)
    assert archive_resp.status_code == 200

    pay_id = client.post(
        "/api/v1/payments",
        headers=headers,
        json={"transaction_date": "2026-08-15", "amount": "10000.00", "currency": "INR", "narration": "PAYMENT FOR ARCHIVED INVOICE"},
    ).json()["data"]["id"]

    resp = client.post(
        f"/api/v1/reconciliation/candidates/{pay_id}/filtered",
        headers=headers,
        json={"override_customer_id": cust_id},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["total_retained"] == 0


def test_adversarial_case_09_micro_penny_floating_point_residue() -> None:
    """Case 9: Balance conservation breach is rejected fail-closed."""
    engine = CandidateFilterRuleEngine()
    company_id = uuid.uuid4()

    cand = _make_candidate(
        total_amount=Decimal("10000.00"),
        paid_amount=Decimal("2000.00"),
        outstanding_amount=Decimal("8000.00"),
    )
    # Simulate floating point residue or corrupted ledger balance
    object.__setattr__(cand, "paid_amount", Decimal("2000.05"))

    reasons = engine.evaluate_candidate_eligibility(
        candidate=cand,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("8000.00"),
        criteria=CandidateFilterCriteria(),
    )
    assert FilterExclusionReason.BALANCE_CONSERVATION_BREACH in reasons


def test_adversarial_case_10_concurrent_settlement_dirty_check(
    db_session: Session, registered_owner: dict, client: TestClient
) -> None:
    """Case 10: Filtering leaves SQLAlchemy session completely clean with zero mutations."""
    headers = registered_owner["headers"]
    company_id = uuid.UUID(registered_owner["company"]["id"])

    cust_id = client.post("/api/v1/customers", headers=headers, json={"name": "Concurrent Check Corp"}).json()["data"]["id"]
    client.post(f"/api/v1/customers/{cust_id}/identifiers", headers=headers, json={"identifier_type": "BANK_ACCOUNT", "identifier_value": "887766554433"})

    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={"invoice_number": "INV-CC-001", "customer_id": cust_id, "issue_date": "2026-08-01", "due_date": "2026-08-31", "total_amount": 10000.00, "currency": "INR"},
    )

    pay_id = uuid.UUID(
        client.post(
            "/api/v1/payments",
            headers=headers,
            json={"transaction_date": "2026-08-15", "amount": "10000.00", "currency": "INR", "narration": "FOR INV-CC-001", "bank_account_number": "887766554433"},
        ).json()["data"]["id"]
    )

    use_case = FilterCandidateInvoicesUseCase(
        payment_lookup_port=SQLAlchemyPaymentLookupAdapter(db=db_session),
        customer_lookup_port=SQLAlchemyCustomerLookupAdapter(db=db_session),
        invoice_lookup_port=SQLAlchemyInvoiceLookupAdapter(db=db_session),
    )

    result = use_case.execute(payment_id=pay_id, company_id=company_id)

    assert result.status_code == "SUCCESS"
    assert len(db_session.dirty) == 0
    assert len(db_session.new) == 0
    assert len(db_session.deleted) == 0
