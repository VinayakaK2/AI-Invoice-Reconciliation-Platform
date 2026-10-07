"""Domain entities, value objects, evidence signals, and rule engine for multi-invoice matching.

Phase 14.7 establishes deterministic multi-invoice payment matching (1:N) on top of the
bounded, 11-gate-filtered candidate universe produced by Phase 14.4.

CRITICAL INVARIANTS:
1. Deterministic Systems Decide: 100% deterministic rules, zero LLM authority.
2. Authoritative Outstanding Amount: Evaluates sum(candidate.outstanding_amount) == payment.effective_amount.
   Never compares against original total amount if prior partial payments exist.
3. Zero Financial Mutation: Strictly in-memory evaluation producing a matching fact / hypothesis.
   Zero writes to invoices, payments, allocations, or ledgers.
4. Multi-Invoice Scope (1:N): Evaluates whether payment can be an exact settlement for 2 or more
   candidates (2 <= k <= max_combination_size, default 4). Single-invoice matching is Phase 14.5/14.6.
5. Strict Ambiguity Preservation: When multiple distinct combinations sum to the payment amount,
   the engine emits AMBIGUOUS_MULTI_INVOICE_MATCH without arbitrary tie-breaking.
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


class MultiInvoiceMatchStatus(str, Enum):
    """Authoritative outcome of Phase 14.7 multi-invoice matching evaluation."""

    MULTI_INVOICE_MATCH = "MULTI_INVOICE_MATCH"
    NO_MULTI_INVOICE_MATCH = "NO_MULTI_INVOICE_MATCH"
    AMBIGUOUS_MULTI_INVOICE_MATCH = "AMBIGUOUS_MULTI_INVOICE_MATCH"


class MultiInvoiceMatchReasonCode(str, Enum):
    """Authoritative machine-readable reason taxonomy for multi-invoice matching."""

    MULTI_INVOICE_MATCH_FOUND = "MULTI_INVOICE_MATCH_FOUND"
    MULTI_INVOICE_WITH_REFERENCE_MATCH = "MULTI_INVOICE_WITH_REFERENCE_MATCH"
    NO_MULTI_INVOICE_COMBINATION_FOUND = "NO_MULTI_INVOICE_COMBINATION_FOUND"
    MULTIPLE_MULTI_INVOICE_MATCHES = "MULTIPLE_MULTI_INVOICE_MATCHES"
    TRUNCATED_UNIVERSE_AMBIGUITY = "TRUNCATED_UNIVERSE_AMBIGUITY"
    EXACT_MATCH_DETECTED = "EXACT_MATCH_DETECTED"
    PARTIAL_MATCH_DETECTED = "PARTIAL_MATCH_DETECTED"
    UNDERPAYMENT_DETECTED = "UNDERPAYMENT_DETECTED"
    PAYMENT_INELIGIBLE = "PAYMENT_INELIGIBLE"
    CUSTOMER_UNRESOLVED = "CUSTOMER_UNRESOLVED"
    NO_RETAINED_CANDIDATES = "NO_RETAINED_CANDIDATES"
    INSUFFICIENT_CANDIDATES = "INSUFFICIENT_CANDIDATES"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"


class MultiInvoiceMatchEvidenceType(str, Enum):
    """Categorization of evidence signals supporting a multi-invoice match."""

    MULTI_INVOICE_SUM_EXACT = "MULTI_INVOICE_SUM_EXACT"
    INVOICE_NUMBER_MATCH = "INVOICE_NUMBER_MATCH"
    CUSTOMER_NAME_MATCH = "CUSTOMER_NAME_MATCH"
    DATE_PROXIMITY_MATCH = "DATE_PROXIMITY_MATCH"


@dataclass(frozen=True)
class MultiInvoiceMatchEvidenceSignal:
    """Structured, immutable atomic evidence unit supporting a multi-invoice match hypothesis."""

    evidence_type: MultiInvoiceMatchEvidenceType
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
class MultiInvoiceMatchCriteria:
    """Configurable evaluation criteria for multi-invoice matching."""

    max_combination_size: int = 4
    amount_tolerance: Decimal = Decimal("0.00")
    require_exact_currency: bool = True
    date_proximity_days: int = 30

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
class MultiInvoiceMatchHypothesis:
    """Evaluated match pairing between a payment and a combination of eligible candidate invoices (1:N).

    Represents a proposed matching fact, strictly decoupled from financial mutation.
    """

    invoice_ids: List[UUID]
    invoice_numbers: List[str]
    matched_amount: Decimal
    invoices_outstanding_before: List[Decimal]
    invoices_outstanding_after: List[Decimal]
    payment_unallocated_before: Decimal
    payment_unallocated_after: Decimal
    currency: str
    match_type: str = "MULTI_INVOICE_EXACT"
    combination_size: int = 2
    has_reference_match: bool = False
    matched_reference_count: int = 0
    max_date_difference_days: int = 0
    evidence_signals: List[MultiInvoiceMatchEvidenceSignal] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate financial and domain invariants."""
        if len(self.invoice_ids) < 2:
            raise FinancialInvariantError(
                f"Multi-invoice match requires at least 2 invoices. Got: {len(self.invoice_ids)}"
            )
        if len(self.invoice_ids) != len(set(self.invoice_ids)):
            raise FinancialInvariantError("Duplicate invoice IDs in multi-invoice match combination.")
        if len(self.invoice_numbers) != len(self.invoice_ids):
            raise FinancialInvariantError("invoice_numbers length must match invoice_ids length.")
        if len(self.invoices_outstanding_before) != len(self.invoice_ids):
            raise FinancialInvariantError("invoices_outstanding_before length must match invoice_ids length.")
        if len(self.invoices_outstanding_after) != len(self.invoice_ids):
            raise FinancialInvariantError("invoices_outstanding_after length must match invoice_ids length.")

        if self.matched_amount <= Decimal("0.00"):
            raise FinancialInvariantError(
                f"Matched amount must be strictly positive. Got: {self.matched_amount}"
            )

        sum_before = sum(self.invoices_outstanding_before)
        if sum_before != self.matched_amount:
            raise FinancialInvariantError(
                f"Sum of candidate outstanding balances before match ({sum_before}) "
                f"must equal matched amount ({self.matched_amount})"
            )

        for rem in self.invoices_outstanding_after:
            if rem != Decimal("0.00"):
                raise FinancialInvariantError(
                    f"Projected invoice outstanding after exact multi-invoice match must be 0.00. Got: {rem}"
                )

        if self.payment_unallocated_before != self.matched_amount:
            raise FinancialInvariantError(
                f"Payment unallocated before match ({self.payment_unallocated_before}) "
                f"must equal matched amount ({self.matched_amount})"
            )
        if self.payment_unallocated_after != Decimal("0.00"):
            raise FinancialInvariantError(
                f"Projected payment unallocated after exact multi-invoice match must be 0.00. "
                f"Got: {self.payment_unallocated_after}"
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
            "match_type": self.match_type,
            "combination_size": self.combination_size,
            "has_reference_match": self.has_reference_match,
            "matched_reference_count": self.matched_reference_count,
            "max_date_difference_days": self.max_date_difference_days,
            "evidence_signals": [s.to_dict() for s in self.evidence_signals],
        }


@dataclass(frozen=True)
class MultiInvoiceMatchResult:
    """Authoritative outcome of Phase 14.7 multi-invoice matching evaluation.

    Guarantees:
    - Pure in-memory diagnostic fact (zero accounting mutation).
    - Preserves distinction between unique, no match, and ambiguous multi-invoice matches.
    - Preserves candidate universe completeness / truncation metadata.
    """

    payment_id: UUID
    company_id: UUID
    customer_id: Optional[UUID]
    status: MultiInvoiceMatchStatus
    matched_combination: Optional[MultiInvoiceMatchHypothesis]
    competing_combinations: List[MultiInvoiceMatchHypothesis]
    total_combinations_found: int
    reason_code: MultiInvoiceMatchReasonCode
    reason_description: str
    is_universe_truncated: bool
    candidate_count_evaluated: int
    is_deterministic: bool = True
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert multi-invoice match result to serializable dictionary."""
        return {
            "payment_id": str(self.payment_id),
            "company_id": str(self.company_id),
            "customer_id": str(self.customer_id) if self.customer_id else None,
            "status": self.status.value,
            "matched_combination": (
                self.matched_combination.to_dict() if self.matched_combination else None
            ),
            "competing_combinations": [c.to_dict() for c in self.competing_combinations],
            "total_combinations_found": self.total_combinations_found,
            "reason_code": self.reason_code.value,
            "reason_description": self.reason_description,
            "is_universe_truncated": self.is_universe_truncated,
            "candidate_count_evaluated": self.candidate_count_evaluated,
            "is_deterministic": self.is_deterministic,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class MultiInvoiceMatchRuleEngine:
    """Pure deterministic domain engine for Phase 14.7 multi-invoice matching.

    Guarantees:
    1. Zero Financial Mutation: strictly read-only evaluation.
    2. Zero Floating-Point Arithmetic: strict Decimal comparisons.
    3. Authoritative Outstanding Amount: uses candidate.outstanding_amount.
    4. Deterministic Output: identical inputs produce identical results regardless of candidate order.
    5. Multi-Invoice Scope: evaluates combinations of 2 <= k <= max_combination_size.
    6. Strict Ambiguity Preservation: if multiple combinations equal payment amount, emits AMBIGUOUS_MULTI_INVOICE_MATCH.
    """

    @staticmethod
    def _normalize_currency(currency: Optional[str]) -> str:
        """Normalize currency code to uppercase stripped string."""
        if not currency or not isinstance(currency, str):
            return ""
        return currency.strip().upper()

    @classmethod
    def _build_evidence_signals(
        cls,
        payment: PaymentIntakeContext,
        candidates: Tuple[CandidateInvoice, ...],
        criteria: MultiInvoiceMatchCriteria,
        effective_amount: Decimal,
    ) -> Tuple[List[MultiInvoiceMatchEvidenceSignal], bool, int, int]:
        """Compute structured evidence signals supporting a multi-invoice match hypothesis."""
        signals: List[MultiInvoiceMatchEvidenceSignal] = []
        inv_numbers = [c.invoice_number for c in candidates]
        inv_numbers_str = ", ".join(inv_numbers)

        # Signal 1: Exact Multi-Invoice Sum Match (+30.0, STRONG)
        signals.append(
            MultiInvoiceMatchEvidenceSignal(
                evidence_type=MultiInvoiceMatchEvidenceType.MULTI_INVOICE_SUM_EXACT,
                signal_strength=SignalStrength.STRONG,
                description=(
                    f"Sum of outstanding balances across {len(candidates)} candidate invoices "
                    f"({inv_numbers_str}) exactly equals payment amount ({effective_amount} {payment.currency})."
                ),
                matched_value=str(effective_amount),
                source_field="effective_amount",
                weight=30.0,
                metadata={
                    "combination_size": len(candidates),
                    "invoice_numbers": inv_numbers,
                    "applied_amount": str(effective_amount),
                },
            )
        )

        # Signal 2: Narration / Reference Match for any or all invoices (+35.0, STRONG)
        ref_matched_cands = [c for c in candidates if c.is_reference_match]
        has_reference_match = len(ref_matched_cands) > 0
        matched_reference_count = len(ref_matched_cands)

        if has_reference_match:
            ref_nums = [c.invoice_number for c in ref_matched_cands]
            signals.append(
                MultiInvoiceMatchEvidenceSignal(
                    evidence_type=MultiInvoiceMatchEvidenceType.INVOICE_NUMBER_MATCH,
                    signal_strength=SignalStrength.STRONG,
                    description=(
                        f"Payment narration contains explicit reference to {len(ref_matched_cands)} "
                        f"of the {len(candidates)} invoices in combination: {', '.join(ref_nums)}."
                    ),
                    matched_value=", ".join(ref_nums),
                    source_field="raw_narration",
                    weight=35.0,
                    metadata={"matched_reference_invoices": ref_nums},
                )
            )

        # Signal 3: Customer Context Established (+20.0, MEDIUM)
        signals.append(
            MultiInvoiceMatchEvidenceSignal(
                evidence_type=MultiInvoiceMatchEvidenceType.CUSTOMER_NAME_MATCH,
                signal_strength=SignalStrength.MEDIUM,
                description="All candidate invoices belong to verified counterparty customer workspace.",
                matched_value=", ".join(str(c.invoice_id) for c in candidates),
                source_field="customer_id",
                weight=20.0,
            )
        )

        # Signal 4: Date Proximity (+10.0, WEAK)
        date_diffs = [abs((payment.payment_date - c.due_date).days) for c in candidates]
        max_date_diff = max(date_diffs) if date_diffs else 0
        if max_date_diff <= criteria.date_proximity_days:
            signals.append(
                MultiInvoiceMatchEvidenceSignal(
                    evidence_type=MultiInvoiceMatchEvidenceType.DATE_PROXIMITY_MATCH,
                    signal_strength=SignalStrength.WEAK,
                    description=(
                        f"All invoices in combination have due dates within {max_date_diff} days "
                        f"of payment date (window: {criteria.date_proximity_days} days)."
                    ),
                    matched_value=f"{max_date_diff} days",
                    source_field="payment_date",
                    weight=10.0,
                    metadata={"max_days_difference": max_date_diff},
                )
            )

        return signals, has_reference_match, matched_reference_count, max_date_diff

    def evaluate(
        self,
        payment: PaymentIntakeContext,
        universe: FilteredCandidateUniverse,
        criteria: Optional[MultiInvoiceMatchCriteria] = None,
    ) -> MultiInvoiceMatchResult:
        """Evaluate multi-invoice matching between payment and filtered candidate universe."""
        criteria = criteria or MultiInvoiceMatchCriteria()

        # 1. Financial Sanity on Payment Effective Amount
        effective_amount = getattr(payment, "effective_amount", payment.amount)
        if effective_amount <= Decimal("0.00"):
            return MultiInvoiceMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH,
                matched_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                reason_code=MultiInvoiceMatchReasonCode.PAYMENT_INELIGIBLE,
                reason_description=f"Payment effective amount must be strictly positive. Got: {effective_amount}",
                is_universe_truncated=False,
                candidate_count_evaluated=0,
            )

        # 2. Check Upstream Universe Status Codes
        if universe.status_code == "CUSTOMER_UNRESOLVED":
            return MultiInvoiceMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=None,
                status=MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH,
                matched_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                reason_code=MultiInvoiceMatchReasonCode.CUSTOMER_UNRESOLVED,
                reason_description="Payer customer could not be resolved from payment narration.",
                is_universe_truncated=False,
                candidate_count_evaluated=0,
            )

        if universe.status_code == "NOT_ELIGIBLE":
            return MultiInvoiceMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH,
                matched_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                reason_code=MultiInvoiceMatchReasonCode.PAYMENT_INELIGIBLE,
                reason_description="Payment is not eligible for reconciliation intake.",
                is_universe_truncated=False,
                candidate_count_evaluated=0,
            )

        if universe.status_code == "CURRENCY_MISMATCH":
            return MultiInvoiceMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH,
                matched_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                reason_code=MultiInvoiceMatchReasonCode.CURRENCY_MISMATCH,
                reason_description="Currency mismatch detected: all open customer invoices are in incompatible currencies.",
                is_universe_truncated=False,
                candidate_count_evaluated=0,
            )

        # Determine truncation status from upstream universe
        is_truncated = (
            universe.exclusion_breakdown.get(
                FilterExclusionReason.TRUNCATED_BY_LIMIT.value, 0
            )
            > 0
        )

        # 3. Check for Empty Retained Candidates Set
        if not universe.retained_candidates:
            return MultiInvoiceMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH,
                matched_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                reason_code=MultiInvoiceMatchReasonCode.NO_RETAINED_CANDIDATES,
                reason_description="No open candidate invoices remained after candidate filtering.",
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=universe.total_evaluated,
            )

        # 4. Currency Compatibility Check on Payment
        norm_pay_curr = self._normalize_currency(payment.currency)
        if criteria.require_exact_currency:
            if not norm_pay_curr or not re.match(r"^[A-Z]{3}$", norm_pay_curr):
                return MultiInvoiceMatchResult(
                    payment_id=payment.payment_id,
                    company_id=payment.company_id,
                    customer_id=universe.customer_id,
                    status=MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH,
                    matched_combination=None,
                    competing_combinations=[],
                    total_combinations_found=0,
                    reason_code=MultiInvoiceMatchReasonCode.CURRENCY_MISMATCH,
                    reason_description=f"Invalid or unsupported payment currency code: '{payment.currency}'",
                    is_universe_truncated=is_truncated,
                    candidate_count_evaluated=len(universe.retained_candidates),
                )

        # 5. Filter Retained Candidates for Multi-Invoice Search
        eligible_candidates: List[CandidateInvoice] = []
        exact_single_candidates: List[CandidateInvoice] = []
        larger_single_candidates: List[CandidateInvoice] = []

        for cand in universe.retained_candidates:
            norm_cand_curr = self._normalize_currency(cand.currency)
            if criteria.require_exact_currency and norm_cand_curr != norm_pay_curr:
                continue
            if cand.outstanding_amount <= Decimal("0.00"):
                continue

            if cand.outstanding_amount == effective_amount:
                exact_single_candidates.append(cand)
            elif cand.outstanding_amount > effective_amount:
                larger_single_candidates.append(cand)
            else:
                # cand.outstanding_amount < effective_amount
                # Valid potential component of a multi-invoice combination!
                eligible_candidates.append(cand)

        # Need at least 2 candidates with outstanding_amount < effective_amount to form a multi-invoice match
        if len(eligible_candidates) < 2:
            diag_reason = MultiInvoiceMatchReasonCode.INSUFFICIENT_CANDIDATES
            diag_desc = (
                f"Fewer than 2 candidate invoices have outstanding balance less than payment amount "
                f"({effective_amount} {payment.currency}) to form a multi-invoice combination."
            )
            if exact_single_candidates:
                diag_reason = MultiInvoiceMatchReasonCode.EXACT_MATCH_DETECTED
                diag_desc = (
                    f"Payment amount ({effective_amount} {payment.currency}) exactly matches a single "
                    f"invoice ({exact_single_candidates[0].invoice_number}) (governed by Phase 14.5 Exact Matching)."
                )
            elif larger_single_candidates and not eligible_candidates:
                diag_reason = MultiInvoiceMatchReasonCode.PARTIAL_MATCH_DETECTED
                diag_desc = (
                    f"Payment amount ({effective_amount} {payment.currency}) is less than all open candidate "
                    f"invoices (governed by Phase 14.6 Partial Matching)."
                )

            return MultiInvoiceMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH,
                matched_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                reason_code=diag_reason,
                reason_description=diag_desc,
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # 6. Sort eligible candidates deterministically:
        # (due_date ASC, invoice_number ASC, invoice_id.int ASC)
        eligible_candidates.sort(
            key=lambda c: (
                c.due_date,
                c.invoice_number,
                c.invoice_id.int,
            )
        )

        # 7. Search for Combinations Summing to Effective Amount (2 <= k <= max_combination_size)
        matching_hypotheses: List[MultiInvoiceMatchHypothesis] = []
        max_k = min(criteria.max_combination_size, len(eligible_candidates))

        for k in range(2, max_k + 1):
            for combo in itertools.combinations(eligible_candidates, k):
                combo_sum = sum(c.outstanding_amount for c in combo)
                if abs(combo_sum - effective_amount) <= criteria.amount_tolerance:
                    # Found a valid subset!
                    signals, has_ref, ref_count, max_date_diff = self._build_evidence_signals(
                        payment=payment,
                        candidates=combo,
                        criteria=criteria,
                        effective_amount=effective_amount,
                    )
                    hypothesis = MultiInvoiceMatchHypothesis(
                        invoice_ids=[c.invoice_id for c in combo],
                        invoice_numbers=[c.invoice_number for c in combo],
                        matched_amount=effective_amount,
                        invoices_outstanding_before=[c.outstanding_amount for c in combo],
                        invoices_outstanding_after=[Decimal("0.00") for _ in combo],
                        payment_unallocated_before=effective_amount,
                        payment_unallocated_after=Decimal("0.00"),
                        currency=payment.currency,
                        match_type="MULTI_INVOICE_EXACT",
                        combination_size=k,
                        has_reference_match=has_ref,
                        matched_reference_count=ref_count,
                        max_date_difference_days=max_date_diff,
                        evidence_signals=signals,
                    )
                    matching_hypotheses.append(hypothesis)

        # Enforce deterministic total order on matching hypotheses:
        # 1. has_reference_match DESC
        # 2. matched_reference_count DESC
        # 3. combination_size ASC
        # 4. max_date_difference_days ASC
        # 5. tuple(invoice_numbers) ASC
        # 6. tuple(invoice_ids) ASC
        matching_hypotheses.sort(
            key=lambda h: (
                not h.has_reference_match,
                -h.matched_reference_count,
                h.combination_size,
                h.max_date_difference_days,
                tuple(h.invoice_numbers),
                tuple(id_.int for id_ in h.invoice_ids),
            )
        )

        total_combinations_found = len(matching_hypotheses)

        # 8. Decisioning: Zero, Multiple, or Single Multi-Invoice Match
        if total_combinations_found == 0:
            diag_reason = MultiInvoiceMatchReasonCode.NO_MULTI_INVOICE_COMBINATION_FOUND
            diag_desc = (
                f"No combination of 2 to {criteria.max_combination_size} candidate invoices "
                f"sums to payment amount ({effective_amount} {payment.currency})."
            )
            # Check if total sum of all eligible candidates is less than payment amount
            all_eligible_sum = sum(c.outstanding_amount for c in eligible_candidates)
            if all_eligible_sum < effective_amount:
                diag_reason = MultiInvoiceMatchReasonCode.UNDERPAYMENT_DETECTED
                diag_desc = (
                    f"Total outstanding balance of all {len(eligible_candidates)} smaller candidate "
                    f"invoices ({all_eligible_sum} {payment.currency}) is less than payment amount "
                    f"({effective_amount} {payment.currency})."
                )

            return MultiInvoiceMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH,
                matched_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                reason_code=diag_reason,
                reason_description=diag_desc,
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        if total_combinations_found > 1:
            # Ambiguity detected: multiple distinct combinations sum to payment amount.
            # Must strictly be flagged for human review. No autonomous picking!
            reason_code = (
                MultiInvoiceMatchReasonCode.TRUNCATED_UNIVERSE_AMBIGUITY
                if is_truncated
                else MultiInvoiceMatchReasonCode.MULTIPLE_MULTI_INVOICE_MATCHES
            )
            reason_desc = (
                f"Multiple distinct combinations ({total_combinations_found}) of candidate invoices "
                f"sum to payment amount ({effective_amount} {payment.currency}). "
                f"Flagged for human review or downstream scoring."
            )
            return MultiInvoiceMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=MultiInvoiceMatchStatus.AMBIGUOUS_MULTI_INVOICE_MATCH,
                matched_combination=None,
                competing_combinations=matching_hypotheses,
                total_combinations_found=total_combinations_found,
                reason_code=reason_code,
                reason_description=reason_desc,
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # 9. Exactly One Combination Found (total_combinations_found == 1)
        single_match = matching_hypotheses[0]

        # Check truncation risk: if universe was truncated, alternative combinations might exist
        # among the excluded invoices. If not all invoices in the combination have explicit reference match,
        # flag TRUNCATED_UNIVERSE_AMBIGUITY.
        if is_truncated and single_match.matched_reference_count < single_match.combination_size:
            return MultiInvoiceMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=MultiInvoiceMatchStatus.AMBIGUOUS_MULTI_INVOICE_MATCH,
                matched_combination=None,
                competing_combinations=matching_hypotheses,
                total_combinations_found=1,
                reason_code=MultiInvoiceMatchReasonCode.TRUNCATED_UNIVERSE_AMBIGUITY,
                reason_description=(
                    f"Single multi-invoice combination found, but candidate universe was truncated "
                    f"and not all invoices in the combination have explicit reference matches to guarantee uniqueness."
                ),
                is_universe_truncated=True,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # Single unambiguous multi-invoice match!
        reason_code = (
            MultiInvoiceMatchReasonCode.MULTI_INVOICE_WITH_REFERENCE_MATCH
            if single_match.has_reference_match
            else MultiInvoiceMatchReasonCode.MULTI_INVOICE_MATCH_FOUND
        )
        inv_nums_str = ", ".join(single_match.invoice_numbers)
        reason_desc = (
            f"Unique multi-invoice match found for {single_match.combination_size} invoices "
            f"({inv_nums_str}) summing exactly to {single_match.matched_amount} {payment.currency}."
        )
        return MultiInvoiceMatchResult(
            payment_id=payment.payment_id,
            company_id=payment.company_id,
            customer_id=universe.customer_id,
            status=MultiInvoiceMatchStatus.MULTI_INVOICE_MATCH,
            matched_combination=single_match,
            competing_combinations=[],
            total_combinations_found=1,
            reason_code=reason_code,
            reason_description=reason_desc,
            is_universe_truncated=is_truncated,
            candidate_count_evaluated=len(universe.retained_candidates),
        )
