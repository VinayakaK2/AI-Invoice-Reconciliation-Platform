"""Unit tests for Phase 14.6 Partial 1:1 Matching deterministic domain rules.

Verifies:
- Criteria and Hypothesis invariant validations
- Single unambiguous partial 1:1 amount matching (Payment < Invoice Outstanding)
- Compares against authoritative outstanding amount (never original total if paid > 0)
- Exact payment (P == O) is delegated / rejected as non-partial
- Overpayment (P > O) is rejected as non-partial
- Multi-invoice sum non-matches (deferred to Phase 14.7)
- Ambiguity preservation when multiple candidates can accept partial payment
- Explicit reference preservation without autonomous tie-breaking
- Incomplete / truncated universe handling
- Currency compatibility & Zero FX policy
- 100-run permutation determinism
- Strict Decimal precision (micro-penny boundary 100.00 vs 99.99 -> 0.01)
"""

from datetime import date
from decimal import Decimal
import random
from typing import List, Optional
import uuid
import pytest

from app.modules.reconciliation.domain.candidate_filters import (
    CandidateFilterCriteria,
    ExcludedCandidateInvoice,
    FilterExclusionReason,
    FilteredCandidateUniverse,
)
from app.modules.reconciliation.domain.candidate_invoices import (
    CandidateInvoice,
    CandidateInvoiceEvidenceSignal,
    InvoiceEvidenceType,
)
from app.modules.reconciliation.domain.entities import SignalStrength
from app.modules.reconciliation.domain.partial_matching import (
    PartialMatchCriteria,
    PartialMatchEvidenceType,
    PartialMatchHypothesis,
    PartialMatchReasonCode,
    PartialMatchResult,
    PartialMatchRuleEngine,
    PartialMatchStatus,
)
from app.modules.reconciliation.domain.rules import PaymentIntakeContext
from app.shared.exceptions import DomainError, FinancialInvariantError


def _make_payment(
    amount: Decimal = Decimal("4000.00"),
    currency: str = "INR",
    payment_date: date = date(2026, 8, 15),
    narration: str = "PARTIAL SETTLEMENT",
    payment_reference: str = "REF12345",
    payment_id: Optional[uuid.UUID] = None,
    company_id: Optional[uuid.UUID] = None,
) -> PaymentIntakeContext:
    """Helper to instantiate valid PaymentIntakeContext."""
    return PaymentIntakeContext(
        payment_id=payment_id or uuid.uuid4(),
        company_id=company_id or uuid.uuid4(),
        amount=amount,
        currency=currency,
        payment_date=payment_date,
        narration=narration,
        payment_reference=payment_reference,
    )


def _make_candidate(
    invoice_id: Optional[uuid.UUID] = None,
    invoice_number: str = "INV-2026-001",
    total_amount: Optional[Decimal] = None,
    paid_amount: Decimal = Decimal("0.00"),
    outstanding_amount: Decimal = Decimal("10000.00"),
    currency: str = "INR",
    issue_date: date = date(2026, 8, 1),
    due_date: date = date(2026, 8, 31),
    status: str = "PENDING",
    retrieval_priority: float = 50.0,
    is_exact_amount_match: bool = False,
    is_partial_amount_match: bool = True,
    is_reference_match: bool = False,
    rank: int = 1,
) -> CandidateInvoice:
    """Helper to instantiate valid CandidateInvoice."""
    tot = total_amount if total_amount is not None else (paid_amount + outstanding_amount)
    return CandidateInvoice(
        invoice_id=invoice_id or uuid.uuid4(),
        invoice_number=invoice_number,
        total_amount=tot,
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


def _make_filtered_universe(
    retained_candidates: List[CandidateInvoice],
    excluded_candidates: Optional[List[ExcludedCandidateInvoice]] = None,
    payment_id: Optional[uuid.UUID] = None,
    company_id: Optional[uuid.UUID] = None,
    customer_id: Optional[uuid.UUID] = None,
    status_code: str = "SUCCESS",
    is_truncated: bool = False,
) -> FilteredCandidateUniverse:
    """Helper to instantiate valid FilteredCandidateUniverse."""
    p_id = payment_id or uuid.uuid4()
    c_id = company_id or uuid.uuid4()
    cust_id = customer_id or uuid.uuid4()
    excl = excluded_candidates or []
    breakdown = {}
    if is_truncated:
        breakdown[FilterExclusionReason.TRUNCATED_BY_LIMIT.value] = len(excl) or 1

    return FilteredCandidateUniverse(
        payment_id=p_id,
        company_id=c_id,
        customer_id=cust_id,
        retained_candidates=retained_candidates,
        excluded_candidates=excl,
        filter_criteria=CandidateFilterCriteria(),
        total_evaluated=len(retained_candidates) + len(excl),
        total_retained=len(retained_candidates),
        total_excluded=len(excl),
        currency_mismatches_detected=0,
        exclusion_breakdown=breakdown,
        status_code=status_code,
    )


def test_partial_match_criteria_validation() -> None:
    """Test invariant validation on PartialMatchCriteria."""
    with pytest.raises(DomainError, match="amount_tolerance must be non-negative"):
        PartialMatchCriteria(amount_tolerance=Decimal("-0.01"))

    with pytest.raises(DomainError, match="date_proximity_days must be non-negative"):
        PartialMatchCriteria(date_proximity_days=-5)


def test_partial_match_hypothesis_invariants() -> None:
    """Test strict financial invariants on PartialMatchHypothesis."""
    inv_id = uuid.uuid4()

    # 1. Non-positive matched amount
    with pytest.raises(FinancialInvariantError, match="Matched amount must be strictly positive"):
        PartialMatchHypothesis(
            invoice_id=inv_id,
            invoice_number="INV-001",
            matched_amount=Decimal("0.00"),
            invoice_outstanding_before=Decimal("1000.00"),
            invoice_outstanding_after=Decimal("1000.00"),
            payment_unallocated_before=Decimal("0.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
        )

    # 2. Non-positive invoice outstanding balance
    with pytest.raises(FinancialInvariantError, match="Invoice outstanding balance before match must be positive"):
        PartialMatchHypothesis(
            invoice_id=inv_id,
            invoice_number="INV-001",
            matched_amount=Decimal("500.00"),
            invoice_outstanding_before=Decimal("0.00"),
            invoice_outstanding_after=Decimal("-500.00"),
            payment_unallocated_before=Decimal("500.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
        )

    # 3. Matched amount equals or exceeds outstanding before (not partial)
    with pytest.raises(FinancialInvariantError, match="matched_amount .* < invoice_outstanding_before"):
        PartialMatchHypothesis(
            invoice_id=inv_id,
            invoice_number="INV-001",
            matched_amount=Decimal("1000.00"),
            invoice_outstanding_before=Decimal("1000.00"),
            invoice_outstanding_after=Decimal("0.00"),
            payment_unallocated_before=Decimal("1000.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
        )

    # 4. Projected invoice outstanding after is inconsistent
    with pytest.raises(FinancialInvariantError, match="Projected invoice outstanding after partial match must be"):
        PartialMatchHypothesis(
            invoice_id=inv_id,
            invoice_number="INV-001",
            matched_amount=Decimal("400.00"),
            invoice_outstanding_before=Decimal("1000.00"),
            invoice_outstanding_after=Decimal("500.00"),  # Expected 600.00
            payment_unallocated_before=Decimal("400.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
        )

    # 5. Payment unallocated before does not match matched amount
    with pytest.raises(FinancialInvariantError, match="Payment unallocated before match .* must equal matched amount"):
        PartialMatchHypothesis(
            invoice_id=inv_id,
            invoice_number="INV-001",
            matched_amount=Decimal("400.00"),
            invoice_outstanding_before=Decimal("1000.00"),
            invoice_outstanding_after=Decimal("600.00"),
            payment_unallocated_before=Decimal("500.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
        )

    # 6. Payment unallocated after is non-zero
    with pytest.raises(FinancialInvariantError, match="Projected payment unallocated after partial match .* must be 0.00"):
        PartialMatchHypothesis(
            invoice_id=inv_id,
            invoice_number="INV-001",
            matched_amount=Decimal("400.00"),
            invoice_outstanding_before=Decimal("1000.00"),
            invoice_outstanding_after=Decimal("600.00"),
            payment_unallocated_before=Decimal("400.00"),
            payment_unallocated_after=Decimal("50.00"),
            currency="INR",
        )


def test_single_partial_amount_match_success() -> None:
    """Test standard partial match: Payment ₹40,000 < Invoice Outstanding ₹100,000."""
    payment = _make_payment(amount=Decimal("40000.00"), narration="PARTIAL PAYMENT")
    candidate = _make_candidate(
        invoice_number="INV-100K",
        total_amount=Decimal("100000.00"),
        outstanding_amount=Decimal("100000.00"),
        is_reference_match=False,
    )
    universe = _make_filtered_universe(retained_candidates=[candidate])

    engine = PartialMatchRuleEngine()
    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == PartialMatchStatus.PARTIAL_MATCH
    assert result.total_partial_candidates_found == 1
    assert result.matched_candidate is not None
    assert result.matched_candidate.invoice_number == "INV-100K"
    assert result.matched_candidate.matched_amount == Decimal("40000.00")
    assert result.matched_candidate.invoice_outstanding_before == Decimal("100000.00")
    assert result.matched_candidate.invoice_outstanding_after == Decimal("60000.00")
    assert result.matched_candidate.payment_unallocated_before == Decimal("40000.00")
    assert result.matched_candidate.payment_unallocated_after == Decimal("0.00")
    assert result.reason_code == PartialMatchReasonCode.PARTIAL_AMOUNT_MATCH_FOUND
    assert "applied: 40000.00, remaining: 60000.00" in result.reason_description


def test_single_partial_amount_with_reference_match() -> None:
    """Test partial match with explicit invoice reference in narration."""
    payment = _make_payment(
        amount=Decimal("40000.00"),
        narration="PARTIAL PAYMENT FOR INV-100K REF",
    )
    candidate = _make_candidate(
        invoice_number="INV-100K",
        total_amount=Decimal("100000.00"),
        outstanding_amount=Decimal("100000.00"),
        is_reference_match=True,
    )
    universe = _make_filtered_universe(retained_candidates=[candidate])

    engine = PartialMatchRuleEngine()
    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == PartialMatchStatus.PARTIAL_MATCH
    assert result.reason_code == PartialMatchReasonCode.PARTIAL_AMOUNT_WITH_REFERENCE_MATCH
    assert result.matched_candidate.is_reference_match is True
    assert any(
        s.evidence_type == PartialMatchEvidenceType.INVOICE_NUMBER_MATCH
        for s in result.matched_candidate.evidence_signals
    )


def test_partial_match_uses_authoritative_outstanding_not_total() -> None:
    """Test that engine evaluates against current outstanding balance, NOT original total.

    Original: ₹100,000, Already Paid: ₹40,000, Outstanding: ₹60,000.
    Payment: ₹30,000.
    Applied: ₹30,000, Remaining: ₹30,000.
    """
    payment = _make_payment(amount=Decimal("30000.00"))
    candidate = _make_candidate(
        invoice_number="INV-PARTIALLY-PAID",
        total_amount=Decimal("100000.00"),
        paid_amount=Decimal("40000.00"),
        outstanding_amount=Decimal("60000.00"),
        status="PARTIALLY_PAID",
    )
    universe = _make_filtered_universe(retained_candidates=[candidate])

    engine = PartialMatchRuleEngine()
    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == PartialMatchStatus.PARTIAL_MATCH
    assert result.matched_candidate.matched_amount == Decimal("30000.00")
    assert result.matched_candidate.invoice_outstanding_before == Decimal("60000.00")
    assert result.matched_candidate.invoice_outstanding_after == Decimal("30000.00")


def test_exact_payment_returns_no_partial_match() -> None:
    """Test that exact payment (Payment == Outstanding) is NOT classified as partial match."""
    payment = _make_payment(amount=Decimal("100000.00"))
    candidate = _make_candidate(
        invoice_number="INV-EXACT",
        total_amount=Decimal("100000.00"),
        outstanding_amount=Decimal("100000.00"),
    )
    universe = _make_filtered_universe(retained_candidates=[candidate])

    engine = PartialMatchRuleEngine()
    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == PartialMatchStatus.NO_PARTIAL_MATCH
    assert result.matched_candidate is None
    assert result.reason_code == PartialMatchReasonCode.EXACT_MATCH_DETECTED
    assert "Phase 14.5 Exact Matching" in result.reason_description


def test_overpayment_returns_no_partial_match() -> None:
    """Test that overpayment (Payment > Outstanding) is NOT classified as partial match."""
    payment = _make_payment(amount=Decimal("100000.00"))
    candidate = _make_candidate(
        invoice_number="INV-SMALL",
        total_amount=Decimal("40000.00"),
        outstanding_amount=Decimal("40000.00"),
    )
    universe = _make_filtered_universe(retained_candidates=[candidate])

    engine = PartialMatchRuleEngine()
    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == PartialMatchStatus.NO_PARTIAL_MATCH
    assert result.matched_candidate is None
    assert result.reason_code == PartialMatchReasonCode.OVERPAYMENT_DETECTED
    assert "Phase 14.7" in result.reason_description


def test_multi_invoice_sum_does_not_match_in_phase_14_6() -> None:
    """Test that multi-invoice sum (P = 50k, InvA = 20k, InvB = 30k) emits NO_PARTIAL_MATCH."""
    payment = _make_payment(amount=Decimal("50000.00"))
    candidate_a = _make_candidate(
        invoice_number="INV-A",
        outstanding_amount=Decimal("20000.00"),
    )
    candidate_b = _make_candidate(
        invoice_number="INV-B",
        outstanding_amount=Decimal("30000.00"),
    )
    universe = _make_filtered_universe(retained_candidates=[candidate_a, candidate_b])

    engine = PartialMatchRuleEngine()
    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == PartialMatchStatus.NO_PARTIAL_MATCH
    assert result.matched_candidate is None
    assert result.reason_code == PartialMatchReasonCode.OVERPAYMENT_DETECTED


def test_ambiguous_partial_match_multiple_candidates() -> None:
    """Test that multiple candidates capable of accepting partial payment yield AMBIGUOUS_PARTIAL_MATCH."""
    payment = _make_payment(amount=Decimal("40000.00"))
    candidate_a = _make_candidate(
        invoice_number="INV-A",
        outstanding_amount=Decimal("100000.00"),
    )
    candidate_b = _make_candidate(
        invoice_number="INV-B",
        outstanding_amount=Decimal("80000.00"),
    )
    universe = _make_filtered_universe(retained_candidates=[candidate_a, candidate_b])

    engine = PartialMatchRuleEngine()
    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == PartialMatchStatus.AMBIGUOUS_PARTIAL_MATCH
    assert result.matched_candidate is None
    assert len(result.competing_candidates) == 2
    assert result.total_partial_candidates_found == 2
    assert result.reason_code == PartialMatchReasonCode.MULTIPLE_PARTIAL_CANDIDATES


def test_multiple_partial_candidates_remain_ambiguous_even_with_reference_match() -> None:
    """Test that multiple partial candidates strictly remain ambiguous even if one has a reference match.

    Autonomous tie-breaking is prohibited in Phase 14.6 under business rules.
    """
    payment = _make_payment(
        amount=Decimal("40000.00"),
        narration="PARTIAL PAYMENT FOR INV-A",
    )
    candidate_a = _make_candidate(
        invoice_number="INV-A",
        outstanding_amount=Decimal("100000.00"),
        is_reference_match=True,
    )
    candidate_b = _make_candidate(
        invoice_number="INV-B",
        outstanding_amount=Decimal("80000.00"),
        is_reference_match=False,
    )
    universe = _make_filtered_universe(retained_candidates=[candidate_a, candidate_b])

    engine = PartialMatchRuleEngine()
    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == PartialMatchStatus.AMBIGUOUS_PARTIAL_MATCH
    assert result.matched_candidate is None
    assert len(result.competing_candidates) == 2
    # Check deterministic ordering: reference match candidate appears first in competing_candidates
    assert result.competing_candidates[0].invoice_number == "INV-A"
    assert result.competing_candidates[1].invoice_number == "INV-B"


def test_truncated_universe_ambiguity_when_truncated_candidate_is_partial() -> None:
    """Test truncation collision detection: if an excluded truncated candidate is also partial-compatible."""
    payment = _make_payment(amount=Decimal("40000.00"))
    candidate_retained = _make_candidate(
        invoice_number="INV-RETAINED",
        outstanding_amount=Decimal("100000.00"),
    )
    candidate_excluded = ExcludedCandidateInvoice(
        invoice_id=uuid.uuid4(),
        invoice_number="INV-TRUNCATED",
        outstanding_amount=Decimal("80000.00"),
        currency="INR",
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 31),
        exclusion_reasons=[FilterExclusionReason.TRUNCATED_BY_LIMIT],
        diagnostic_details="Truncated by limit",
    )
    universe = _make_filtered_universe(
        retained_candidates=[candidate_retained],
        excluded_candidates=[candidate_excluded],
        is_truncated=True,
    )

    engine = PartialMatchRuleEngine()
    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == PartialMatchStatus.AMBIGUOUS_PARTIAL_MATCH
    assert result.reason_code == PartialMatchReasonCode.TRUNCATED_UNIVERSE_AMBIGUITY
    assert result.total_partial_candidates_found == 2


def test_truncated_universe_single_match_without_reference_yields_ambiguity() -> None:
    """Test that a single partial match in a truncated universe without reference match is flagged ambiguous."""
    payment = _make_payment(amount=Decimal("40000.00"))
    candidate_retained = _make_candidate(
        invoice_number="INV-RETAINED",
        outstanding_amount=Decimal("100000.00"),
        is_reference_match=False,
    )
    candidate_excluded = ExcludedCandidateInvoice(
        invoice_id=uuid.uuid4(),
        invoice_number="INV-OLD",
        outstanding_amount=Decimal("20000.00"),  # Less than payment (not partial)
        currency="INR",
        issue_date=date(2025, 1, 1),
        due_date=date(2025, 1, 31),
        exclusion_reasons=[FilterExclusionReason.TRUNCATED_BY_LIMIT],
        diagnostic_details="Truncated",
    )
    universe = _make_filtered_universe(
        retained_candidates=[candidate_retained],
        excluded_candidates=[candidate_excluded],
        is_truncated=True,
    )

    engine = PartialMatchRuleEngine()
    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == PartialMatchStatus.AMBIGUOUS_PARTIAL_MATCH
    assert result.reason_code == PartialMatchReasonCode.TRUNCATED_UNIVERSE_AMBIGUITY


def test_truncated_universe_single_match_with_reference_yields_partial_match() -> None:
    """Test that explicit reference match bypasses truncated universe ambiguity."""
    payment = _make_payment(
        amount=Decimal("40000.00"),
        narration="INV-RETAINED PARTIAL",
    )
    candidate_retained = _make_candidate(
        invoice_number="INV-RETAINED",
        outstanding_amount=Decimal("100000.00"),
        is_reference_match=True,
    )
    candidate_excluded = ExcludedCandidateInvoice(
        invoice_id=uuid.uuid4(),
        invoice_number="INV-OLD",
        outstanding_amount=Decimal("20000.00"),
        currency="INR",
        issue_date=date(2025, 1, 1),
        due_date=date(2025, 1, 31),
        exclusion_reasons=[FilterExclusionReason.TRUNCATED_BY_LIMIT],
        diagnostic_details="Truncated",
    )
    universe = _make_filtered_universe(
        retained_candidates=[candidate_retained],
        excluded_candidates=[candidate_excluded],
        is_truncated=True,
    )

    engine = PartialMatchRuleEngine()
    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == PartialMatchStatus.PARTIAL_MATCH
    assert result.reason_code == PartialMatchReasonCode.PARTIAL_AMOUNT_WITH_REFERENCE_MATCH


def test_currency_mismatch_returns_no_partial_match() -> None:
    """Test that currency mismatch between payment (INR) and candidate (USD) rejects match."""
    payment = _make_payment(amount=Decimal("4000.00"), currency="INR")
    candidate = _make_candidate(
        outstanding_amount=Decimal("10000.00"),
        currency="USD",
    )
    universe = _make_filtered_universe(retained_candidates=[candidate])

    engine = PartialMatchRuleEngine()
    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == PartialMatchStatus.NO_PARTIAL_MATCH
    assert result.matched_candidate is None


def test_zero_or_negative_payment_effective_amount() -> None:
    """Test that zero or negative payment effective amounts are safely rejected."""
    engine = PartialMatchRuleEngine()

    for invalid_amt in [Decimal("0.00"), Decimal("-500.00")]:
        payment = _make_payment(amount=invalid_amt)
        candidate = _make_candidate(outstanding_amount=Decimal("10000.00"))
        universe = _make_filtered_universe(retained_candidates=[candidate])

        result = engine.evaluate(payment=payment, universe=universe)
        assert result.status == PartialMatchStatus.NO_PARTIAL_MATCH
        assert result.reason_code == PartialMatchReasonCode.PAYMENT_INELIGIBLE


def test_upstream_status_codes_handling() -> None:
    """Test handling of upstream universe status codes."""
    engine = PartialMatchRuleEngine()
    payment = _make_payment(amount=Decimal("4000.00"))

    # 1. CUSTOMER_UNRESOLVED
    u1 = _make_filtered_universe(retained_candidates=[], status_code="CUSTOMER_UNRESOLVED")
    r1 = engine.evaluate(payment=payment, universe=u1)
    assert r1.status == PartialMatchStatus.NO_PARTIAL_MATCH
    assert r1.reason_code == PartialMatchReasonCode.CUSTOMER_UNRESOLVED

    # 2. NOT_ELIGIBLE
    u2 = _make_filtered_universe(retained_candidates=[], status_code="NOT_ELIGIBLE")
    r2 = engine.evaluate(payment=payment, universe=u2)
    assert r2.status == PartialMatchStatus.NO_PARTIAL_MATCH
    assert r2.reason_code == PartialMatchReasonCode.PAYMENT_INELIGIBLE

    # 3. CURRENCY_MISMATCH
    u3 = _make_filtered_universe(retained_candidates=[], status_code="CURRENCY_MISMATCH")
    r3 = engine.evaluate(payment=payment, universe=u3)
    assert r3.status == PartialMatchStatus.NO_PARTIAL_MATCH
    assert r3.reason_code == PartialMatchReasonCode.CURRENCY_MISMATCH


def test_strict_micro_penny_precision() -> None:
    """Test exact micro-penny arithmetic: 100.00 outstanding vs 99.99 payment -> applied 99.99, remaining 0.01."""
    payment = _make_payment(amount=Decimal("99.99"))
    candidate = _make_candidate(
        invoice_number="INV-PENNY",
        outstanding_amount=Decimal("100.00"),
    )
    universe = _make_filtered_universe(retained_candidates=[candidate])

    engine = PartialMatchRuleEngine()
    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == PartialMatchStatus.PARTIAL_MATCH
    assert result.matched_candidate.matched_amount == Decimal("99.99")
    assert result.matched_candidate.invoice_outstanding_after == Decimal("0.01")


def test_100_run_permutation_invariance_and_determinism() -> None:
    """Test that candidate ordering in universe has zero impact on output across 100 permutations."""
    payment = _make_payment(amount=Decimal("3000.00"))
    candidates = [
        _make_candidate(
            invoice_number=f"INV-{i:03d}",
            outstanding_amount=Decimal(str(10000 + i * 1000)),
            due_date=date(2026, 8, 1 + (i % 25)),
            is_reference_match=(i == 3),
        )
        for i in range(10)
    ]

    engine = PartialMatchRuleEngine()

    base_universe = _make_filtered_universe(retained_candidates=list(candidates))
    base_result = engine.evaluate(payment=payment, universe=base_universe)

    for _ in range(100):
        shuffled = list(candidates)
        random.shuffle(shuffled)
        shuffled_universe = _make_filtered_universe(retained_candidates=shuffled)
        res = engine.evaluate(payment=payment, universe=shuffled_universe)

        assert res.status == base_result.status
        assert res.total_partial_candidates_found == base_result.total_partial_candidates_found
        assert res.reason_code == base_result.reason_code
        assert [c.invoice_number for c in res.competing_candidates] == [
            c.invoice_number for c in base_result.competing_candidates
        ]
