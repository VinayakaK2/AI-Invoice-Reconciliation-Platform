"""Unit tests for Phase 14.5 Exact 1:1 Matching deterministic domain rules.

Verifies:
- Criteria and Hypothesis invariant validations
- Single unambiguous exact 1:1 amount matching
- Compares against authoritative outstanding amount (never original total if paid > 0)
- Partial payment (E < O) and overpayment (E > O) non-matches
- Multi-invoice sum non-matches (deferred to Phase 14.7)
- Ambiguity preservation when multiple candidates share exact amount
- Explicit reference disambiguation
- Incomplete / truncated universe handling
- Currency compatibility & Zero FX policy
- 100-run permutation determinism
- Strict Decimal precision (micro-penny boundary)
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
from app.modules.reconciliation.domain.exact_matching import (
    ExactMatchCriteria,
    ExactMatchEvidenceType,
    ExactMatchHypothesis,
    ExactMatchReasonCode,
    ExactMatchResult,
    ExactMatchRuleEngine,
    ExactMatchStatus,
)
from app.modules.reconciliation.domain.rules import PaymentIntakeContext
from app.shared.exceptions import DomainError, FinancialInvariantError


def _make_payment(
    amount: Decimal = Decimal("5000.00"),
    currency: str = "INR",
    payment_date: date = date(2026, 8, 15),
    narration: str = "NEFT PAYMENT",
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
    total_amount: Decimal = Decimal("5000.00"),
    paid_amount: Decimal = Decimal("0.00"),
    outstanding_amount: Decimal = Decimal("5000.00"),
    currency: str = "INR",
    issue_date: date = date(2026, 8, 1),
    due_date: date = date(2026, 8, 31),
    status: str = "PENDING",
    retrieval_priority: float = 50.0,
    is_exact_amount_match: bool = True,
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


def test_exact_match_criteria_validation() -> None:
    """Test invariant validation on ExactMatchCriteria."""
    with pytest.raises(DomainError, match="amount_tolerance must be non-negative"):
        ExactMatchCriteria(amount_tolerance=Decimal("-0.01"))

    with pytest.raises(DomainError, match="date_proximity_days must be non-negative"):
        ExactMatchCriteria(date_proximity_days=-5)


def test_exact_match_hypothesis_invariants() -> None:
    """Test strict financial invariants on ExactMatchHypothesis."""
    inv_id = uuid.uuid4()

    # Matched amount must be strictly positive
    with pytest.raises(FinancialInvariantError, match="Matched amount must be strictly positive"):
        ExactMatchHypothesis(
            invoice_id=inv_id,
            invoice_number="INV-001",
            matched_amount=Decimal("0.00"),
            invoice_outstanding_before=Decimal("0.00"),
            invoice_outstanding_after=Decimal("0.00"),
            payment_unallocated_before=Decimal("0.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
        )

    # matched_amount must equal invoice_outstanding_before
    with pytest.raises(FinancialInvariantError, match="requires matched_amount"):
        ExactMatchHypothesis(
            invoice_id=inv_id,
            invoice_number="INV-001",
            matched_amount=Decimal("5000.00"),
            invoice_outstanding_before=Decimal("6000.00"),
            invoice_outstanding_after=Decimal("0.00"),
            payment_unallocated_before=Decimal("5000.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
        )

    # invoice_outstanding_after must be 0.00
    with pytest.raises(FinancialInvariantError, match="Projected invoice outstanding after exact match must be 0.00"):
        ExactMatchHypothesis(
            invoice_id=inv_id,
            invoice_number="INV-001",
            matched_amount=Decimal("5000.00"),
            invoice_outstanding_before=Decimal("5000.00"),
            invoice_outstanding_after=Decimal("1000.00"),
            payment_unallocated_before=Decimal("5000.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
        )


def test_single_exact_amount_match_success() -> None:
    """Test standard 1:1 exact match with matching amount and currency."""
    engine = ExactMatchRuleEngine()
    payment = _make_payment(amount=Decimal("7500.00"))
    cand = _make_candidate(
        invoice_number="INV-7500",
        total_amount=Decimal("7500.00"),
        outstanding_amount=Decimal("7500.00"),
    )
    universe = _make_filtered_universe(
        retained_candidates=[cand],
        payment_id=payment.payment_id,
        company_id=payment.company_id,
    )

    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == ExactMatchStatus.EXACT_MATCH
    assert result.reason_code == ExactMatchReasonCode.EXACT_AMOUNT_MATCH_FOUND
    assert result.total_exact_candidates_found == 1
    assert result.matched_candidate is not None
    assert result.matched_candidate.invoice_id == cand.invoice_id
    assert result.matched_candidate.matched_amount == Decimal("7500.00")
    assert result.matched_candidate.invoice_outstanding_after == Decimal("0.00")
    assert result.matched_candidate.payment_unallocated_after == Decimal("0.00")
    assert len(result.competing_candidates) == 0
    assert len(result.matched_candidate.evidence_signals) >= 1
    assert any(
        s.evidence_type == ExactMatchEvidenceType.EXACT_AMOUNT_MATCH
        for s in result.matched_candidate.evidence_signals
    )


def test_exact_match_uses_authoritative_outstanding_not_total() -> None:
    """Test that exact match compares against outstanding_amount, NOT original total_amount."""
    engine = ExactMatchRuleEngine()
    payment = _make_payment(amount=Decimal("4000.00"))

    # Invoice A: original total 10000.00, paid 6000.00, outstanding 4000.00 -> MATCH!
    cand_a = _make_candidate(
        invoice_number="INV-PARTIAL",
        total_amount=Decimal("10000.00"),
        paid_amount=Decimal("6000.00"),
        outstanding_amount=Decimal("4000.00"),
    )

    # Invoice B: original total 4000.00, paid 2500.00, outstanding 1500.00 -> NO MATCH!
    cand_b = _make_candidate(
        invoice_number="INV-MISMATCH",
        total_amount=Decimal("4000.00"),
        paid_amount=Decimal("2500.00"),
        outstanding_amount=Decimal("1500.00"),
    )

    universe = _make_filtered_universe(
        retained_candidates=[cand_a, cand_b],
        payment_id=payment.payment_id,
        company_id=payment.company_id,
    )

    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == ExactMatchStatus.EXACT_MATCH
    assert result.matched_candidate is not None
    assert result.matched_candidate.invoice_id == cand_a.invoice_id
    assert result.matched_candidate.matched_amount == Decimal("4000.00")
    assert result.total_exact_candidates_found == 1


def test_partial_payment_returns_no_exact_match() -> None:
    """Test that an underpayment (payment < outstanding) emits NO_EXACT_MATCH."""
    engine = ExactMatchRuleEngine()
    payment = _make_payment(amount=Decimal("3000.00"))
    cand = _make_candidate(
        total_amount=Decimal("10000.00"),
        outstanding_amount=Decimal("10000.00"),
    )
    universe = _make_filtered_universe(
        retained_candidates=[cand],
        payment_id=payment.payment_id,
        company_id=payment.company_id,
    )

    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == ExactMatchStatus.NO_EXACT_MATCH
    assert result.matched_candidate is None
    assert result.reason_code == ExactMatchReasonCode.PARTIAL_PAYMENT_DETECTED
    assert "potential partial payment for Phase 14.6" in result.reason_description


def test_overpayment_returns_no_exact_match() -> None:
    """Test that an overpayment (payment > outstanding) emits NO_EXACT_MATCH."""
    engine = ExactMatchRuleEngine()
    payment = _make_payment(amount=Decimal("12000.00"))
    cand = _make_candidate(
        total_amount=Decimal("10000.00"),
        outstanding_amount=Decimal("10000.00"),
    )
    universe = _make_filtered_universe(
        retained_candidates=[cand],
        payment_id=payment.payment_id,
        company_id=payment.company_id,
    )

    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == ExactMatchStatus.NO_EXACT_MATCH
    assert result.matched_candidate is None
    assert result.reason_code == ExactMatchReasonCode.OVERPAYMENT_DETECTED
    assert "potential multi-invoice or overpayment for Phase 14.7" in result.reason_description


def test_multi_invoice_sum_does_not_match_in_phase_14_5() -> None:
    """Test that a payment equal to sum of multiple invoices emits NO_EXACT_MATCH in Phase 14.5."""
    engine = ExactMatchRuleEngine()
    payment = _make_payment(amount=Decimal("5000.00"))
    cand_1 = _make_candidate(
        invoice_number="INV-A",
        total_amount=Decimal("3000.00"),
        outstanding_amount=Decimal("3000.00"),
    )
    cand_2 = _make_candidate(
        invoice_number="INV-B",
        total_amount=Decimal("2000.00"),
        outstanding_amount=Decimal("2000.00"),
    )
    # Sum is 5000.00, but 1:1 matching rejects it
    universe = _make_filtered_universe(
        retained_candidates=[cand_1, cand_2],
        payment_id=payment.payment_id,
        company_id=payment.company_id,
    )

    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == ExactMatchStatus.NO_EXACT_MATCH
    assert result.matched_candidate is None
    assert result.total_exact_candidates_found == 0


def test_ambiguous_exact_match_multiple_candidates_same_amount() -> None:
    """Test that two candidates with identical outstanding amounts emit AMBIGUOUS_EXACT_MATCH without arbitrary picking."""
    engine = ExactMatchRuleEngine()
    payment = _make_payment(amount=Decimal("5000.00"), narration="GENERAL PAYMENT")

    cand_1 = _make_candidate(
        invoice_number="INV-001",
        total_amount=Decimal("5000.00"),
        outstanding_amount=Decimal("5000.00"),
        is_reference_match=False,
    )
    cand_2 = _make_candidate(
        invoice_number="INV-002",
        total_amount=Decimal("5000.00"),
        outstanding_amount=Decimal("5000.00"),
        is_reference_match=False,
    )

    universe = _make_filtered_universe(
        retained_candidates=[cand_1, cand_2],
        payment_id=payment.payment_id,
        company_id=payment.company_id,
    )

    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == ExactMatchStatus.AMBIGUOUS_EXACT_MATCH
    assert result.matched_candidate is None
    assert result.total_exact_candidates_found == 2
    assert len(result.competing_candidates) == 2
    assert result.reason_code == ExactMatchReasonCode.MULTIPLE_EXACT_AMOUNT_MATCHES
    assert {c.invoice_number for c in result.competing_candidates} == {"INV-001", "INV-002"}


def test_multiple_exact_candidates_remain_ambiguous_even_with_reference_match() -> None:
    """Test that under business rule §2.5 line 64, multiple exact amount matches unconditionally emit AMBIGUOUS_EXACT_MATCH.

    Autonomous reference tie-breaking is prohibited. The reference match signal is preserved
    in competing_candidates for human review.
    """
    engine = ExactMatchRuleEngine()
    payment = _make_payment(
        amount=Decimal("5000.00"),
        narration="SETTLEMENT FOR INV-001 FROM CLIENT",
    )

    cand_1 = _make_candidate(
        invoice_number="INV-001",
        total_amount=Decimal("5000.00"),
        outstanding_amount=Decimal("5000.00"),
        is_reference_match=True,  # Matches narration!
    )
    cand_2 = _make_candidate(
        invoice_number="INV-002",
        total_amount=Decimal("5000.00"),
        outstanding_amount=Decimal("5000.00"),
        is_reference_match=False,
    )

    universe = _make_filtered_universe(
        retained_candidates=[cand_1, cand_2],
        payment_id=payment.payment_id,
        company_id=payment.company_id,
    )

    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == ExactMatchStatus.AMBIGUOUS_EXACT_MATCH
    assert result.matched_candidate is None
    assert result.total_exact_candidates_found == 2
    assert len(result.competing_candidates) == 2
    assert result.reason_code == ExactMatchReasonCode.MULTIPLE_EXACT_AMOUNT_MATCHES
    assert any(c.invoice_number == "INV-001" and c.is_reference_match for c in result.competing_candidates)
    assert any(c.invoice_number == "INV-002" and not c.is_reference_match for c in result.competing_candidates)


def test_duplicate_candidate_records_do_not_create_false_ambiguity() -> None:
    """Test that candidate stream deduplication (from Phase 14.4) prevents duplicate invoice instances from causing false ambiguity."""
    engine = ExactMatchRuleEngine()
    payment = _make_payment(amount=Decimal("5000.00"))
    inv_id = uuid.uuid4()

    # One unique retained candidate
    cand = _make_candidate(
        invoice_id=inv_id,
        invoice_number="INV-UNIQUE",
        total_amount=Decimal("5000.00"),
        outstanding_amount=Decimal("5000.00"),
    )
    # Duplicate instance pruned into excluded_candidates
    dup_excl = ExcludedCandidateInvoice(
        invoice_id=inv_id,
        invoice_number="INV-UNIQUE",
        outstanding_amount=Decimal("5000.00"),
        currency="INR",
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 31),
        exclusion_reasons=[FilterExclusionReason.DUPLICATE_CANDIDATE],
        diagnostic_details="Duplicate pruned",
    )

    universe = _make_filtered_universe(
        retained_candidates=[cand],
        excluded_candidates=[dup_excl],
        payment_id=payment.payment_id,
        company_id=payment.company_id,
    )

    result = engine.evaluate(payment=payment, universe=universe)

    # Must NOT be marked ambiguous!
    assert result.status == ExactMatchStatus.EXACT_MATCH
    assert result.matched_candidate is not None
    assert result.matched_candidate.invoice_id == inv_id
    assert result.total_exact_candidates_found == 1


def test_truncated_universe_ambiguity_when_truncated_candidate_matches_amount() -> None:
    """Test that if an invoice matching the payment amount was truncated to enforce max_candidates, ambiguity is raised."""
    engine = ExactMatchRuleEngine()
    payment = _make_payment(amount=Decimal("5000.00"))

    cand_retained = _make_candidate(
        invoice_number="INV-RET",
        total_amount=Decimal("5000.00"),
        outstanding_amount=Decimal("5000.00"),
    )
    cand_truncated = ExcludedCandidateInvoice(
        invoice_id=uuid.uuid4(),
        invoice_number="INV-TRUNC",
        outstanding_amount=Decimal("5000.00"),  # Same amount!
        currency="INR",
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 31),
        exclusion_reasons=[FilterExclusionReason.TRUNCATED_BY_LIMIT],
        diagnostic_details="Truncated",
    )

    universe = _make_filtered_universe(
        retained_candidates=[cand_retained],
        excluded_candidates=[cand_truncated],
        payment_id=payment.payment_id,
        company_id=payment.company_id,
        is_truncated=True,
    )

    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == ExactMatchStatus.AMBIGUOUS_EXACT_MATCH
    assert result.reason_code == ExactMatchReasonCode.TRUNCATED_UNIVERSE_AMBIGUITY
    assert result.is_universe_truncated is True


def test_truncated_universe_single_match_without_reference_yields_ambiguity() -> None:
    """Test that a single exact amount match in a truncated universe without reference match yields ambiguity due to truncation risk."""
    engine = ExactMatchRuleEngine()
    payment = _make_payment(amount=Decimal("5000.00"), narration="GENERAL MONEY TRANSFER")

    cand_retained = _make_candidate(
        invoice_number="INV-001",
        total_amount=Decimal("5000.00"),
        outstanding_amount=Decimal("5000.00"),
        is_reference_match=False,
    )

    universe = _make_filtered_universe(
        retained_candidates=[cand_retained],
        payment_id=payment.payment_id,
        company_id=payment.company_id,
        is_truncated=True,
    )

    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == ExactMatchStatus.AMBIGUOUS_EXACT_MATCH
    assert result.reason_code == ExactMatchReasonCode.TRUNCATED_UNIVERSE_AMBIGUITY
    assert result.is_universe_truncated is True


def test_truncated_universe_single_match_with_reference_yields_exact_match() -> None:
    """Test that a single match in a truncated universe WITH explicit reference match succeeds with truncation flag set."""
    engine = ExactMatchRuleEngine()
    payment = _make_payment(amount=Decimal("5000.00"), narration="SETTLEMENT FOR INV-001")

    cand_retained = _make_candidate(
        invoice_number="INV-001",
        total_amount=Decimal("5000.00"),
        outstanding_amount=Decimal("5000.00"),
        is_reference_match=True,
    )

    universe = _make_filtered_universe(
        retained_candidates=[cand_retained],
        payment_id=payment.payment_id,
        company_id=payment.company_id,
        is_truncated=True,
    )

    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == ExactMatchStatus.EXACT_MATCH
    assert result.matched_candidate is not None
    assert result.matched_candidate.invoice_number == "INV-001"
    assert result.is_universe_truncated is True
    assert result.reason_code == ExactMatchReasonCode.EXACT_AMOUNT_WITH_REFERENCE_MATCH


def test_currency_mismatch_returns_no_exact_match() -> None:
    """Test zero FX conversion policy: payment USD vs invoice INR never matches."""
    engine = ExactMatchRuleEngine()
    payment = _make_payment(amount=Decimal("5000.00"), currency="USD")
    cand = _make_candidate(
        total_amount=Decimal("5000.00"),
        outstanding_amount=Decimal("5000.00"),
        currency="INR",
    )
    universe = _make_filtered_universe(
        retained_candidates=[cand],
        payment_id=payment.payment_id,
        company_id=payment.company_id,
    )

    result = engine.evaluate(payment=payment, universe=universe)

    assert result.status == ExactMatchStatus.NO_EXACT_MATCH
    assert result.matched_candidate is None


def test_zero_or_negative_payment_effective_amount() -> None:
    """Test non-positive payment effective amounts are safely rejected."""
    engine = ExactMatchRuleEngine()
    cand = _make_candidate()
    universe = _make_filtered_universe([cand])

    payment_zero = _make_payment(amount=Decimal("0.00"))
    result_zero = engine.evaluate(payment=payment_zero, universe=universe)
    assert result_zero.status == ExactMatchStatus.NO_EXACT_MATCH
    assert result_zero.reason_code == ExactMatchReasonCode.PAYMENT_INELIGIBLE

    payment_neg = _make_payment(amount=Decimal("-500.00"))
    result_neg = engine.evaluate(payment=payment_neg, universe=universe)
    assert result_neg.status == ExactMatchStatus.NO_EXACT_MATCH
    assert result_neg.reason_code == ExactMatchReasonCode.PAYMENT_INELIGIBLE


def test_upstream_status_codes_handling() -> None:
    """Test upstream candidate universe short-circuit statuses."""
    engine = ExactMatchRuleEngine()
    payment = _make_payment()

    # Customer unresolved
    u_unresolved = _make_filtered_universe([], status_code="CUSTOMER_UNRESOLVED")
    res_unresolved = engine.evaluate(payment=payment, universe=u_unresolved)
    assert res_unresolved.status == ExactMatchStatus.NO_EXACT_MATCH
    assert res_unresolved.reason_code == ExactMatchReasonCode.CUSTOMER_UNRESOLVED

    # Payment intake ineligible
    u_ineligible = _make_filtered_universe([], status_code="NOT_ELIGIBLE")
    res_ineligible = engine.evaluate(payment=payment, universe=u_ineligible)
    assert res_ineligible.status == ExactMatchStatus.NO_EXACT_MATCH
    assert res_ineligible.reason_code == ExactMatchReasonCode.PAYMENT_INELIGIBLE

    # Currency mismatch
    u_curr = _make_filtered_universe([], status_code="CURRENCY_MISMATCH")
    res_curr = engine.evaluate(payment=payment, universe=u_curr)
    assert res_curr.status == ExactMatchStatus.NO_EXACT_MATCH
    assert res_curr.reason_code == ExactMatchReasonCode.CURRENCY_MISMATCH


def test_strict_micro_penny_precision() -> None:
    """Test that micro-penny differences (e.g. 0.01) do not match exactly."""
    engine = ExactMatchRuleEngine()
    payment = _make_payment(amount=Decimal("1000.00"))

    # Outstanding is 1 cent higher
    cand = _make_candidate(
        total_amount=Decimal("1000.01"),
        outstanding_amount=Decimal("1000.01"),
    )
    universe = _make_filtered_universe(
        retained_candidates=[cand],
        payment_id=payment.payment_id,
        company_id=payment.company_id,
    )

    result = engine.evaluate(payment=payment, universe=universe)
    assert result.status == ExactMatchStatus.NO_EXACT_MATCH
    assert result.matched_candidate is None


def test_100_run_permutation_invariance_and_determinism() -> None:
    """Test 100-run randomized permutation invariance to prove determinism."""
    engine = ExactMatchRuleEngine()
    payment = _make_payment(
        amount=Decimal("5000.00"),
        narration="PAYMENT FOR INV-MATCH",
    )

    cands = [
        _make_candidate(
            invoice_number="INV-OTHER-1",
            total_amount=Decimal("3000.00"),
            outstanding_amount=Decimal("3000.00"),
        ),
        _make_candidate(
            invoice_number="INV-MATCH",
            total_amount=Decimal("5000.00"),
            outstanding_amount=Decimal("5000.00"),
            is_reference_match=True,
        ),
        _make_candidate(
            invoice_number="INV-OTHER-2",
            total_amount=Decimal("8000.00"),
            outstanding_amount=Decimal("8000.00"),
        ),
    ]

    base_universe = _make_filtered_universe(
        retained_candidates=list(cands),
        payment_id=payment.payment_id,
        company_id=payment.company_id,
    )
    baseline_result = engine.evaluate(payment=payment, universe=base_universe)

    assert baseline_result.status == ExactMatchStatus.EXACT_MATCH
    baseline_dict = baseline_result.to_dict()
    # Exclude evaluated_at timestamp from byte-for-byte comparison
    baseline_dict.pop("evaluated_at")

    rng = random.Random(42)
    for _ in range(100):
        shuffled = list(cands)
        rng.shuffle(shuffled)
        shuffled_universe = _make_filtered_universe(
            retained_candidates=shuffled,
            payment_id=payment.payment_id,
            company_id=payment.company_id,
            customer_id=base_universe.customer_id,
        )
        res = engine.evaluate(payment=payment, universe=shuffled_universe)
        res_dict = res.to_dict()
        res_dict.pop("evaluated_at")
        assert res_dict == baseline_dict
