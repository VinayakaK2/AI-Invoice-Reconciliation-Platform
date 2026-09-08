"""Unit tests for Phase 14.4 Candidate Filtering deterministic domain rules.

Verifies:
- Fail-closed waterfall pipeline
- Machine-readable exclusion reason codes
- Tenant and customer isolation
- Duplicate candidate pruning
- Currency compatibility
- Status and balance invariants
- Temporal causality and lookback windows
- Amount limits and reference match constraints
- 100-run permutation invariance and determinism
"""

from datetime import date
from decimal import Decimal
import random
from typing import List, Optional
import uuid
import pytest

from app.modules.reconciliation.domain.candidate_filters import (
    CandidateFilterCriteria,
    CandidateFilterRuleEngine,
    FilterExclusionReason,
    FilteredCandidateUniverse,
)
from app.modules.reconciliation.domain.candidate_invoices import (
    CandidateInvoice,
    CandidateInvoiceEvidenceSignal,
    CandidateInvoiceUniverse,
    InvoiceEvidenceType,
)
from app.modules.reconciliation.domain.entities import SignalStrength
from app.modules.reconciliation.domain.invoice_rules import InvoiceCandidateContext
from app.shared.exceptions import DomainError


def _make_candidate(
    invoice_id: Optional[uuid.UUID] = None,
    invoice_number: str = "INV-2026-001",
    total_amount: Decimal = Decimal("10000.00"),
    paid_amount: Decimal = Decimal("0.00"),
    outstanding_amount: Decimal = Decimal("10000.00"),
    currency: str = "INR",
    issue_date: date = date(2026, 8, 1),
    due_date: date = date(2026, 8, 31),
    status: str = "PENDING",
    retrieval_priority: float = 50.0,
    is_exact_amount_match: bool = False,
    is_partial_amount_match: bool = False,
    is_reference_match: bool = False,
    rank: int = 1,
) -> CandidateInvoice:
    """Helper to instantiate valid CandidateInvoice."""
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
        retrieval_priority=retrieval_priority,
        evidence_signals=[],
        is_exact_amount_match=is_exact_amount_match,
        is_partial_amount_match=is_partial_amount_match,
        is_reference_match=is_reference_match,
        rank=rank,
    )


def _make_universe(
    candidates: List[CandidateInvoice],
    payment_id: Optional[uuid.UUID] = None,
    company_id: Optional[uuid.UUID] = None,
    customer_id: Optional[uuid.UUID] = None,
) -> CandidateInvoiceUniverse:
    """Helper to instantiate CandidateInvoiceUniverse."""
    return CandidateInvoiceUniverse(
        payment_id=payment_id or uuid.uuid4(),
        company_id=company_id or uuid.uuid4(),
        customer_id=customer_id or uuid.uuid4(),
        candidates=candidates,
        total_eligible_invoices=len(candidates),
        truncated=False,
        candidate_limit=30,
        truncation_reason=None,
    )


def test_filter_criteria_validation() -> None:
    """Test invariant validation on CandidateFilterCriteria initialization."""
    # Negative min_amount
    with pytest.raises(DomainError, match="min_amount must be non-negative"):
        CandidateFilterCriteria(min_amount=Decimal("-10.00"))

    # Negative max_amount
    with pytest.raises(DomainError, match="max_amount must be non-negative"):
        CandidateFilterCriteria(max_amount=Decimal("-10.00"))

    # min_amount > max_amount
    with pytest.raises(DomainError, match="cannot exceed max_amount"):
        CandidateFilterCriteria(min_amount=Decimal("100.00"), max_amount=Decimal("50.00"))

    # max_candidates out of bounds
    with pytest.raises(DomainError, match="max_candidates must be between 1 and 100"):
        CandidateFilterCriteria(max_candidates=0)
    with pytest.raises(DomainError, match="max_candidates must be between 1 and 100"):
        CandidateFilterCriteria(max_candidates=101)


def test_filter_excludes_tenant_mismatch() -> None:
    """Gate 1: Verify that an invoice from a foreign tenant is strictly excluded."""
    engine = CandidateFilterRuleEngine()
    company_a = uuid.uuid4()
    company_b = uuid.uuid4()
    cand = _make_candidate()
    criteria = CandidateFilterCriteria()

    reasons = engine.evaluate_candidate_eligibility(
        candidate=cand,
        payment_company_id=company_a,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("10000.00"),
        criteria=criteria,
        candidate_company_id=company_b,
    )

    assert FilterExclusionReason.TENANT_MISMATCH in reasons


def test_filter_excludes_customer_mismatch() -> None:
    """Gate 2: Verify that an invoice belonging to a different customer is excluded."""
    engine = CandidateFilterRuleEngine()
    company_id = uuid.uuid4()
    cust_a = uuid.uuid4()
    cust_b = uuid.uuid4()
    cand = _make_candidate()
    criteria = CandidateFilterCriteria()

    reasons = engine.evaluate_candidate_eligibility(
        candidate=cand,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("10000.00"),
        criteria=criteria,
        resolved_customer_id=cust_a,
        candidate_customer_id=cust_b,
    )

    assert FilterExclusionReason.CUSTOMER_MISMATCH in reasons


def test_filter_excludes_archived_entities() -> None:
    """Gate 3 & 4: Verify exclusion of archived customer and archived invoice."""
    engine = CandidateFilterRuleEngine()
    company_id = uuid.uuid4()
    cand = _make_candidate()
    criteria = CandidateFilterCriteria()

    # Archived invoice
    reasons_inv = engine.evaluate_candidate_eligibility(
        candidate=cand,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("10000.00"),
        criteria=criteria,
        is_archived=True,
    )
    assert FilterExclusionReason.ARCHIVED_INVOICE in reasons_inv

    # Archived customer
    reasons_cust = engine.evaluate_candidate_eligibility(
        candidate=cand,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("10000.00"),
        criteria=criteria,
        customer_is_archived=True,
    )
    assert FilterExclusionReason.CUSTOMER_ARCHIVED in reasons_cust


def test_filter_excludes_currency_mismatch() -> None:
    """Gate 5: Verify currency mismatch and malformed currency code rejection."""
    engine = CandidateFilterRuleEngine()
    company_id = uuid.uuid4()
    criteria = CandidateFilterCriteria()

    # Different currency: USD vs INR
    cand_usd = _make_candidate(currency="USD")
    reasons = engine.evaluate_candidate_eligibility(
        candidate=cand_usd,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("10000.00"),
        criteria=criteria,
    )
    assert FilterExclusionReason.CURRENCY_MISMATCH in reasons

    # Malformed currency
    cand_bad = _make_candidate(currency="12")
    reasons_bad = engine.evaluate_candidate_eligibility(
        candidate=cand_bad,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("10000.00"),
        criteria=criteria,
    )
    assert FilterExclusionReason.CURRENCY_MISMATCH in reasons_bad


def test_filter_deduplicates_identical_invoices() -> None:
    """Gate 3 (Deduplication): Repeated candidate invoice IDs are pruned."""
    engine = CandidateFilterRuleEngine()
    cand1 = _make_candidate(invoice_number="INV-001", retrieval_priority=80.0)
    cand2 = _make_candidate(invoice_number="INV-002", retrieval_priority=50.0)
    # Duplicate instance of cand1
    cand1_dup = CandidateInvoice(
        invoice_id=cand1.invoice_id,
        invoice_number=cand1.invoice_number,
        total_amount=cand1.total_amount,
        paid_amount=cand1.paid_amount,
        outstanding_amount=cand1.outstanding_amount,
        currency=cand1.currency,
        issue_date=cand1.issue_date,
        due_date=cand1.due_date,
        status=cand1.status,
        retrieval_priority=cand1.retrieval_priority,
        evidence_signals=[],
        is_exact_amount_match=False,
        is_partial_amount_match=False,
        is_reference_match=False,
    )

    universe = _make_universe(candidates=[cand1, cand2, cand1_dup])
    filtered = engine.filter_universe(
        universe=universe,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("10000.00"),
    )

    assert filtered.total_evaluated == 3
    assert filtered.total_retained == 2
    assert filtered.total_excluded == 1
    assert [c.invoice_id for c in filtered.retained_candidates] == [cand1.invoice_id, cand2.invoice_id]
    assert filtered.excluded_candidates[0].exclusion_reasons == [FilterExclusionReason.DUPLICATE_CANDIDATE]
    assert filtered.exclusion_breakdown["DUPLICATE_CANDIDATE"] == 1


def test_filter_excludes_ineligible_statuses() -> None:
    """Gate 6: Only allowed statuses pass."""
    engine = CandidateFilterRuleEngine()
    company_id = uuid.uuid4()
    criteria = CandidateFilterCriteria(allowed_statuses={"PENDING"})

    # PARTIALLY_PAID candidate when only PENDING allowed
    cand = _make_candidate(status="PARTIALLY_PAID", paid_amount=Decimal("2000.00"), outstanding_amount=Decimal("8000.00"))
    reasons = engine.evaluate_candidate_eligibility(
        candidate=cand,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("8000.00"),
        criteria=criteria,
    )
    assert FilterExclusionReason.STATUS_INELIGIBLE in reasons


def test_filter_temporal_causality_and_grace() -> None:
    """Gate 9: Temporal causality rules and grace window handling."""
    engine = CandidateFilterRuleEngine()
    company_id = uuid.uuid4()
    payment_date = date(2026, 8, 15)

    # Inverted dates (due_date < issue_date)
    cand_inverted = _make_candidate(issue_date=date(2026, 8, 20), due_date=date(2026, 8, 10))
    reasons_inverted = engine.evaluate_candidate_eligibility(
        candidate=cand_inverted,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=payment_date,
        payment_effective_amount=Decimal("10000.00"),
        criteria=CandidateFilterCriteria(),
    )
    assert FilterExclusionReason.NON_CAUSAL_DATE in reasons_inverted

    # Future issue date beyond grace: Payment 2026-08-15, Issue 2026-08-25 (10 days in future)
    cand_future = _make_candidate(issue_date=date(2026, 8, 25), due_date=date(2026, 9, 25))
    reasons_future = engine.evaluate_candidate_eligibility(
        candidate=cand_future,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=payment_date,
        payment_effective_amount=Decimal("10000.00"),
        criteria=CandidateFilterCriteria(require_causality=True, max_advance_days=0),
    )
    assert FilterExclusionReason.NON_CAUSAL_DATE in reasons_future

    # Future issue date WITHIN grace: Payment 2026-08-15, Issue 2026-08-18 (3 days in future, grace 5d)
    cand_grace = _make_candidate(issue_date=date(2026, 8, 18), due_date=date(2026, 9, 18))
    reasons_grace = engine.evaluate_candidate_eligibility(
        candidate=cand_grace,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=payment_date,
        payment_effective_amount=Decimal("10000.00"),
        criteria=CandidateFilterCriteria(require_causality=True, max_advance_days=5),
    )
    assert FilterExclusionReason.NON_CAUSAL_DATE not in reasons_grace


def test_filter_lookback_window_and_reference_exception() -> None:
    """Gate 9: Stale invoices (>365 days) excluded unless reference match present."""
    engine = CandidateFilterRuleEngine()
    company_id = uuid.uuid4()
    payment_date = date(2026, 8, 15)

    # Invoice issued 500 days ago, no reference match
    cand_stale = _make_candidate(
        issue_date=date(2025, 3, 1),
        due_date=date(2025, 3, 31),
        is_reference_match=False,
    )
    reasons_stale = engine.evaluate_candidate_eligibility(
        candidate=cand_stale,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=payment_date,
        payment_effective_amount=Decimal("10000.00"),
        criteria=CandidateFilterCriteria(max_lookback_days=365, allow_expired_debt_with_reference=True),
    )
    assert FilterExclusionReason.DATE_OUT_OF_WINDOW in reasons_stale

    # Invoice issued 500 days ago, WITH reference match
    cand_ref = _make_candidate(
        issue_date=date(2025, 3, 1),
        due_date=date(2025, 3, 31),
        is_reference_match=True,
    )
    reasons_ref = engine.evaluate_candidate_eligibility(
        candidate=cand_ref,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=payment_date,
        payment_effective_amount=Decimal("10000.00"),
        criteria=CandidateFilterCriteria(max_lookback_days=365, allow_expired_debt_with_reference=True),
    )
    assert FilterExclusionReason.DATE_OUT_OF_WINDOW not in reasons_ref


def test_filter_amount_policy_bounds() -> None:
    """Gate 8: Amount range filters and disallow_overpayment policy."""
    engine = CandidateFilterRuleEngine()
    company_id = uuid.uuid4()
    payment_date = date(2026, 8, 15)

    # Below min_amount
    cand_small = _make_candidate(outstanding_amount=Decimal("500.00"), total_amount=Decimal("500.00"))
    reasons_small = engine.evaluate_candidate_eligibility(
        candidate=cand_small,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=payment_date,
        payment_effective_amount=Decimal("1000.00"),
        criteria=CandidateFilterCriteria(min_amount=Decimal("1000.00")),
    )
    assert FilterExclusionReason.AMOUNT_BELOW_MINIMUM in reasons_small

    # Above max_amount
    cand_large = _make_candidate(outstanding_amount=Decimal("50000.00"), total_amount=Decimal("50000.00"))
    reasons_large = engine.evaluate_candidate_eligibility(
        candidate=cand_large,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=payment_date,
        payment_effective_amount=Decimal("1000.00"),
        criteria=CandidateFilterCriteria(max_amount=Decimal("20000.00")),
    )
    assert FilterExclusionReason.AMOUNT_ABOVE_MAXIMUM in reasons_large

    # Overpayment disallowed: invoice outstanding (15,000) > payment effective (10,000)
    cand_over = _make_candidate(outstanding_amount=Decimal("15000.00"), total_amount=Decimal("15000.00"))
    reasons_over = engine.evaluate_candidate_eligibility(
        candidate=cand_over,
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=payment_date,
        payment_effective_amount=Decimal("10000.00"),
        criteria=CandidateFilterCriteria(disallow_overpayment=True),
    )
    assert FilterExclusionReason.EXCEEDS_PAYMENT_AMOUNT in reasons_over


def test_filter_max_candidates_bounding_and_ranking() -> None:
    """Verify bounding to max_candidates limit and sequential rank re-assignment."""
    engine = CandidateFilterRuleEngine()
    candidates = [
        _make_candidate(
            invoice_number=f"INV-{i:03d}",
            outstanding_amount=Decimal(f"{1000 * (i + 1)}.00"),
            total_amount=Decimal(f"{1000 * (i + 1)}.00"),
            rank=i + 1,
        )
        for i in range(10)
    ]
    universe = _make_universe(candidates=candidates)

    criteria = CandidateFilterCriteria(max_candidates=5)
    filtered = engine.filter_universe(
        universe=universe,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("10000.00"),
        criteria=criteria,
    )

    assert filtered.total_evaluated == 10
    assert filtered.total_retained == 5
    assert filtered.total_excluded == 5
    assert [c.rank for c in filtered.retained_candidates] == [1, 2, 3, 4, 5]
    assert all(
        e.exclusion_reasons == [FilterExclusionReason.TRUNCATED_BY_LIMIT]
        for e in filtered.excluded_candidates
    )
    assert filtered.exclusion_breakdown["TRUNCATED_BY_LIMIT"] == 5


def test_filter_100_run_permutation_determinism() -> None:
    """Verify 100-run permutation invariance: identical retained and excluded results."""
    engine = CandidateFilterRuleEngine()
    candidates = [
        _make_candidate(
            invoice_number=f"INV-{i:03d}",
            outstanding_amount=Decimal(f"{1000 * (i + 1)}.00"),
            total_amount=Decimal(f"{1000 * (i + 1)}.00"),
            due_date=date(2026, 8, 10 + (i % 15)),
        )
        for i in range(15)
    ]
    universe = _make_universe(candidates=candidates)
    criteria = CandidateFilterCriteria(max_candidates=10)

    baseline = engine.filter_universe(
        universe=universe,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("5000.00"),
        criteria=criteria,
    )
    baseline_retained_ids = [c.invoice_id for c in baseline.retained_candidates]
    baseline_excluded_ids = [e.invoice_id for e in baseline.excluded_candidates]

    for _ in range(100):
        shuffled_candidates = list(candidates)
        random.shuffle(shuffled_candidates)
        run_universe = _make_universe(candidates=shuffled_candidates)

        run_filtered = engine.filter_universe(
            universe=run_universe,
            payment_currency="INR",
            payment_date=date(2026, 8, 15),
            payment_effective_amount=Decimal("5000.00"),
            criteria=criteria,
        )

        assert [c.invoice_id for c in run_filtered.retained_candidates] == baseline_retained_ids
        assert len(run_filtered.excluded_candidates) == len(baseline_excluded_ids)


def test_filter_contexts_fast_pruning() -> None:
    """Verify filter_contexts on raw InvoiceCandidateContext objects."""
    engine = CandidateFilterRuleEngine()
    company_id = uuid.uuid4()
    cust_id = uuid.uuid4()

    valid_context = InvoiceCandidateContext(
        id=uuid.uuid4(),
        company_id=company_id,
        customer_id=cust_id,
        invoice_number="INV-001",
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 31),
        total_amount=Decimal("10000.00"),
        paid_amount=Decimal("0.00"),
        outstanding_amount=Decimal("10000.00"),
        currency="INR",
        status="PENDING",
        is_archived=False,
    )

    archived_context = InvoiceCandidateContext(
        id=uuid.uuid4(),
        company_id=company_id,
        customer_id=cust_id,
        invoice_number="INV-002",
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 31),
        total_amount=Decimal("10000.00"),
        paid_amount=Decimal("0.00"),
        outstanding_amount=Decimal("10000.00"),
        currency="INR",
        status="PENDING",
        is_archived=True,
    )

    accepted = engine.filter_contexts(
        invoices=[valid_context, archived_context],
        payment_company_id=company_id,
        payment_currency="INR",
        payment_date=date(2026, 8, 15),
        payment_effective_amount=Decimal("10000.00"),
        resolved_customer_id=cust_id,
    )

    assert len(accepted) == 1
    assert accepted[0].id == valid_context.id
