"""Unit tests for Phase 14.7 Multi-Invoice Matching (1:N) deterministic domain rules.

Verifies:
- Criteria and Hypothesis invariant validations
- Exact 2-invoice sum match (Inv A + Inv B == Payment)
- Exact 3-invoice and 4-invoice sum matches
- Bounded combination size (k <= max_combination_size, default 4)
- 1-invoice exact match rejected / diagnosed as Phase 14.5
- 1-invoice partial match rejected / diagnosed as Phase 14.6
- Underpayment / no combination sums to payment
- Ambiguity preservation when multiple combinations equal payment amount
- Truncated universe handling (TRUNCATED_UNIVERSE_AMBIGUITY)
- Currency compatibility & Zero FX policy
- 100-run permutation determinism
- Strict Decimal precision (micro-penny boundary 49.995 + 50.005 == 100.000)
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
from app.modules.reconciliation.domain.multi_invoice_matching import (
    MultiInvoiceMatchCriteria,
    MultiInvoiceMatchEvidenceType,
    MultiInvoiceMatchHypothesis,
    MultiInvoiceMatchReasonCode,
    MultiInvoiceMatchResult,
    MultiInvoiceMatchRuleEngine,
    MultiInvoiceMatchStatus,
)
from app.modules.reconciliation.domain.rules import PaymentIntakeContext
from app.shared.exceptions import DomainError, FinancialInvariantError


def _make_payment(
    amount: Decimal = Decimal("50000.00"),
    currency: str = "INR",
    payment_date: date = date(2026, 8, 15),
    narration: str = "MULTI SETTLEMENT",
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
    outstanding_amount: Decimal = Decimal("20000.00"),
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


def _make_universe(
    payment: PaymentIntakeContext,
    retained_candidates: List[CandidateInvoice],
    excluded_candidates: Optional[List[ExcludedCandidateInvoice]] = None,
    customer_id: Optional[uuid.UUID] = None,
    status_code: str = "FILTERED_SUCCESS",
    exclusion_breakdown: Optional[dict] = None,
) -> FilteredCandidateUniverse:
    """Helper to instantiate valid FilteredCandidateUniverse."""
    return FilteredCandidateUniverse(
        payment_id=payment.payment_id,
        company_id=payment.company_id,
        customer_id=customer_id or uuid.uuid4(),
        retained_candidates=retained_candidates,
        excluded_candidates=excluded_candidates or [],
        filter_criteria=CandidateFilterCriteria(),
        total_evaluated=len(retained_candidates) + len(excluded_candidates or []),
        total_retained=len(retained_candidates),
        total_excluded=len(excluded_candidates or []),
        currency_mismatches_detected=0,
        exclusion_breakdown=exclusion_breakdown or {},
        status_code=status_code,
    )


# -----------------------------------------------------------------------------
# 1. Criteria and Hypothesis Invariant Tests
# -----------------------------------------------------------------------------

def test_criteria_validation_invariants():
    """Verify criteria validates max_combination_size, tolerance, and proximity."""
    # Valid default criteria
    crit = MultiInvoiceMatchCriteria()
    assert crit.max_combination_size == 4
    assert crit.amount_tolerance == Decimal("0.00")
    assert crit.require_exact_currency is True
    assert crit.date_proximity_days == 30

    # max_combination_size < 2 raises DomainError
    with pytest.raises(DomainError, match="max_combination_size must be at least 2"):
        MultiInvoiceMatchCriteria(max_combination_size=1)

    # max_combination_size > 10 raises DomainError
    with pytest.raises(DomainError, match="max_combination_size cannot exceed 10"):
        MultiInvoiceMatchCriteria(max_combination_size=11)

    # negative amount tolerance raises DomainError
    with pytest.raises(DomainError, match="amount_tolerance must be non-negative"):
        MultiInvoiceMatchCriteria(amount_tolerance=Decimal("-1.00"))

    # negative proximity days raises DomainError
    with pytest.raises(DomainError, match="date_proximity_days must be non-negative"):
        MultiInvoiceMatchCriteria(date_proximity_days=-5)


def test_hypothesis_validation_invariants():
    """Verify hypothesis enforces strict financial and multi-invoice invariants."""
    inv_id1, inv_id2 = uuid.uuid4(), uuid.uuid4()

    # Valid 2-invoice hypothesis
    hyp = MultiInvoiceMatchHypothesis(
        invoice_ids=[inv_id1, inv_id2],
        invoice_numbers=["INV-001", "INV-002"],
        matched_amount=Decimal("50000.00"),
        invoices_outstanding_before=[Decimal("20000.00"), Decimal("30000.00")],
        invoices_outstanding_after=[Decimal("0.00"), Decimal("0.00")],
        payment_unallocated_before=Decimal("50000.00"),
        payment_unallocated_after=Decimal("0.00"),
        currency="INR",
        combination_size=2,
    )
    assert hyp.matched_amount == Decimal("50000.00")
    assert len(hyp.invoice_ids) == 2

    # Reject fewer than 2 invoices
    with pytest.raises(FinancialInvariantError, match="at least 2 invoices"):
        MultiInvoiceMatchHypothesis(
            invoice_ids=[inv_id1],
            invoice_numbers=["INV-001"],
            matched_amount=Decimal("50000.00"),
            invoices_outstanding_before=[Decimal("50000.00")],
            invoices_outstanding_after=[Decimal("0.00")],
            payment_unallocated_before=Decimal("50000.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
        )

    # Reject duplicate invoice IDs
    with pytest.raises(FinancialInvariantError, match="Duplicate invoice IDs"):
        MultiInvoiceMatchHypothesis(
            invoice_ids=[inv_id1, inv_id1],
            invoice_numbers=["INV-001", "INV-001"],
            matched_amount=Decimal("50000.00"),
            invoices_outstanding_before=[Decimal("25000.00"), Decimal("25000.00")],
            invoices_outstanding_after=[Decimal("0.00"), Decimal("0.00")],
            payment_unallocated_before=Decimal("50000.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
        )

    # Reject sum(invoices_before) != matched_amount
    with pytest.raises(FinancialInvariantError, match="Sum of candidate outstanding balances"):
        MultiInvoiceMatchHypothesis(
            invoice_ids=[inv_id1, inv_id2],
            invoice_numbers=["INV-001", "INV-002"],
            matched_amount=Decimal("50000.00"),
            invoices_outstanding_before=[Decimal("20000.00"), Decimal("20000.00")],  # Sum is 40,000
            invoices_outstanding_after=[Decimal("0.00"), Decimal("0.00")],
            payment_unallocated_before=Decimal("50000.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
        )

    # Reject non-zero invoice_outstanding_after
    with pytest.raises(FinancialInvariantError, match="must be 0.00"):
        MultiInvoiceMatchHypothesis(
            invoice_ids=[inv_id1, inv_id2],
            invoice_numbers=["INV-001", "INV-002"],
            matched_amount=Decimal("50000.00"),
            invoices_outstanding_before=[Decimal("20000.00"), Decimal("30000.00")],
            invoices_outstanding_after=[Decimal("1000.00"), Decimal("0.00")],
            payment_unallocated_before=Decimal("50000.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
        )

    # Reject non-zero payment_unallocated_after
    with pytest.raises(FinancialInvariantError, match="payment unallocated after.*must be 0.00"):
        MultiInvoiceMatchHypothesis(
            invoice_ids=[inv_id1, inv_id2],
            invoice_numbers=["INV-001", "INV-002"],
            matched_amount=Decimal("50000.00"),
            invoices_outstanding_before=[Decimal("20000.00"), Decimal("30000.00")],
            invoices_outstanding_after=[Decimal("0.00"), Decimal("0.00")],
            payment_unallocated_before=Decimal("50000.00"),
            payment_unallocated_after=Decimal("5000.00"),
            currency="INR",
        )


# -----------------------------------------------------------------------------
# 2. Multi-Invoice Matching Happy Path Tests (2, 3, 4 Invoices)
# -----------------------------------------------------------------------------

def test_exact_two_invoice_match():
    """Verify payment matches exactly 2 invoices summing to payment amount."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("50000.00"))

    cand1 = _make_candidate(invoice_number="INV-001", outstanding_amount=Decimal("20000.00"))
    cand2 = _make_candidate(invoice_number="INV-002", outstanding_amount=Decimal("30000.00"))
    cand3 = _make_candidate(invoice_number="INV-003", outstanding_amount=Decimal("15000.00"))

    universe = _make_universe(payment, [cand1, cand2, cand3])
    result = engine.evaluate(payment, universe)

    assert result.status == MultiInvoiceMatchStatus.MULTI_INVOICE_MATCH
    assert result.reason_code == MultiInvoiceMatchReasonCode.MULTI_INVOICE_MATCH_FOUND
    assert result.total_combinations_found == 1
    assert result.matched_combination is not None
    assert set(result.matched_combination.invoice_numbers) == {"INV-001", "INV-002"}
    assert result.matched_combination.matched_amount == Decimal("50000.00")
    assert result.matched_combination.combination_size == 2
    assert result.is_deterministic is True


def test_exact_three_invoice_match():
    """Verify payment matches exactly 3 invoices summing to payment amount."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("60000.00"))

    cand1 = _make_candidate(invoice_number="INV-001", outstanding_amount=Decimal("10000.00"))
    cand2 = _make_candidate(invoice_number="INV-002", outstanding_amount=Decimal("20000.00"))
    cand3 = _make_candidate(invoice_number="INV-003", outstanding_amount=Decimal("30000.00"))
    cand4 = _make_candidate(invoice_number="INV-004", outstanding_amount=Decimal("25000.00"))

    universe = _make_universe(payment, [cand1, cand2, cand3, cand4])
    result = engine.evaluate(payment, universe)

    assert result.status == MultiInvoiceMatchStatus.MULTI_INVOICE_MATCH
    assert result.total_combinations_found == 1
    assert result.matched_combination is not None
    assert set(result.matched_combination.invoice_numbers) == {"INV-001", "INV-002", "INV-003"}
    assert result.matched_combination.matched_amount == Decimal("60000.00")
    assert result.matched_combination.combination_size == 3


def test_exact_four_invoice_match():
    """Verify payment matches exactly 4 invoices summing to payment amount."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("40000.00"))

    cand1 = _make_candidate(invoice_number="INV-001", outstanding_amount=Decimal("10000.00"))
    cand2 = _make_candidate(invoice_number="INV-002", outstanding_amount=Decimal("10000.00"))
    cand3 = _make_candidate(invoice_number="INV-003", outstanding_amount=Decimal("10000.00"))
    cand4 = _make_candidate(invoice_number="INV-004", outstanding_amount=Decimal("10000.00"))

    universe = _make_universe(payment, [cand1, cand2, cand3, cand4])
    result = engine.evaluate(payment, universe)

    assert result.status == MultiInvoiceMatchStatus.MULTI_INVOICE_MATCH
    assert result.matched_combination is not None
    assert result.matched_combination.combination_size == 4
    assert result.matched_combination.matched_amount == Decimal("40000.00")


def test_five_invoice_combination_exceeds_default_limit():
    """Verify that a 5-invoice combination is not evaluated with default max_combination_size=4."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("50000.00"))

    # 5 invoices of 10,000 each
    candidates = [
        _make_candidate(invoice_number=f"INV-00{i}", outstanding_amount=Decimal("10000.00"))
        for i in range(1, 6)
    ]
    universe = _make_universe(payment, candidates)

    # Default max_combination_size = 4
    result = engine.evaluate(payment, universe)
    assert result.status == MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH
    assert result.reason_code == MultiInvoiceMatchReasonCode.NO_MULTI_INVOICE_COMBINATION_FOUND

    # With max_combination_size = 5, it should match
    crit = MultiInvoiceMatchCriteria(max_combination_size=5)
    result_5 = engine.evaluate(payment, universe, criteria=crit)
    assert result_5.status == MultiInvoiceMatchStatus.MULTI_INVOICE_MATCH
    assert result_5.matched_combination is not None
    assert result_5.matched_combination.combination_size == 5


def test_reference_match_evidence_signal():
    """Verify evidence signal and reason code when payment narration references invoice numbers."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(
        amount=Decimal("50000.00"),
        narration="SETTLEMENT FOR INV-001 AND INV-002",
    )

    cand1 = _make_candidate(
        invoice_number="INV-001",
        outstanding_amount=Decimal("20000.00"),
        is_reference_match=True,
    )
    cand2 = _make_candidate(
        invoice_number="INV-002",
        outstanding_amount=Decimal("30000.00"),
        is_reference_match=True,
    )

    universe = _make_universe(payment, [cand1, cand2])
    result = engine.evaluate(payment, universe)

    assert result.status == MultiInvoiceMatchStatus.MULTI_INVOICE_MATCH
    assert result.reason_code == MultiInvoiceMatchReasonCode.MULTI_INVOICE_WITH_REFERENCE_MATCH
    assert result.matched_combination.has_reference_match is True
    assert result.matched_combination.matched_reference_count == 2
    signal_types = [s.evidence_type for s in result.matched_combination.evidence_signals]
    assert MultiInvoiceMatchEvidenceType.INVOICE_NUMBER_MATCH in signal_types
    assert MultiInvoiceMatchEvidenceType.MULTI_INVOICE_SUM_EXACT in signal_types


# -----------------------------------------------------------------------------
# 3. Single Invoice & Partial Match Exclusion Tests
# -----------------------------------------------------------------------------

def test_single_exact_match_candidate_diagnosed():
    """Verify that a single invoice matching payment amount exactly is rejected as multi-invoice."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("50000.00"))

    # Only 1 candidate equals 50,000; cannot form multi-invoice match
    cand1 = _make_candidate(invoice_number="INV-001", outstanding_amount=Decimal("50000.00"))
    cand2 = _make_candidate(invoice_number="INV-002", outstanding_amount=Decimal("80000.00"))

    universe = _make_universe(payment, [cand1, cand2])
    result = engine.evaluate(payment, universe)

    assert result.status == MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH
    assert result.reason_code == MultiInvoiceMatchReasonCode.EXACT_MATCH_DETECTED


def test_single_partial_match_candidate_diagnosed():
    """Verify that payment amount less than all candidates is rejected as multi-invoice."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("20000.00"))

    cand1 = _make_candidate(invoice_number="INV-001", outstanding_amount=Decimal("50000.00"))
    cand2 = _make_candidate(invoice_number="INV-002", outstanding_amount=Decimal("80000.00"))

    universe = _make_universe(payment, [cand1, cand2])
    result = engine.evaluate(payment, universe)

    assert result.status == MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH
    assert result.reason_code == MultiInvoiceMatchReasonCode.PARTIAL_MATCH_DETECTED


def test_underpayment_diagnosed_when_all_eligible_sum_too_small():
    """Verify underpayment diagnosed when sum of all candidates < payment amount."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("100000.00"))

    cand1 = _make_candidate(invoice_number="INV-001", outstanding_amount=Decimal("20000.00"))
    cand2 = _make_candidate(invoice_number="INV-002", outstanding_amount=Decimal("30000.00"))

    universe = _make_universe(payment, [cand1, cand2])
    result = engine.evaluate(payment, universe)

    assert result.status == MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH
    assert result.reason_code == MultiInvoiceMatchReasonCode.UNDERPAYMENT_DETECTED


# -----------------------------------------------------------------------------
# 4. Ambiguity Preservation Tests (Rule 19: No Autonomous Guessing)
# -----------------------------------------------------------------------------

def test_ambiguity_preserved_when_multiple_disjoint_combinations_match():
    """Verify AMBIGUOUS_MULTI_INVOICE_MATCH when 2 distinct disjoint subsets sum to payment amount."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("50000.00"))

    # Combo 1: INV-001 (20k) + INV-002 (30k) = 50k
    cand1 = _make_candidate(invoice_number="INV-001", outstanding_amount=Decimal("20000.00"))
    cand2 = _make_candidate(invoice_number="INV-002", outstanding_amount=Decimal("30000.00"))

    # Combo 2: INV-003 (25k) + INV-004 (25k) = 50k
    cand3 = _make_candidate(invoice_number="INV-003", outstanding_amount=Decimal("25000.00"))
    cand4 = _make_candidate(invoice_number="INV-004", outstanding_amount=Decimal("25000.00"))

    universe = _make_universe(payment, [cand1, cand2, cand3, cand4])
    result = engine.evaluate(payment, universe)

    assert result.status == MultiInvoiceMatchStatus.AMBIGUOUS_MULTI_INVOICE_MATCH
    assert result.reason_code == MultiInvoiceMatchReasonCode.MULTIPLE_MULTI_INVOICE_MATCHES
    assert result.total_combinations_found == 2
    assert result.matched_combination is None  # Strictly no autonomous choice
    assert len(result.competing_combinations) == 2


def test_ambiguity_preserved_across_different_combination_sizes():
    """Verify ambiguity preserved when a 2-invoice combo and a 3-invoice combo both match."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("100000.00"))

    # Combo 1 (size 2): 50k + 50k = 100k
    cand1 = _make_candidate(invoice_number="INV-001", outstanding_amount=Decimal("50000.00"))
    cand2 = _make_candidate(invoice_number="INV-002", outstanding_amount=Decimal("50000.00"))

    # Combo 2 (size 3): 30k + 30k + 40k = 100k
    cand3 = _make_candidate(invoice_number="INV-003", outstanding_amount=Decimal("30000.00"))
    cand4 = _make_candidate(invoice_number="INV-004", outstanding_amount=Decimal("30000.00"))
    cand5 = _make_candidate(invoice_number="INV-005", outstanding_amount=Decimal("40000.00"))

    universe = _make_universe(payment, [cand1, cand2, cand3, cand4, cand5])
    result = engine.evaluate(payment, universe)

    assert result.status == MultiInvoiceMatchStatus.AMBIGUOUS_MULTI_INVOICE_MATCH
    assert result.total_combinations_found == 2
    assert result.matched_combination is None
    sizes = {c.combination_size for c in result.competing_combinations}
    assert sizes == {2, 3}


# -----------------------------------------------------------------------------
# 5. Truncated Universe & Incomplete Data Safety Tests
# -----------------------------------------------------------------------------

def test_truncated_universe_ambiguity():
    """Verify TRUNCATED_UNIVERSE_AMBIGUITY when universe was truncated and match lacks full reference."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("50000.00"))

    cand1 = _make_candidate(invoice_number="INV-001", outstanding_amount=Decimal("20000.00"))
    cand2 = _make_candidate(invoice_number="INV-002", outstanding_amount=Decimal("30000.00"))

    universe = _make_universe(
        payment=payment,
        retained_candidates=[cand1, cand2],
        exclusion_breakdown={FilterExclusionReason.TRUNCATED_BY_LIMIT.value: 5},
    )
    result = engine.evaluate(payment, universe)

    assert result.status == MultiInvoiceMatchStatus.AMBIGUOUS_MULTI_INVOICE_MATCH
    assert result.reason_code == MultiInvoiceMatchReasonCode.TRUNCATED_UNIVERSE_AMBIGUITY
    assert result.is_universe_truncated is True
    assert result.matched_combination is None


def test_truncated_universe_with_full_reference_match_is_unambiguous():
    """Verify match is accepted even if universe is truncated if ALL invoices in combo are referenced."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(
        amount=Decimal("50000.00"),
        narration="INV-001 AND INV-002",
    )

    cand1 = _make_candidate(
        invoice_number="INV-001",
        outstanding_amount=Decimal("20000.00"),
        is_reference_match=True,
    )
    cand2 = _make_candidate(
        invoice_number="INV-002",
        outstanding_amount=Decimal("30000.00"),
        is_reference_match=True,
    )

    universe = _make_universe(
        payment=payment,
        retained_candidates=[cand1, cand2],
        exclusion_breakdown={FilterExclusionReason.TRUNCATED_BY_LIMIT.value: 10},
    )
    result = engine.evaluate(payment, universe)

    assert result.status == MultiInvoiceMatchStatus.MULTI_INVOICE_MATCH
    assert result.reason_code == MultiInvoiceMatchReasonCode.MULTI_INVOICE_WITH_REFERENCE_MATCH
    assert result.matched_combination is not None


# -----------------------------------------------------------------------------
# 6. Currency Compatibility & Upstream Status Tests
# -----------------------------------------------------------------------------

def test_currency_mismatch_rejected():
    """Verify invoices with mismatched currency are excluded from combinations."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("50000.00"), currency="INR")

    cand1 = _make_candidate(invoice_number="INV-001", outstanding_amount=Decimal("20000.00"), currency="USD")
    cand2 = _make_candidate(invoice_number="INV-002", outstanding_amount=Decimal("30000.00"), currency="INR")

    universe = _make_universe(payment, [cand1, cand2])
    result = engine.evaluate(payment, universe)

    assert result.status == MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH
    assert result.reason_code == MultiInvoiceMatchReasonCode.INSUFFICIENT_CANDIDATES


def test_upstream_customer_unresolved_propagated():
    """Verify upstream CUSTOMER_UNRESOLVED status returns immediately."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("50000.00"))
    universe = _make_universe(payment, [], status_code="CUSTOMER_UNRESOLVED")

    result = engine.evaluate(payment, universe)
    assert result.status == MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH
    assert result.reason_code == MultiInvoiceMatchReasonCode.CUSTOMER_UNRESOLVED


def test_upstream_currency_mismatch_propagated():
    """Verify upstream CURRENCY_MISMATCH status returns immediately."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("50000.00"))
    universe = _make_universe(payment, [], status_code="CURRENCY_MISMATCH")

    result = engine.evaluate(payment, universe)
    assert result.status == MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH
    assert result.reason_code == MultiInvoiceMatchReasonCode.CURRENCY_MISMATCH


# -----------------------------------------------------------------------------
# 7. 100-Run Permutation Determinism Test
# -----------------------------------------------------------------------------

def test_100_run_permutation_determinism():
    """Verify that shuffling candidate invoices 100 times produces bit-for-bit identical results."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("50000.00"))

    base_candidates = [
        _make_candidate(invoice_number="INV-001", outstanding_amount=Decimal("20000.00")),
        _make_candidate(invoice_number="INV-002", outstanding_amount=Decimal("30000.00")),
        _make_candidate(invoice_number="INV-003", outstanding_amount=Decimal("15000.00")),
        _make_candidate(invoice_number="INV-004", outstanding_amount=Decimal("35000.00")),
    ]

    universe_base = _make_universe(payment, base_candidates)
    baseline_result = engine.evaluate(payment, universe_base)
    baseline_dict = baseline_result.to_dict()
    # Exclude evaluated_at for comparison
    del baseline_dict["evaluated_at"]

    for _ in range(100):
        shuffled = list(base_candidates)
        random.shuffle(shuffled)
        universe_shuffled = _make_universe(
            payment, shuffled, customer_id=universe_base.customer_id
        )
        run_result = engine.evaluate(payment, universe_shuffled)
        run_dict = run_result.to_dict()
        del run_dict["evaluated_at"]

        assert run_dict == baseline_dict


# -----------------------------------------------------------------------------
# 8. Micro-Penny Decimal Precision Test
# -----------------------------------------------------------------------------

def test_micro_penny_precision():
    """Verify that Decimal arithmetic avoids binary floating point drift."""
    engine = MultiInvoiceMatchRuleEngine()
    payment = _make_payment(amount=Decimal("100.000"))

    cand1 = _make_candidate(invoice_number="INV-001", outstanding_amount=Decimal("49.995"))
    cand2 = _make_candidate(invoice_number="INV-002", outstanding_amount=Decimal("50.005"))

    universe = _make_universe(payment, [cand1, cand2])
    result = engine.evaluate(payment, universe)

    assert result.status == MultiInvoiceMatchStatus.MULTI_INVOICE_MATCH
    assert result.matched_combination is not None
    assert result.matched_combination.matched_amount == Decimal("100.000")
