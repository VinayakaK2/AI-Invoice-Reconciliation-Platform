"""Unit tests for Phase 14.8 Combination Matching pure domain rule engine.

Tests:
1. Criteria and hypothesis invariant validation.
2. Unique combination matching (k=2, k=3, k=4).
3. Competing combinations evaluation:
   - Rule M-3 FIFO aging heuristic (phases.md example: 10k+25k vs 15k+8k+12k = 35k).
   - Reference priority override over FIFO aging.
   - Tied aging and reference handling (unresolvable ambiguity).
   - FIFO aging disabled criteria toggle.
4. Truncated universe ambiguity defense.
5. Non-combination diagnostic outcomes (underpayment, exact match, partial match, currency mismatch).
6. 100-run permutation determinism (bit-for-bit identical results).
7. Micro-penny exact Decimal precision (no float rounding).
"""

from datetime import date, timedelta
from decimal import Decimal
import random
from typing import Optional
import uuid
import pytest

from app.modules.reconciliation.domain.candidate_filters import (
    CandidateFilterCriteria,
    FilteredCandidateUniverse,
)
from app.modules.reconciliation.domain.candidate_invoices import CandidateInvoice
from app.modules.reconciliation.domain.combination_matching import (
    CombinationHypothesis,
    CombinationMatchCriteria,
    CombinationMatchEvidenceType,
    CombinationMatchReasonCode,
    CombinationMatchResult,
    CombinationMatchRuleEngine,
    CombinationMatchStatus,
)
from app.modules.reconciliation.domain.entities import SignalStrength
from app.modules.reconciliation.domain.rules import PaymentIntakeContext
from app.shared.exceptions import DomainError, FinancialInvariantError


def _make_candidate(
    invoice_number: str,
    outstanding: str,
    due_date: date,
    currency: str = "INR",
    total: Optional[str] = None,
    paid: str = "0.00",
    issue_date: Optional[date] = None,
    is_ref_match: bool = False,
) -> CandidateInvoice:
    """Helper to build a valid CandidateInvoice."""
    total_dec = Decimal(total) if total else Decimal(outstanding) + Decimal(paid)
    out_dec = Decimal(outstanding)
    paid_dec = Decimal(paid)
    iss_date = issue_date or (due_date - timedelta(days=30))

    return CandidateInvoice(
        invoice_id=uuid.uuid4(),
        invoice_number=invoice_number,
        total_amount=total_dec,
        paid_amount=paid_dec,
        outstanding_amount=out_dec,
        currency=currency,
        issue_date=iss_date,
        due_date=due_date,
        status="PENDING" if paid_dec == Decimal("0.00") else "PARTIALLY_PAID",
        retrieval_priority=80.0,
        evidence_signals=[],
        is_exact_amount_match=False,
        is_partial_amount_match=False,
        is_reference_match=is_ref_match,
        rank=1,
    )


def _make_payment(
    amount: str,
    currency: str = "INR",
    narration: str = "COMBINATION TEST SETTLEMENT",
    payment_reference: str = "REF12345",
    customer_id: Optional[uuid.UUID] = None,
    payment_date: Optional[date] = None,
    company_id: Optional[uuid.UUID] = None,
) -> PaymentIntakeContext:
    """Helper to build a valid PaymentIntakeContext."""
    amt_dec = Decimal(amount)
    return PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id or uuid.uuid4(),
        amount=amt_dec,
        currency=currency,
        payment_date=payment_date or date(2026, 8, 15),
        narration=narration,
        payment_reference=payment_reference,
        bank_account_number="123456789012",
        allocated_amount=Decimal("0.00"),
        unallocated_amount=amt_dec,
    )


def _make_universe(
    candidates: list[CandidateInvoice],
    payment: Optional[PaymentIntakeContext] = None,
    customer_id: Optional[uuid.UUID] = None,
    company_id: Optional[uuid.UUID] = None,
    status_code: str = "SUCCESS",
) -> FilteredCandidateUniverse:
    """Helper to build FilteredCandidateUniverse."""
    pay_id = payment.payment_id if payment else uuid.uuid4()
    comp_id = payment.company_id if payment else (company_id or uuid.uuid4())
    return FilteredCandidateUniverse(
        payment_id=pay_id,
        company_id=comp_id,
        customer_id=customer_id or uuid.uuid4(),
        retained_candidates=candidates,
        excluded_candidates=[],
        filter_criteria=CandidateFilterCriteria(),
        total_evaluated=len(candidates),
        total_retained=len(candidates),
        total_excluded=0,
        currency_mismatches_detected=0,
        exclusion_breakdown={},
        status_code=status_code,
    )


# ==============================================================================
# Invariant Tests
# ==============================================================================


def test_criteria_validation_invariants() -> None:
    """Verify criteria validates max_combination_size, tolerance, proximity, and toggle."""
    crit = CombinationMatchCriteria()
    assert crit.max_combination_size == 4
    assert crit.amount_tolerance == Decimal("0.00")
    assert crit.require_exact_currency is True
    assert crit.date_proximity_days == 30
    assert crit.enable_fifo_aging is True

    # max_combination_size < 2 raises DomainError
    with pytest.raises(DomainError, match="max_combination_size must be at least 2"):
        CombinationMatchCriteria(max_combination_size=1)

    # max_combination_size > 10 raises DomainError
    with pytest.raises(DomainError, match="max_combination_size cannot exceed 10"):
        CombinationMatchCriteria(max_combination_size=11)

    # amount_tolerance < 0 raises DomainError
    with pytest.raises(DomainError, match="amount_tolerance must be non-negative"):
        CombinationMatchCriteria(amount_tolerance=Decimal("-0.01"))

    # date_proximity_days < 0 raises DomainError
    with pytest.raises(DomainError, match="date_proximity_days must be non-negative"):
        CombinationMatchCriteria(date_proximity_days=-1)


def test_hypothesis_validation_invariants() -> None:
    """Verify hypothesis validates list lengths, balance sum, and positive amounts."""
    id1, id2 = uuid.uuid4(), uuid.uuid4()
    d1, d2 = date(2026, 5, 1), date(2026, 5, 15)

    # Valid hypothesis
    hypo = CombinationHypothesis(
        invoice_ids=[id1, id2],
        invoice_numbers=["INV-001", "INV-002"],
        matched_amount=Decimal("50000.00"),
        invoices_outstanding_before=[Decimal("20000.00"), Decimal("30000.00")],
        invoices_outstanding_after=[Decimal("0.00"), Decimal("0.00")],
        payment_unallocated_before=Decimal("50000.00"),
        payment_unallocated_after=Decimal("0.00"),
        currency="INR",
        combination_size=2,
        oldest_due_date=d1,
        newest_due_date=d2,
        average_days_to_due=-30.0,
    )
    assert hypo.combination_size == 2
    assert hypo.matched_amount == Decimal("50000.00")

    # Less than 2 invoices
    with pytest.raises(DomainError, match="at least 2 invoices"):
        CombinationHypothesis(
            invoice_ids=[id1],
            invoice_numbers=["INV-001"],
            matched_amount=Decimal("50000.00"),
            invoices_outstanding_before=[Decimal("50000.00")],
            invoices_outstanding_after=[Decimal("0.00")],
            payment_unallocated_before=Decimal("50000.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
            combination_size=1,
            oldest_due_date=d1,
            newest_due_date=d1,
            average_days_to_due=0.0,
        )

    # Duplicate invoice IDs
    with pytest.raises(DomainError, match="Duplicate invoice IDs"):
        CombinationHypothesis(
            invoice_ids=[id1, id1],
            invoice_numbers=["INV-001", "INV-001"],
            matched_amount=Decimal("50000.00"),
            invoices_outstanding_before=[Decimal("25000.00"), Decimal("25000.00")],
            invoices_outstanding_after=[Decimal("0.00"), Decimal("0.00")],
            payment_unallocated_before=Decimal("50000.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
            combination_size=2,
            oldest_due_date=d1,
            newest_due_date=d1,
            average_days_to_due=0.0,
        )

    # Sum does not match
    with pytest.raises(FinancialInvariantError, match="does not equal matched amount"):
        CombinationHypothesis(
            invoice_ids=[id1, id2],
            invoice_numbers=["INV-001", "INV-002"],
            matched_amount=Decimal("50000.00"),
            invoices_outstanding_before=[Decimal("20000.00"), Decimal("20000.00")],  # Sum = 40k != 50k
            invoices_outstanding_after=[Decimal("0.00"), Decimal("0.00")],
            payment_unallocated_before=Decimal("50000.00"),
            payment_unallocated_after=Decimal("0.00"),
            currency="INR",
            combination_size=2,
            oldest_due_date=d1,
            newest_due_date=d2,
            average_days_to_due=-30.0,
        )


# ==============================================================================
# Unique Combination Matching Tests
# ==============================================================================


def test_unique_two_invoice_combination_match() -> None:
    """Verify payment exactly matching 2 invoices returns UNIQUE_COMBINATION_MATCH."""
    engine = CombinationMatchRuleEngine()
    pay = _make_payment("50000.00")
    inv1 = _make_candidate("INV-001", "20000.00", date(2026, 7, 1))
    inv2 = _make_candidate("INV-002", "30000.00", date(2026, 7, 15))
    inv3 = _make_candidate("INV-003", "45000.00", date(2026, 8, 1))

    universe = _make_universe([inv1, inv2, inv3], payment=pay)
    result = engine.evaluate(payment=pay, universe=universe)

    assert result.status == CombinationMatchStatus.UNIQUE_COMBINATION_MATCH
    assert result.total_combinations_found == 1
    assert result.prioritized_combination is not None
    assert result.prioritized_combination.combination_size == 2
    assert set(result.prioritized_combination.invoice_numbers) == {"INV-001", "INV-002"}
    assert result.requires_review is False
    assert result.reason_code == CombinationMatchReasonCode.UNIQUE_COMBINATION_FOUND


def test_unique_three_invoice_combination_match() -> None:
    """Verify payment matching 3 invoices returns UNIQUE_COMBINATION_MATCH."""
    engine = CombinationMatchRuleEngine()
    pay = _make_payment("60000.00")
    inv1 = _make_candidate("INV-001", "10000.00", date(2026, 7, 1))
    inv2 = _make_candidate("INV-002", "20000.00", date(2026, 7, 15))
    inv3 = _make_candidate("INV-003", "30000.00", date(2026, 8, 1))

    universe = _make_universe([inv1, inv2, inv3], payment=pay)
    result = engine.evaluate(payment=pay, universe=universe)

    assert result.status == CombinationMatchStatus.UNIQUE_COMBINATION_MATCH
    assert result.total_combinations_found == 1
    assert result.prioritized_combination is not None
    assert result.prioritized_combination.combination_size == 3


# ==============================================================================
# Rule M-3 FIFO Aging Heuristic & Competing Combinations Tests
# ==============================================================================


def test_competing_combinations_rule_m3_fifo_aging_prioritization() -> None:
    """Test phases.md §14.8 canonical example:

    Invoices:
    10k (due 2026-05-01)
    25k (due 2026-05-15)
    15k (due 2026-06-01)
    8k  (due 2026-06-10)
    12k (due 2026-06-20)

    Payment: 35k

    Subset A: 10k + 25k = 35k (oldest due: 2026-05-01)
    Subset B: 15k + 8k + 12k = 35k (oldest due: 2026-06-01)

    Rule M-3 dictates:
    Subset A settles oldest debt (May 1 vs June 1) -> Prioritized!
    Flagged as REVIEW_REQUIRED due to ambiguity.
    """
    engine = CombinationMatchRuleEngine()
    pay = _make_payment("35000.00", payment_date=date(2026, 7, 1))

    inv1 = _make_candidate("INV-10K", "10000.00", date(2026, 5, 1))
    inv2 = _make_candidate("INV-25K", "25000.00", date(2026, 5, 15))
    inv3 = _make_candidate("INV-15K", "15000.00", date(2026, 6, 1))
    inv4 = _make_candidate("INV-8K", "8000.00", date(2026, 6, 10))
    inv5 = _make_candidate("INV-12K", "12000.00", date(2026, 6, 20))

    universe = _make_universe([inv1, inv2, inv3, inv4, inv5], payment=pay)
    result = engine.evaluate(payment=pay, universe=universe)

    assert result.status == CombinationMatchStatus.PRIORITIZED_COMBINATION_MATCH
    assert result.total_combinations_found == 2
    assert result.applied_heuristic == "RULE_M3_FIFO_AGING"
    assert result.requires_review is True
    assert result.reason_code == CombinationMatchReasonCode.FIFO_AGING_PRIORITIZED

    # Prioritized combination is Subset A (INV-10K + INV-25K)
    assert result.prioritized_combination is not None
    assert set(result.prioritized_combination.invoice_numbers) == {"INV-10K", "INV-25K"}
    assert result.prioritized_combination.combination_size == 2
    assert result.prioritized_combination.is_fifo_prioritized is True

    # Evidence signal for FIFO aging exists
    fifo_signals = [
        s
        for s in result.prioritized_combination.evidence_signals
        if s.evidence_type == CombinationMatchEvidenceType.FIFO_OLDEST_INVOICE_PRIORITY
    ]
    assert len(fifo_signals) == 1
    assert "2026-05-01" in fifo_signals[0].description

    # Both competing combinations are reported in order
    assert len(result.competing_combinations) == 2
    assert result.competing_combinations[0] == result.prioritized_combination
    assert set(result.competing_combinations[1].invoice_numbers) == {"INV-15K", "INV-8K", "INV-12K"}


def test_reference_priority_overrides_fifo_aging() -> None:
    """When narration explicitly references an invoice, that combination takes precedence over aging."""
    engine = CombinationMatchRuleEngine()
    # Narration mentions INV-15K and INV-8K
    pay = _make_payment("35000.00", narration="SETTLEMENT FOR INV-15K AND INV-8K")

    inv1 = _make_candidate("INV-10K", "10000.00", date(2026, 5, 1))  # Older, but no ref
    inv2 = _make_candidate("INV-25K", "25000.00", date(2026, 5, 15))
    inv3 = _make_candidate("INV-15K", "15000.00", date(2026, 6, 1))  # Referenced!
    inv4 = _make_candidate("INV-8K", "8000.00", date(2026, 6, 10))   # Referenced!
    inv5 = _make_candidate("INV-12K", "12000.00", date(2026, 6, 20))

    universe = _make_universe([inv1, inv2, inv3, inv4, inv5], payment=pay)
    result = engine.evaluate(payment=pay, universe=universe)

    assert result.status == CombinationMatchStatus.PRIORITIZED_COMBINATION_MATCH
    assert result.applied_heuristic == "REFERENCE_MATCH_PRIORITY"
    assert result.reason_code == CombinationMatchReasonCode.REFERENCE_PRIORITIZED
    assert result.requires_review is True

    # Prioritized is Subset B because it has 2 matched references
    assert result.prioritized_combination is not None
    assert set(result.prioritized_combination.invoice_numbers) == {"INV-15K", "INV-8K", "INV-12K"}
    assert result.prioritized_combination.matched_reference_count == 2


def test_tied_aging_and_reference_preserves_unresolvable_ambiguity() -> None:
    """When competing combinations have identical due dates and references, preserve strict ambiguity."""
    engine = CombinationMatchRuleEngine()
    pay = _make_payment("50000.00")
    same_due = date(2026, 6, 1)

    # Subset A: 20k + 30k = 50k (both due June 1)
    inv1 = _make_candidate("INV-A1", "20000.00", same_due)
    inv2 = _make_candidate("INV-A2", "30000.00", same_due)

    # Subset B: 25k + 25k = 50k (both due June 1)
    inv3 = _make_candidate("INV-B1", "25000.00", same_due)
    inv4 = _make_candidate("INV-B2", "25000.00", same_due)

    universe = _make_universe([inv1, inv2, inv3, inv4], payment=pay)
    result = engine.evaluate(payment=pay, universe=universe)

    assert result.status == CombinationMatchStatus.AMBIGUOUS_COMBINATION_MATCH
    assert result.total_combinations_found == 2
    assert result.prioritized_combination is None
    assert result.requires_review is True
    assert result.reason_code == CombinationMatchReasonCode.MULTIPLE_COMBINATIONS_UNRESOLVED
    assert len(result.competing_combinations) == 2


def test_disabled_fifo_aging_preserves_ambiguity() -> None:
    """When enable_fifo_aging is False, engine does not prioritize by aging."""
    engine = CombinationMatchRuleEngine()
    pay = _make_payment("35000.00")

    inv1 = _make_candidate("INV-10K", "10000.00", date(2026, 5, 1))
    inv2 = _make_candidate("INV-25K", "25000.00", date(2026, 5, 15))
    inv3 = _make_candidate("INV-15K", "15000.00", date(2026, 6, 1))
    inv4 = _make_candidate("INV-8K", "8000.00", date(2026, 6, 10))
    inv5 = _make_candidate("INV-12K", "12000.00", date(2026, 6, 20))

    crit = CombinationMatchCriteria(enable_fifo_aging=False)
    universe = _make_universe([inv1, inv2, inv3, inv4, inv5], payment=pay)
    result = engine.evaluate(payment=pay, universe=universe, criteria=crit)

    assert result.status == CombinationMatchStatus.AMBIGUOUS_COMBINATION_MATCH
    assert result.prioritized_combination is None
    assert result.reason_code == CombinationMatchReasonCode.MULTIPLE_COMBINATIONS_UNRESOLVED


# ==============================================================================
# Truncation & Non-Combination Outcomes Tests
# ==============================================================================


def test_truncated_universe_ambiguity() -> None:
    """If candidate universe was truncated and combo lacks reference, emit TRUNCATED_UNIVERSE_AMBIGUITY."""
    engine = CombinationMatchRuleEngine()
    pay = _make_payment("50000.00")
    inv1 = _make_candidate("INV-001", "20000.00", date(2026, 7, 1))
    inv2 = _make_candidate("INV-002", "30000.00", date(2026, 7, 15))

    universe = _make_universe([inv1, inv2], payment=pay, status_code="TRUNCATED")
    result = engine.evaluate(payment=pay, universe=universe)

    assert result.status == CombinationMatchStatus.AMBIGUOUS_COMBINATION_MATCH
    assert result.reason_code == CombinationMatchReasonCode.TRUNCATED_UNIVERSE_AMBIGUITY
    assert result.is_universe_truncated is True
    assert result.requires_review is True


def test_underpayment_diagnosed_when_all_eligible_sum_too_small() -> None:
    """When all eligible candidates sum to less than payment, diagnose UNDERPAYMENT_DETECTED."""
    engine = CombinationMatchRuleEngine()
    pay = _make_payment("50000.00")
    inv1 = _make_candidate("INV-001", "10000.00", date(2026, 7, 1))
    inv2 = _make_candidate("INV-002", "15000.00", date(2026, 7, 15))

    universe = _make_universe([inv1, inv2], payment=pay)
    result = engine.evaluate(payment=pay, universe=universe)

    assert result.status == CombinationMatchStatus.NO_COMBINATION_MATCH
    assert result.reason_code == CombinationMatchReasonCode.UNDERPAYMENT_DETECTED


def test_single_exact_match_candidate_diagnosed() -> None:
    """If single candidate exactly equals payment, diagnose EXACT_MATCH_DETECTED (Phase 14.5)."""
    engine = CombinationMatchRuleEngine()
    pay = _make_payment("50000.00")
    inv1 = _make_candidate("INV-EXACT", "50000.00", date(2026, 7, 1))
    inv2 = _make_candidate("INV-OTHER", "20000.00", date(2026, 7, 1))

    universe = _make_universe([inv1, inv2], payment=pay)
    result = engine.evaluate(payment=pay, universe=universe)

    assert result.status == CombinationMatchStatus.NO_COMBINATION_MATCH
    assert result.reason_code == CombinationMatchReasonCode.EXACT_MATCH_DETECTED


def test_single_partial_match_candidate_diagnosed() -> None:
    """If all candidate balances exceed payment, diagnose PARTIAL_MATCH_DETECTED (Phase 14.6)."""
    engine = CombinationMatchRuleEngine()
    pay = _make_payment("10000.00")
    inv1 = _make_candidate("INV-LARGE-1", "50000.00", date(2026, 7, 1))
    inv2 = _make_candidate("INV-LARGE-2", "75000.00", date(2026, 7, 1))

    universe = _make_universe([inv1, inv2], payment=pay)
    result = engine.evaluate(payment=pay, universe=universe)

    assert result.status == CombinationMatchStatus.NO_COMBINATION_MATCH
    assert result.reason_code == CombinationMatchReasonCode.PARTIAL_MATCH_DETECTED


def test_currency_mismatch_rejected() -> None:
    """Currency mismatch rejects combination evaluation."""
    engine = CombinationMatchRuleEngine()
    pay = _make_payment("50000.00", currency="INR")
    inv1 = _make_candidate("INV-USD-1", "20000.00", date(2026, 7, 1), currency="USD")
    inv2 = _make_candidate("INV-USD-2", "30000.00", date(2026, 7, 1), currency="USD")

    universe = _make_universe([inv1, inv2], payment=pay)
    result = engine.evaluate(payment=pay, universe=universe)

    assert result.status == CombinationMatchStatus.NO_COMBINATION_MATCH
    assert result.reason_code == CombinationMatchReasonCode.CURRENCY_MISMATCH


# ==============================================================================
# Determinism & Precision Tests
# ==============================================================================


def test_100_run_permutation_determinism() -> None:
    """Shuffling candidate list 100 times produces bit-for-bit identical results."""
    engine = CombinationMatchRuleEngine()
    pay = _make_payment("35000.00")

    inv1 = _make_candidate("INV-10K", "10000.00", date(2026, 5, 1))
    inv2 = _make_candidate("INV-25K", "25000.00", date(2026, 5, 15))
    inv3 = _make_candidate("INV-15K", "15000.00", date(2026, 6, 1))
    inv4 = _make_candidate("INV-8K", "8000.00", date(2026, 6, 10))
    inv5 = _make_candidate("INV-12K", "12000.00", date(2026, 6, 20))
    base_list = [inv1, inv2, inv3, inv4, inv5]

    universe = _make_universe(base_list, payment=pay)
    baseline_result = engine.evaluate(payment=pay, universe=universe)

    for i in range(100):
        shuffled = list(base_list)
        random.seed(i)
        random.shuffle(shuffled)
        shuffled_universe = _make_universe(shuffled, payment=pay)
        res = engine.evaluate(payment=pay, universe=shuffled_universe)

        assert res.status == baseline_result.status
        assert res.reason_code == baseline_result.reason_code
        assert res.applied_heuristic == baseline_result.applied_heuristic
        assert res.total_combinations_found == baseline_result.total_combinations_found
        assert (
            res.prioritized_combination.invoice_numbers
            == baseline_result.prioritized_combination.invoice_numbers
        )


def test_micro_penny_precision() -> None:
    """Exact Decimal arithmetic: 49.99 + 50.01 == 100.00 without floating point drift."""
    engine = CombinationMatchRuleEngine()
    pay = _make_payment("100.00")
    inv1 = _make_candidate("INV-P1", "49.99", date(2026, 7, 1))
    inv2 = _make_candidate("INV-P2", "50.01", date(2026, 7, 15))

    universe = _make_universe([inv1, inv2], payment=pay)
    result = engine.evaluate(payment=pay, universe=universe)

    assert result.status == CombinationMatchStatus.UNIQUE_COMBINATION_MATCH
    assert result.prioritized_combination is not None
    assert result.prioritized_combination.matched_amount == Decimal("100.00")
