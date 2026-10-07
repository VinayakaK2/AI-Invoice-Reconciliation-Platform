"""Domain entities, value objects, evidence signals, and rule engine for combination matching.

Phase 14.8 establishes deterministic combination matching across multiple outstanding invoices,
evaluating competing combinations, reference prioritization, and Rule M-3 FIFO aging heuristics
on top of the bounded, 11-gate-filtered candidate universe (Phase 14.4) and multi-invoice
matching fact foundation (Phase 14.7).

CRITICAL INVARIANTS:
1. Deterministic Systems Decide: 100% deterministic rules, zero LLM authority.
2. Authoritative Outstanding Amount: Evaluates sum(candidate.outstanding_amount) == payment.effective_amount.
   Never compares against original total amount if prior partial payments exist.
3. Zero Financial Mutation: Strictly in-memory evaluation producing a matching fact / hypothesis.
   Zero writes to invoices, payments, allocations, or ledgers (Observed Financial Mutation: NONE).
4. Multi-Invoice Combination Scope (1:N): Evaluates candidate combinations (2 <= k <= max_combination_size, default 4).
   Single-invoice matching is diagnosed and governed by Phase 14.5/14.6.
5. Rule M-3 FIFO Aging Heuristic: When multiple combinations sum to the same payment amount, the combination
   that settles the oldest outstanding invoices (by due_date) receives priority, but is explicitly flagged as
   REVIEW_REQUIRED due to ambiguity.
6. Ambiguity Preservation: If competing combinations have identical dates and evidence, ambiguity is preserved
   without arbitrary picking.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import itertools
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from app.modules.reconciliation.domain.candidate_filters import (
    FilterExclusionReason,
    FilteredCandidateUniverse,
)
from app.modules.reconciliation.domain.candidate_invoices import CandidateInvoice
from app.modules.reconciliation.domain.entities import SignalStrength
from app.modules.reconciliation.domain.rules import PaymentIntakeContext
from app.shared.exceptions import DomainError, FinancialInvariantError


class CombinationMatchStatus(str, Enum):
    """Authoritative outcome of Phase 14.8 combination matching evaluation."""

    UNIQUE_COMBINATION_MATCH = "UNIQUE_COMBINATION_MATCH"
    PRIORITIZED_COMBINATION_MATCH = "PRIORITIZED_COMBINATION_MATCH"
    AMBIGUOUS_COMBINATION_MATCH = "AMBIGUOUS_COMBINATION_MATCH"
    NO_COMBINATION_MATCH = "NO_COMBINATION_MATCH"


class CombinationMatchReasonCode(str, Enum):
    """Authoritative machine-readable reason taxonomy for combination matching."""

    UNIQUE_COMBINATION_FOUND = "UNIQUE_COMBINATION_FOUND"
    FIFO_AGING_PRIORITIZED = "FIFO_AGING_PRIORITIZED"
    REFERENCE_PRIORITIZED = "REFERENCE_PRIORITIZED"
    MULTIPLE_COMBINATIONS_UNRESOLVED = "MULTIPLE_COMBINATIONS_UNRESOLVED"
    TRUNCATED_UNIVERSE_AMBIGUITY = "TRUNCATED_UNIVERSE_AMBIGUITY"
    NO_COMBINATION_FOUND = "NO_COMBINATION_FOUND"
    UNDERPAYMENT_DETECTED = "UNDERPAYMENT_DETECTED"
    EXACT_MATCH_DETECTED = "EXACT_MATCH_DETECTED"
    PARTIAL_MATCH_DETECTED = "PARTIAL_MATCH_DETECTED"
    PAYMENT_INELIGIBLE = "PAYMENT_INELIGIBLE"
    CUSTOMER_UNRESOLVED = "CUSTOMER_UNRESOLVED"
    NO_RETAINED_CANDIDATES = "NO_RETAINED_CANDIDATES"
    INSUFFICIENT_CANDIDATES = "INSUFFICIENT_CANDIDATES"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"


class CombinationMatchEvidenceType(str, Enum):
    """Categorization of evidence signals supporting combination matching."""

    COMBINATION_SUM_EXACT = "COMBINATION_SUM_EXACT"
    FIFO_OLDEST_INVOICE_PRIORITY = "FIFO_OLDEST_INVOICE_PRIORITY"
    INVOICE_NUMBER_MATCH = "INVOICE_NUMBER_MATCH"
    CUSTOMER_NAME_MATCH = "CUSTOMER_NAME_MATCH"
    DATE_PROXIMITY_MATCH = "DATE_PROXIMITY_MATCH"


@dataclass(frozen=True)
class CombinationMatchEvidenceSignal:
    """Structured, immutable atomic evidence unit supporting a combination match hypothesis."""

    evidence_type: CombinationMatchEvidenceType
    signal_strength: SignalStrength
    description: str
    matched_value: str
    source_field: str
    weight: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert evidence signal to serializable dictionary."""
        return {
            "evidence_type": self.evidence_type.value,
            "signal_strength": self.signal_strength.value,
            "description": self.description,
            "matched_value": self.matched_value,
            "source_field": self.source_field,
            "weight": self.weight,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class CombinationMatchCriteria:
    """Configurable evaluation criteria for combination matching."""

    max_combination_size: int = 4
    amount_tolerance: Decimal = Decimal("0.00")
    require_exact_currency: bool = True
    date_proximity_days: int = 30
    enable_fifo_aging: bool = True

    def __post_init__(self) -> None:
        """Validate criteria invariants."""
        if self.max_combination_size < 2:
            raise DomainError(
                f"max_combination_size must be at least 2. Got: {self.max_combination_size}"
            )
        if self.max_combination_size > 10:
            raise DomainError(
                f"max_combination_size cannot exceed 10 for combinatorial safety. Got: {self.max_combination_size}"
            )
        if self.amount_tolerance < Decimal("0.00"):
            raise DomainError(
                f"amount_tolerance must be non-negative. Got: {self.amount_tolerance}"
            )
        if self.date_proximity_days < 0:
            raise DomainError(
                f"date_proximity_days must be non-negative. Got: {self.date_proximity_days}"
            )


@dataclass(frozen=True)
class CombinationHypothesis:
    """Evaluated match pairing between a payment and a combination of candidate invoices (1:N).

    Contains structured financial, aging, and evidence properties. Decoupled from financial mutation.
    """

    invoice_ids: List[UUID]
    invoice_numbers: List[str]
    matched_amount: Decimal
    invoices_outstanding_before: List[Decimal]
    invoices_outstanding_after: List[Decimal]
    payment_unallocated_before: Decimal
    payment_unallocated_after: Decimal
    currency: str
    combination_size: int
    oldest_due_date: date
    newest_due_date: date
    average_days_to_due: float
    has_reference_match: bool = False
    matched_reference_count: int = 0
    max_date_difference_days: int = 0
    is_fifo_prioritized: bool = False
    evidence_signals: List[CombinationMatchEvidenceSignal] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate financial and domain invariants."""
        if len(self.invoice_ids) < 2:
            raise DomainError(
                f"CombinationHypothesis must contain at least 2 invoices. Got: {len(self.invoice_ids)}"
            )
        if len(set(self.invoice_ids)) != len(self.invoice_ids):
            raise DomainError("Duplicate invoice IDs detected in combination hypothesis")
        if len(self.invoice_numbers) != len(self.invoice_ids):
            raise DomainError("invoice_numbers length must match invoice_ids length")
        if len(self.invoices_outstanding_before) != len(self.invoice_ids):
            raise DomainError("invoices_outstanding_before length must match invoice_ids length")
        if len(self.invoices_outstanding_after) != len(self.invoice_ids):
            raise DomainError("invoices_outstanding_after length must match invoice_ids length")
        if self.matched_amount <= Decimal("0.00"):
            raise FinancialInvariantError(
                f"matched_amount must be strictly positive. Got: {self.matched_amount}"
            )

        sum_before = sum(self.invoices_outstanding_before)
        if sum_before != self.matched_amount:
            raise FinancialInvariantError(
                f"Sum of invoices outstanding before ({sum_before}) does not equal "
                f"matched amount ({self.matched_amount})"
            )

        for after in self.invoices_outstanding_after:
            if after != Decimal("0.00"):
                raise FinancialInvariantError(
                    f"Full combination match requires all candidate balances to become 0.00. Got: {after}"
                )

        if self.payment_unallocated_after != Decimal("0.00"):
            raise FinancialInvariantError(
                f"Full combination match requires payment unallocated after to be 0.00. Got: {self.payment_unallocated_after}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Convert hypothesis to serializable dictionary."""
        return {
            "invoice_ids": [str(id_) for id_ in self.invoice_ids],
            "invoice_numbers": self.invoice_numbers,
            "matched_amount": str(self.matched_amount),
            "invoices_outstanding_before": [str(amt) for amt in self.invoices_outstanding_before],
            "invoices_outstanding_after": [str(amt) for amt in self.invoices_outstanding_after],
            "payment_unallocated_before": str(self.payment_unallocated_before),
            "payment_unallocated_after": str(self.payment_unallocated_after),
            "currency": self.currency,
            "combination_size": self.combination_size,
            "oldest_due_date": self.oldest_due_date.isoformat(),
            "newest_due_date": self.newest_due_date.isoformat(),
            "average_days_to_due": round(self.average_days_to_due, 2),
            "has_reference_match": self.has_reference_match,
            "matched_reference_count": self.matched_reference_count,
            "max_date_difference_days": self.max_date_difference_days,
            "is_fifo_prioritized": self.is_fifo_prioritized,
            "evidence_signals": [s.to_dict() for s in self.evidence_signals],
        }


@dataclass(frozen=True)
class CombinationMatchResult:
    """Authoritative result of Phase 14.8 combination matching evaluation.

    Encapsulates whether a unique, prioritized (Rule M-3 FIFO), ambiguous, or no combination
    match was established. Strictly read-only; leaves financial records 100% unmutated.
    """

    payment_id: UUID
    company_id: UUID
    customer_id: Optional[UUID]
    status: CombinationMatchStatus
    prioritized_combination: Optional[CombinationHypothesis]
    competing_combinations: List[CombinationHypothesis]
    total_combinations_found: int
    applied_heuristic: Optional[str]
    requires_review: bool
    reason_code: CombinationMatchReasonCode
    reason_description: str
    is_universe_truncated: bool
    candidate_count_evaluated: int
    is_deterministic: bool = True
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to serializable dictionary."""
        return {
            "payment_id": str(self.payment_id),
            "company_id": str(self.company_id),
            "customer_id": str(self.customer_id) if self.customer_id else None,
            "status": self.status.value,
            "prioritized_combination": (
                self.prioritized_combination.to_dict() if self.prioritized_combination else None
            ),
            "competing_combinations": [c.to_dict() for c in self.competing_combinations],
            "total_combinations_found": self.total_combinations_found,
            "applied_heuristic": self.applied_heuristic,
            "requires_review": self.requires_review,
            "reason_code": self.reason_code.value,
            "reason_description": self.reason_description,
            "is_universe_truncated": self.is_universe_truncated,
            "candidate_count_evaluated": self.candidate_count_evaluated,
            "is_deterministic": self.is_deterministic,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class CombinationMatchRuleEngine:
    """Pure domain rule engine executing Phase 14.8 Combination Matching.

    Evaluates whether an incoming payment effective amount corresponds to:
    1. A single unique combination of 2 or more eligible candidate invoices.
    2. Multiple competing combinations, applying explicit reference resolution and
       Rule M-3 FIFO aging heuristics (prioritizing the oldest outstanding invoices by due date)
       with mandatory REVIEW_REQUIRED flagging.
    3. Unresolvable ambiguity if competing combinations have identical dates and evidence.
    4. Diagnoses non-combination outcomes (underpayment, single exact match, single partial match).

    Guarantees:
    - Zero floating-point arithmetic (exact Python Decimal throughout).
    - Zero database mutations (pure stateless evaluation).
    - 100% permutation determinism (bit-for-bit identical results regardless of candidate ordering).
    - Strict candidate universe bounding (k <= max_combination_size, default 4).
    """

    def evaluate(
        self,
        payment: PaymentIntakeContext,
        universe: FilteredCandidateUniverse,
        criteria: Optional[CombinationMatchCriteria] = None,
    ) -> CombinationMatchResult:
        """Evaluate combination match for an intake payment against a filtered candidate universe."""
        if criteria is None:
            criteria = CombinationMatchCriteria()

        effective_amount = getattr(payment, "effective_amount", payment.amount)
        is_truncated = getattr(universe, "is_truncated", False) or (
            getattr(universe, "exclusion_breakdown", {}).get("TRUNCATED_BY_LIMIT", 0) > 0
            or getattr(universe, "status_code", "") == "TRUNCATED"
        )
        if effective_amount <= Decimal("0.00"):
            return CombinationMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=CombinationMatchStatus.NO_COMBINATION_MATCH,
                prioritized_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                applied_heuristic=None,
                requires_review=False,
                reason_code=CombinationMatchReasonCode.PAYMENT_INELIGIBLE,
                reason_description=f"Payment effective amount must be positive. Got: {effective_amount}",
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # 2. Gate: Customer Resolution Check
        if (
            getattr(universe, "status_code", None) == "CUSTOMER_UNRESOLVED"
            or not getattr(universe, "is_customer_resolved", True)
            or universe.customer_id is None
        ):
            return CombinationMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=None,
                status=CombinationMatchStatus.NO_COMBINATION_MATCH,
                prioritized_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                applied_heuristic=None,
                requires_review=False,
                reason_code=CombinationMatchReasonCode.CUSTOMER_UNRESOLVED,
                reason_description="Payer could not be uniquely identified in Phase 14.2.",
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # 3. Gate: Retained Candidates Check
        if not universe.retained_candidates:
            return CombinationMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=CombinationMatchStatus.NO_COMBINATION_MATCH,
                prioritized_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                applied_heuristic=None,
                requires_review=False,
                reason_code=CombinationMatchReasonCode.NO_RETAINED_CANDIDATES,
                reason_description="No candidate invoices remained after Phase 14.4 candidate filtering.",
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=0,
            )

        # 4. Gate: Currency Compatibility (Zero FX Policy)
        currency_incompatible = [
            c for c in universe.retained_candidates if c.currency != payment.currency
        ]
        if currency_incompatible and criteria.require_exact_currency:
            return CombinationMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=CombinationMatchStatus.NO_COMBINATION_MATCH,
                prioritized_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                applied_heuristic=None,
                requires_review=False,
                reason_code=CombinationMatchReasonCode.CURRENCY_MISMATCH,
                reason_description=(
                    f"Currency mismatch detected between payment ({payment.currency}) "
                    f"and candidate invoices. Cross-currency combination matching is deferred."
                ),
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # 5. Diagnostic Check: Single Candidate Exactly Matches (Governed by Phase 14.5)
        exact_single_matches = [
            c
            for c in universe.retained_candidates
            if c.currency == payment.currency
            and abs(c.outstanding_amount - effective_amount) <= criteria.amount_tolerance
        ]
        if exact_single_matches:
            first_exact = exact_single_matches[0]
            return CombinationMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=CombinationMatchStatus.NO_COMBINATION_MATCH,
                prioritized_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                applied_heuristic=None,
                requires_review=False,
                reason_code=CombinationMatchReasonCode.EXACT_MATCH_DETECTED,
                reason_description=(
                    f"Single candidate invoice {first_exact.invoice_number} exactly matches payment amount "
                    f"({effective_amount} {payment.currency}). Governed by Phase 14.5 Exact Matching."
                ),
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # 6. Eligible Candidates Pool: Must have outstanding_amount < effective_amount
        eligible_candidates = [
            c
            for c in universe.retained_candidates
            if c.currency == payment.currency
            and Decimal("0.00") < c.outstanding_amount < effective_amount
        ]

        if len(eligible_candidates) < 2:
            diag_reason = CombinationMatchReasonCode.INSUFFICIENT_CANDIDATES
            diag_desc = (
                f"Fewer than 2 candidate invoices have outstanding balance less than payment amount "
                f"({effective_amount} {payment.currency}). Found: {len(eligible_candidates)}."
            )
            # If all open candidates have balance > payment amount, diagnose partial match
            all_greater = [
                c
                for c in universe.retained_candidates
                if c.currency == payment.currency and c.outstanding_amount > effective_amount
            ]
            if len(all_greater) == len(universe.retained_candidates):
                diag_reason = CombinationMatchReasonCode.PARTIAL_MATCH_DETECTED
                diag_desc = (
                    f"Payment amount ({effective_amount} {payment.currency}) is less than all open candidate "
                    f"invoices. Governed by Phase 14.6 Partial Payment Matching."
                )

            return CombinationMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=CombinationMatchStatus.NO_COMBINATION_MATCH,
                prioritized_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                applied_heuristic=None,
                requires_review=False,
                reason_code=diag_reason,
                reason_description=diag_desc,
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # Sort eligible candidates deterministically by invoice_number, invoice_id
        eligible_candidates = sorted(
            eligible_candidates,
            key=lambda c: (c.invoice_number, c.invoice_id),
        )

        # 7. Combinatorial Search: 2 <= k <= max_combination_size
        matching_hypotheses: List[CombinationHypothesis] = []
        max_k = min(criteria.max_combination_size, len(eligible_candidates))

        for k in range(2, max_k + 1):
            for combo in itertools.combinations(eligible_candidates, k):
                combo_sum = sum(c.outstanding_amount for c in combo)
                if abs(combo_sum - effective_amount) <= criteria.amount_tolerance:
                    hypothesis = self._build_hypothesis(
                        payment=payment,
                        combo=combo,
                        criteria=criteria,
                        effective_amount=effective_amount,
                    )
                    matching_hypotheses.append(hypothesis)

        total_combinations_found = len(matching_hypotheses)

        # 8. Zero Combinations Found
        if total_combinations_found == 0:
            diag_reason = CombinationMatchReasonCode.NO_COMBINATION_FOUND
            diag_desc = (
                f"No combination of 2 to {criteria.max_combination_size} candidate invoices "
                f"sums to payment amount ({effective_amount} {payment.currency})."
            )
            all_eligible_sum = sum(c.outstanding_amount for c in eligible_candidates)
            if all_eligible_sum < effective_amount:
                diag_reason = CombinationMatchReasonCode.UNDERPAYMENT_DETECTED
                diag_desc = (
                    f"Total outstanding balance of all {len(eligible_candidates)} smaller candidate "
                    f"invoices ({all_eligible_sum} {payment.currency}) is less than payment amount "
                    f"({effective_amount} {payment.currency})."
                )

            return CombinationMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=CombinationMatchStatus.NO_COMBINATION_MATCH,
                prioritized_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                applied_heuristic=None,
                requires_review=False,
                reason_code=diag_reason,
                reason_description=diag_desc,
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # 9. Exactly One Combination Found
        if total_combinations_found == 1:
            single_match = matching_hypotheses[0]
            # Check truncation risk
            if is_truncated and single_match.matched_reference_count < single_match.combination_size:
                return CombinationMatchResult(
                    payment_id=payment.payment_id,
                    company_id=payment.company_id,
                    customer_id=universe.customer_id,
                    status=CombinationMatchStatus.AMBIGUOUS_COMBINATION_MATCH,
                    prioritized_combination=None,
                    competing_combinations=matching_hypotheses,
                    total_combinations_found=1,
                    applied_heuristic=None,
                    requires_review=True,
                    reason_code=CombinationMatchReasonCode.TRUNCATED_UNIVERSE_AMBIGUITY,
                    reason_description=(
                        f"One combination of {single_match.combination_size} invoices matches payment amount, "
                        f"but candidate universe was truncated to enforce bounding limit. Flagged for review."
                    ),
                    is_universe_truncated=True,
                    candidate_count_evaluated=len(universe.retained_candidates),
                )

            return CombinationMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=CombinationMatchStatus.UNIQUE_COMBINATION_MATCH,
                prioritized_combination=single_match,
                competing_combinations=matching_hypotheses,
                total_combinations_found=1,
                applied_heuristic=None,
                requires_review=False,
                reason_code=CombinationMatchReasonCode.UNIQUE_COMBINATION_FOUND,
                reason_description=(
                    f"Exactly one unique combination of {single_match.combination_size} candidate invoices "
                    f"sums to payment amount ({effective_amount} {payment.currency})."
                ),
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # 10. Multiple Competing Combinations Found (total_combinations_found > 1)
        # Evaluate reference signals and Rule M-3 FIFO aging heuristic!
        return self._resolve_competing_combinations(
            payment=payment,
            universe=universe,
            hypotheses=matching_hypotheses,
            criteria=criteria,
            effective_amount=effective_amount,
            is_truncated=is_truncated,
        )

    def _resolve_competing_combinations(
        self,
        payment: PaymentIntakeContext,
        universe: FilteredCandidateUniverse,
        hypotheses: List[CombinationHypothesis],
        criteria: CombinationMatchCriteria,
        effective_amount: Decimal,
        is_truncated: bool,
    ) -> CombinationMatchResult:
        """Evaluate reference priority and Rule M-3 FIFO aging heuristics among competing combinations."""
        # 1. First priority: Explicit Reference Match
        # If one combination has strictly higher matched_reference_count than all others
        max_refs = max(h.matched_reference_count for h in hypotheses)
        top_ref_combos = [h for h in hypotheses if h.matched_reference_count == max_refs]

        if max_refs > 0 and len(top_ref_combos) == 1:
            prioritized = top_ref_combos[0]
            # Order remaining combinations deterministically
            remaining = [h for h in hypotheses if h != prioritized]
            remaining_sorted = self._sort_hypotheses_deterministically(remaining)
            ordered_all = [prioritized] + remaining_sorted

            return CombinationMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=CombinationMatchStatus.PRIORITIZED_COMBINATION_MATCH,
                prioritized_combination=prioritized,
                competing_combinations=ordered_all,
                total_combinations_found=len(hypotheses),
                applied_heuristic="REFERENCE_MATCH_PRIORITY",
                requires_review=True,
                reason_code=CombinationMatchReasonCode.REFERENCE_PRIORITIZED,
                reason_description=(
                    f"Combination of {prioritized.combination_size} invoices prioritized because narration "
                    f"explicitly references {prioritized.matched_reference_count} of its invoice numbers. "
                    f"Flagged for human review due to {len(hypotheses) - 1} competing mathematical combinations."
                ),
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # 2. Second priority: Rule M-3 FIFO Aging Heuristic (if enabled)
        if criteria.enable_fifo_aging:
            # Sort candidate hypotheses by aging criteria:
            # Primary: oldest_due_date ASC (earliest calendar date settles oldest debt)
            # Secondary: average_days_to_due ASC
            # Tertiary: combination_size ASC
            # Quaternary: tuple(invoice_numbers) ASC
            # Quinary: tuple(invoice_ids) ASC
            aging_sorted = sorted(
                hypotheses,
                key=lambda h: (
                    h.oldest_due_date,
                    h.average_days_to_due,
                    h.combination_size,
                    tuple(h.invoice_numbers),
                    tuple(id_.int for id_ in h.invoice_ids),
                ),
            )

            oldest_h = aging_sorted[0]
            second_h = aging_sorted[1]

            # Check if oldest_h strictly settles older debt than second_h
            is_strictly_older = oldest_h.oldest_due_date < second_h.oldest_due_date or (
                oldest_h.oldest_due_date == second_h.oldest_due_date
                and oldest_h.average_days_to_due < second_h.average_days_to_due
            )

            if is_strictly_older:
                # Prioritize oldest_h under Rule M-3!
                fifo_signal = CombinationMatchEvidenceSignal(
                    evidence_type=CombinationMatchEvidenceType.FIFO_OLDEST_INVOICE_PRIORITY,
                    signal_strength=SignalStrength.MEDIUM,
                    description=(
                        f"Rule M-3 FIFO aging heuristic: Combination settles oldest outstanding "
                        f"invoice due on {oldest_h.oldest_due_date.isoformat()} (vs competitor {second_h.oldest_due_date.isoformat()})."
                    ),
                    matched_value=oldest_h.oldest_due_date.isoformat(),
                    source_field="due_date",
                    weight=15.0,
                    metadata={
                        "oldest_due_date": oldest_h.oldest_due_date.isoformat(),
                        "competitor_oldest_due_date": second_h.oldest_due_date.isoformat(),
                    },
                )
                prioritized = CombinationHypothesis(
                    invoice_ids=oldest_h.invoice_ids,
                    invoice_numbers=oldest_h.invoice_numbers,
                    matched_amount=oldest_h.matched_amount,
                    invoices_outstanding_before=oldest_h.invoices_outstanding_before,
                    invoices_outstanding_after=oldest_h.invoices_outstanding_after,
                    payment_unallocated_before=oldest_h.payment_unallocated_before,
                    payment_unallocated_after=oldest_h.payment_unallocated_after,
                    currency=oldest_h.currency,
                    combination_size=oldest_h.combination_size,
                    oldest_due_date=oldest_h.oldest_due_date,
                    newest_due_date=oldest_h.newest_due_date,
                    average_days_to_due=oldest_h.average_days_to_due,
                    has_reference_match=oldest_h.has_reference_match,
                    matched_reference_count=oldest_h.matched_reference_count,
                    max_date_difference_days=oldest_h.max_date_difference_days,
                    is_fifo_prioritized=True,
                    evidence_signals=oldest_h.evidence_signals + [fifo_signal],
                )

                remaining = [h for h in hypotheses if h != oldest_h]
                ordered_all = [prioritized] + self._sort_hypotheses_deterministically(remaining)

                return CombinationMatchResult(
                    payment_id=payment.payment_id,
                    company_id=payment.company_id,
                    customer_id=universe.customer_id,
                    status=CombinationMatchStatus.PRIORITIZED_COMBINATION_MATCH,
                    prioritized_combination=prioritized,
                    competing_combinations=ordered_all,
                    total_combinations_found=len(hypotheses),
                    applied_heuristic="RULE_M3_FIFO_AGING",
                    requires_review=True,
                    reason_code=CombinationMatchReasonCode.FIFO_AGING_PRIORITIZED,
                    reason_description=(
                        f"Combination of {prioritized.combination_size} invoices prioritized via Rule M-3 "
                        f"FIFO aging heuristic (settles oldest invoice due {prioritized.oldest_due_date.isoformat()}). "
                        f"Flagged for human review due to ambiguity across {len(hypotheses)} matching combinations."
                    ),
                    is_universe_truncated=is_truncated,
                    candidate_count_evaluated=len(universe.retained_candidates),
                )

        # 3. Third Case: Unresolvable Ambiguity (identical dates and evidence)
        deterministic_order = self._sort_hypotheses_deterministically(hypotheses)
        reason_code = (
            CombinationMatchReasonCode.TRUNCATED_UNIVERSE_AMBIGUITY
            if is_truncated
            else CombinationMatchReasonCode.MULTIPLE_COMBINATIONS_UNRESOLVED
        )

        return CombinationMatchResult(
            payment_id=payment.payment_id,
            company_id=payment.company_id,
            customer_id=universe.customer_id,
            status=CombinationMatchStatus.AMBIGUOUS_COMBINATION_MATCH,
            prioritized_combination=None,
            competing_combinations=deterministic_order,
            total_combinations_found=len(hypotheses),
            applied_heuristic=None,
            requires_review=True,
            reason_code=reason_code,
            reason_description=(
                f"Multiple distinct combinations ({len(hypotheses)}) sum to payment amount "
                f"({effective_amount} {payment.currency}) with tied aging and reference evidence. "
                f"Strictly flagged for human review without arbitrary selection."
            ),
            is_universe_truncated=is_truncated,
            candidate_count_evaluated=len(universe.retained_candidates),
        )

    def _build_hypothesis(
        self,
        payment: PaymentIntakeContext,
        combo: Tuple[CandidateInvoice, ...],
        criteria: CombinationMatchCriteria,
        effective_amount: Decimal,
    ) -> CombinationHypothesis:
        """Construct an immutable combination hypothesis with evidence signals and aging metadata."""
        due_dates = [c.due_date for c in combo]
        oldest_due = min(due_dates)
        newest_due = max(due_dates)

        payment_date = getattr(payment, "payment_date", getattr(payment, "transaction_date", date.today()))
        # Average days to due date relative to payment date (positive = due after payment, negative = overdue)
        avg_days = sum((d - payment_date).days for d in due_dates) / len(due_dates)

        signals, has_ref, ref_count, max_date_diff = self._build_evidence_signals(
            payment=payment,
            candidates=combo,
            criteria=criteria,
            effective_amount=effective_amount,
        )

        return CombinationHypothesis(
            invoice_ids=[c.invoice_id for c in combo],
            invoice_numbers=[c.invoice_number for c in combo],
            matched_amount=effective_amount,
            invoices_outstanding_before=[c.outstanding_amount for c in combo],
            invoices_outstanding_after=[Decimal("0.00") for _ in combo],
            payment_unallocated_before=effective_amount,
            payment_unallocated_after=Decimal("0.00"),
            currency=payment.currency,
            combination_size=len(combo),
            oldest_due_date=oldest_due,
            newest_due_date=newest_due,
            average_days_to_due=avg_days,
            has_reference_match=has_ref,
            matched_reference_count=ref_count,
            max_date_difference_days=max_date_diff,
            is_fifo_prioritized=False,
            evidence_signals=signals,
        )

    def _build_evidence_signals(
        self,
        payment: PaymentIntakeContext,
        candidates: Tuple[CandidateInvoice, ...],
        criteria: CombinationMatchCriteria,
        effective_amount: Decimal,
    ) -> Tuple[List[CombinationMatchEvidenceSignal], bool, int, int]:
        """Construct structured evidence signals for a combination of invoices."""
        signals: List[CombinationMatchEvidenceSignal] = []

        # 1. Sum exact match signal
        signals.append(
            CombinationMatchEvidenceSignal(
                evidence_type=CombinationMatchEvidenceType.COMBINATION_SUM_EXACT,
                signal_strength=SignalStrength.STRONG,
                description=(
                    f"Sum of outstanding balances for {len(candidates)} candidate invoices "
                    f"exactly equals payment effective amount: {effective_amount} {payment.currency}"
                ),
                matched_value=str(effective_amount),
                source_field="outstanding_amount",
                weight=30.0,
                metadata={
                    "combination_size": len(candidates),
                    "invoice_numbers": [c.invoice_number for c in candidates],
                },
            )
        )

        # 2. Reference match signals in narration
        narration = (
            getattr(payment, "narration", "")
            or getattr(payment, "raw_narration", "")
            or getattr(payment, "normalized_narration", "")
            or ""
        )
        matched_ref_count = 0
        for c in candidates:
            inv_clean = re.sub(r"[^A-Za-z0-9]", "", c.invoice_number).upper()
            narr_clean = re.sub(r"[^A-Za-z0-9]", "", narration).upper()
            is_match = getattr(c, "is_reference_match", False) or (inv_clean and inv_clean in narr_clean)
            if is_match:
                matched_ref_count += 1
                signals.append(
                    CombinationMatchEvidenceSignal(
                        evidence_type=CombinationMatchEvidenceType.INVOICE_NUMBER_MATCH,
                        signal_strength=SignalStrength.STRONG,
                        description=(
                            f"Payment narration references candidate invoice number {c.invoice_number}"
                        ),
                        matched_value=c.invoice_number,
                        source_field="narration",
                        weight=35.0,
                        metadata={"invoice_id": str(c.invoice_id)},
                    )
                )

        has_reference = matched_ref_count > 0

        # 3. Customer name signal
        payer_name = getattr(payment, "customer_name", getattr(payment, "payer_raw_name", None))
        if payer_name:
            signals.append(
                CombinationMatchEvidenceSignal(
                    evidence_type=CombinationMatchEvidenceType.CUSTOMER_NAME_MATCH,
                    signal_strength=SignalStrength.STRONG,
                    description=f"Payment payer matches verified customer {payer_name}",
                    matched_value=payer_name,
                    source_field="customer_name",
                    weight=20.0,
                )
            )

        # 4. Date proximity signals
        payment_date = getattr(payment, "payment_date", getattr(payment, "transaction_date", date.today()))
        max_date_diff = 0
        for c in candidates:
            date_diff = abs((c.due_date - payment_date).days)
            if date_diff > max_date_diff:
                max_date_diff = date_diff
            if date_diff <= criteria.date_proximity_days:
                signals.append(
                    CombinationMatchEvidenceSignal(
                        evidence_type=CombinationMatchEvidenceType.DATE_PROXIMITY_MATCH,
                        signal_strength=SignalStrength.MEDIUM,
                        description=(
                            f"Invoice {c.invoice_number} due date ({c.due_date}) is within "
                            f"{date_diff} days of payment date ({payment_date})"
                        ),
                        matched_value=c.due_date.isoformat(),
                        source_field="due_date",
                        weight=10.0,
                        metadata={"days_difference": date_diff},
                    )
                )

        return signals, has_reference, matched_ref_count, max_date_diff

    def _sort_hypotheses_deterministically(
        self, hypotheses: List[CombinationHypothesis]
    ) -> List[CombinationHypothesis]:
        """Sort combination hypotheses with strict deterministic total ordering."""
        return sorted(
            hypotheses,
            key=lambda h: (
                not h.has_reference_match,
                -h.matched_reference_count,
                h.oldest_due_date,
                h.average_days_to_due,
                h.combination_size,
                h.max_date_difference_days,
                tuple(h.invoice_numbers),
                tuple(id_.int for id_ in h.invoice_ids),
            ),
        )
