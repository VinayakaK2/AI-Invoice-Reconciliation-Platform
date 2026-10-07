"""Domain entities, value objects, evidence signals, and rule engine for partial 1:1 matching.

Phase 14.6 establishes deterministic single-invoice partial-payment matching on top of the
bounded, 11-gate-filtered candidate universe produced by Phase 14.4.

CRITICAL INVARIANTS:
1. Deterministic Systems Decide: 100% deterministic rules, zero LLM authority.
2. Authoritative Outstanding Amount: Evaluates 0 < payment.effective_amount < candidate.outstanding_amount.
   Never compares against original total amount if prior partial payments exist.
3. Zero Financial Mutation: Strictly in-memory evaluation producing a matching fact / hypothesis.
   Zero writes to invoices, payments, allocations, or ledgers.
4. Single-Invoice Scope: Evaluates whether payment can be a partial payment for one candidate at a time.
   Multi-invoice combination matching is strictly deferred to Phase 14.7.
5. Strict Ambiguity Preservation: When multiple distinct candidates can accept the partial payment,
   the engine emits AMBIGUOUS_PARTIAL_MATCH without arbitrary tie-breaking.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
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


class PartialMatchStatus(str, Enum):
    """Authoritative outcome of Phase 14.6 single-invoice partial matching evaluation."""

    PARTIAL_MATCH = "PARTIAL_MATCH"
    NO_PARTIAL_MATCH = "NO_PARTIAL_MATCH"
    AMBIGUOUS_PARTIAL_MATCH = "AMBIGUOUS_PARTIAL_MATCH"


class PartialMatchReasonCode(str, Enum):
    """Authoritative machine-readable reason taxonomy for partial 1:1 matching."""

    PARTIAL_AMOUNT_MATCH_FOUND = "PARTIAL_AMOUNT_MATCH_FOUND"
    PARTIAL_AMOUNT_WITH_REFERENCE_MATCH = "PARTIAL_AMOUNT_WITH_REFERENCE_MATCH"
    NO_CANDIDATE_ELIGIBLE_FOR_PARTIAL = "NO_CANDIDATE_ELIGIBLE_FOR_PARTIAL"
    MULTIPLE_PARTIAL_CANDIDATES = "MULTIPLE_PARTIAL_CANDIDATES"
    TRUNCATED_UNIVERSE_AMBIGUITY = "TRUNCATED_UNIVERSE_AMBIGUITY"
    EXACT_MATCH_DETECTED = "EXACT_MATCH_DETECTED"
    OVERPAYMENT_DETECTED = "OVERPAYMENT_DETECTED"
    PAYMENT_INELIGIBLE = "PAYMENT_INELIGIBLE"
    CUSTOMER_UNRESOLVED = "CUSTOMER_UNRESOLVED"
    NO_RETAINED_CANDIDATES = "NO_RETAINED_CANDIDATES"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    ZERO_OUTSTANDING_BALANCE = "ZERO_OUTSTANDING_BALANCE"


class PartialMatchEvidenceType(str, Enum):
    """Categorization of evidence signals supporting a partial 1:1 match."""

    PARTIAL_AMOUNT_COMPATIBLE = "PARTIAL_AMOUNT_COMPATIBLE"
    INVOICE_NUMBER_MATCH = "INVOICE_NUMBER_MATCH"
    IDENTIFIER_MATCH = "IDENTIFIER_MATCH"
    CUSTOMER_NAME_MATCH = "CUSTOMER_NAME_MATCH"
    DATE_PROXIMITY_MATCH = "DATE_PROXIMITY_MATCH"


@dataclass(frozen=True)
class PartialMatchEvidenceSignal:
    """Structured, immutable atomic evidence unit supporting a partial match hypothesis."""

    evidence_type: PartialMatchEvidenceType
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
class PartialMatchCriteria:
    """Configurable evaluation criteria for partial 1:1 matching."""

    amount_tolerance: Decimal = Decimal("0.00")
    require_exact_currency: bool = True
    date_proximity_days: int = 30

    def __post_init__(self) -> None:
        """Validate criteria invariants."""
        if self.amount_tolerance < Decimal("0.00"):
            raise DomainError(
                f"amount_tolerance must be non-negative. Got: {self.amount_tolerance}"
            )
        if self.date_proximity_days < 0:
            raise DomainError(
                f"date_proximity_days must be non-negative. Got: {self.date_proximity_days}"
            )


@dataclass(frozen=True)
class PartialMatchHypothesis:
    """Evaluated partial match pairing between a payment and an eligible candidate invoice.

    Represents a proposed matching fact, strictly decoupled from financial mutation.
    """

    invoice_id: UUID
    invoice_number: str
    matched_amount: Decimal
    invoice_outstanding_before: Decimal
    invoice_outstanding_after: Decimal
    payment_unallocated_before: Decimal
    payment_unallocated_after: Decimal
    currency: str
    match_type: str = "PARTIAL_ONE_TO_ONE"
    is_reference_match: bool = False
    date_difference_days: int = 0
    evidence_signals: List[PartialMatchEvidenceSignal] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate financial and domain invariants."""
        if self.matched_amount <= Decimal("0.00"):
            raise FinancialInvariantError(
                f"Matched amount must be strictly positive. Got: {self.matched_amount}"
            )
        if self.invoice_outstanding_before <= Decimal("0.00"):
            raise FinancialInvariantError(
                f"Invoice outstanding balance before match must be positive. "
                f"Got: {self.invoice_outstanding_before}"
            )
        if self.matched_amount >= self.invoice_outstanding_before:
            raise FinancialInvariantError(
                f"Partial 1:1 match requires matched_amount ({self.matched_amount}) < "
                f"invoice_outstanding_before ({self.invoice_outstanding_before})"
            )
        expected_remaining = self.invoice_outstanding_before - self.matched_amount
        if self.invoice_outstanding_after != expected_remaining:
            raise FinancialInvariantError(
                f"Projected invoice outstanding after partial match must be {expected_remaining}. "
                f"Got: {self.invoice_outstanding_after}"
            )
        if self.payment_unallocated_before != self.matched_amount:
            raise FinancialInvariantError(
                f"Payment unallocated before match ({self.payment_unallocated_before}) "
                f"must equal matched amount ({self.matched_amount})"
            )
        if self.payment_unallocated_after != Decimal("0.00"):
            raise FinancialInvariantError(
                f"Projected payment unallocated after partial match against single invoice must be 0.00. "
                f"Got: {self.payment_unallocated_after}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Convert hypothesis to serializable dictionary."""
        return {
            "invoice_id": str(self.invoice_id),
            "invoice_number": self.invoice_number,
            "matched_amount": str(self.matched_amount),
            "invoice_outstanding_before": str(self.invoice_outstanding_before),
            "invoice_outstanding_after": str(self.invoice_outstanding_after),
            "payment_unallocated_before": str(self.payment_unallocated_before),
            "payment_unallocated_after": str(self.payment_unallocated_after),
            "currency": self.currency,
            "match_type": self.match_type,
            "is_reference_match": self.is_reference_match,
            "date_difference_days": self.date_difference_days,
            "evidence_signals": [s.to_dict() for s in self.evidence_signals],
        }


@dataclass(frozen=True)
class PartialMatchResult:
    """Authoritative outcome of Phase 14.6 partial 1:1 matching evaluation.

    Guarantees:
    - Pure in-memory diagnostic fact (zero accounting mutation).
    - Preserves distinction between unique, no match, and ambiguous partial match.
    - Preserves candidate universe completeness / truncation metadata.
    """

    payment_id: UUID
    company_id: UUID
    customer_id: Optional[UUID]
    status: PartialMatchStatus
    matched_candidate: Optional[PartialMatchHypothesis]
    competing_candidates: List[PartialMatchHypothesis]
    total_partial_candidates_found: int
    reason_code: PartialMatchReasonCode
    reason_description: str
    is_universe_truncated: bool
    candidate_count_evaluated: int
    is_deterministic: bool = True
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert partial match result to serializable dictionary."""
        return {
            "payment_id": str(self.payment_id),
            "company_id": str(self.company_id),
            "customer_id": str(self.customer_id) if self.customer_id else None,
            "status": self.status.value,
            "matched_candidate": (
                self.matched_candidate.to_dict() if self.matched_candidate else None
            ),
            "competing_candidates": [c.to_dict() for c in self.competing_candidates],
            "total_partial_candidates_found": self.total_partial_candidates_found,
            "reason_code": self.reason_code.value,
            "reason_description": self.reason_description,
            "is_universe_truncated": self.is_universe_truncated,
            "candidate_count_evaluated": self.candidate_count_evaluated,
            "is_deterministic": self.is_deterministic,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class PartialMatchRuleEngine:
    """Pure deterministic domain engine for Phase 14.6 partial 1:1 matching.

    Guarantees:
    1. Zero Financial Mutation: strictly read-only evaluation.
    2. Zero Floating-Point Arithmetic: strict Decimal comparisons.
    3. Authoritative Outstanding Amount: uses candidate.outstanding_amount.
    4. Deterministic Output: identical inputs produce identical results.
    5. Single Invoice Scope: evaluates candidates individually without combinatorial grouping.
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
        candidate: CandidateInvoice,
        criteria: PartialMatchCriteria,
        effective_amount: Decimal,
    ) -> List[PartialMatchEvidenceSignal]:
        """Compute structured evidence signals supporting partial match hypothesis."""
        signals: List[PartialMatchEvidenceSignal] = []
        remaining_balance = candidate.outstanding_amount - effective_amount

        # Signal 1: Partial Amount Compatible (+25.0, STRONG)
        signals.append(
            PartialMatchEvidenceSignal(
                evidence_type=PartialMatchEvidenceType.PARTIAL_AMOUNT_COMPATIBLE,
                signal_strength=SignalStrength.STRONG,
                description=(
                    f"Payment unallocated amount ({effective_amount} {payment.currency}) is strictly "
                    f"less than invoice outstanding balance ({candidate.outstanding_amount} {candidate.currency}). "
                    f"Projected remaining balance: {remaining_balance} {candidate.currency}."
                ),
                matched_value=str(effective_amount),
                source_field="effective_amount",
                weight=25.0,
                metadata={
                    "applied_amount": str(effective_amount),
                    "remaining_balance": str(remaining_balance),
                },
            )
        )

        # Signal 2: Explicit Invoice Number Match (+35.0, STRONG)
        if candidate.is_reference_match:
            signals.append(
                PartialMatchEvidenceSignal(
                    evidence_type=PartialMatchEvidenceType.INVOICE_NUMBER_MATCH,
                    signal_strength=SignalStrength.STRONG,
                    description=f"Payment narration contains explicit invoice number '{candidate.invoice_number}'.",
                    matched_value=candidate.invoice_number,
                    source_field="raw_narration",
                    weight=35.0,
                )
            )

        # Signal 3: Customer Context Established (+20.0, MEDIUM)
        signals.append(
            PartialMatchEvidenceSignal(
                evidence_type=PartialMatchEvidenceType.CUSTOMER_NAME_MATCH,
                signal_strength=SignalStrength.MEDIUM,
                description="Invoice belongs to verified counterparty customer workspace.",
                matched_value=str(candidate.invoice_id),
                source_field="customer_id",
                weight=20.0,
            )
        )

        # Signal 4: Date Proximity (+10.0, WEAK)
        date_diff = abs((payment.payment_date - candidate.due_date).days)
        if date_diff <= criteria.date_proximity_days:
            signals.append(
                PartialMatchEvidenceSignal(
                    evidence_type=PartialMatchEvidenceType.DATE_PROXIMITY_MATCH,
                    signal_strength=SignalStrength.WEAK,
                    description=f"Payment date is within {date_diff} days of invoice due date (window: {criteria.date_proximity_days} days).",
                    matched_value=f"{date_diff} days",
                    source_field="payment_date",
                    weight=10.0,
                    metadata={"days_difference": date_diff},
                )
            )

        return signals

    def evaluate(
        self,
        payment: PaymentIntakeContext,
        universe: FilteredCandidateUniverse,
        criteria: Optional[PartialMatchCriteria] = None,
    ) -> PartialMatchResult:
        """Evaluate partial 1:1 match between payment and filtered candidate universe."""
        criteria = criteria or PartialMatchCriteria()

        # 1. Financial Sanity on Payment Effective Amount
        effective_amount = getattr(payment, "effective_amount", payment.amount)
        if effective_amount <= Decimal("0.00"):
            return PartialMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=PartialMatchStatus.NO_PARTIAL_MATCH,
                matched_candidate=None,
                competing_candidates=[],
                total_partial_candidates_found=0,
                reason_code=PartialMatchReasonCode.PAYMENT_INELIGIBLE,
                reason_description=f"Payment effective amount must be strictly positive. Got: {effective_amount}",
                is_universe_truncated=False,
                candidate_count_evaluated=0,
            )

        # 2. Check Upstream Universe Status Codes
        if universe.status_code == "CUSTOMER_UNRESOLVED":
            return PartialMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=None,
                status=PartialMatchStatus.NO_PARTIAL_MATCH,
                matched_candidate=None,
                competing_candidates=[],
                total_partial_candidates_found=0,
                reason_code=PartialMatchReasonCode.CUSTOMER_UNRESOLVED,
                reason_description="Payer customer could not be resolved from payment narration.",
                is_universe_truncated=False,
                candidate_count_evaluated=0,
            )

        if universe.status_code == "NOT_ELIGIBLE":
            return PartialMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=PartialMatchStatus.NO_PARTIAL_MATCH,
                matched_candidate=None,
                competing_candidates=[],
                total_partial_candidates_found=0,
                reason_code=PartialMatchReasonCode.PAYMENT_INELIGIBLE,
                reason_description="Payment is not eligible for reconciliation intake.",
                is_universe_truncated=False,
                candidate_count_evaluated=0,
            )

        if universe.status_code == "CURRENCY_MISMATCH":
            return PartialMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=PartialMatchStatus.NO_PARTIAL_MATCH,
                matched_candidate=None,
                competing_candidates=[],
                total_partial_candidates_found=0,
                reason_code=PartialMatchReasonCode.CURRENCY_MISMATCH,
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
            return PartialMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=PartialMatchStatus.NO_PARTIAL_MATCH,
                matched_candidate=None,
                competing_candidates=[],
                total_partial_candidates_found=0,
                reason_code=PartialMatchReasonCode.NO_RETAINED_CANDIDATES,
                reason_description="No open candidate invoices remained after candidate filtering.",
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=universe.total_evaluated,
            )

        # 4. Currency Compatibility Check
        norm_pay_curr = self._normalize_currency(payment.currency)
        if criteria.require_exact_currency:
            if not norm_pay_curr or not re.match(r"^[A-Z]{3}$", norm_pay_curr):
                return PartialMatchResult(
                    payment_id=payment.payment_id,
                    company_id=payment.company_id,
                    customer_id=universe.customer_id,
                    status=PartialMatchStatus.NO_PARTIAL_MATCH,
                    matched_candidate=None,
                    competing_candidates=[],
                    total_partial_candidates_found=0,
                    reason_code=PartialMatchReasonCode.CURRENCY_MISMATCH,
                    reason_description=f"Invalid or unsupported payment currency code: '{payment.currency}'",
                    is_universe_truncated=is_truncated,
                    candidate_count_evaluated=len(universe.retained_candidates),
                )

        # 5. Evaluate Partial Matches Over Retained Candidates
        matching_hypotheses: List[PartialMatchHypothesis] = []
        exact_candidates_count = 0
        overpayment_candidates_count = 0

        for candidate in universe.retained_candidates:
            # Currency assertion (defense-in-depth)
            norm_cand_curr = self._normalize_currency(candidate.currency)
            if criteria.require_exact_currency and norm_cand_curr != norm_pay_curr:
                continue

            # Invariant check: candidate balance must be positive
            if candidate.outstanding_amount <= Decimal("0.00"):
                continue

            # Check exact match boundary
            if effective_amount == candidate.outstanding_amount:
                exact_candidates_count += 1
            elif effective_amount < candidate.outstanding_amount:
                # Valid partial match candidate!
                signals = self._build_evidence_signals(
                    payment=payment,
                    candidate=candidate,
                    criteria=criteria,
                    effective_amount=effective_amount,
                )
                date_diff = abs((payment.payment_date - candidate.due_date).days)
                remaining = candidate.outstanding_amount - effective_amount
                hypothesis = PartialMatchHypothesis(
                    invoice_id=candidate.invoice_id,
                    invoice_number=candidate.invoice_number,
                    matched_amount=effective_amount,
                    invoice_outstanding_before=candidate.outstanding_amount,
                    invoice_outstanding_after=remaining,
                    payment_unallocated_before=effective_amount,
                    payment_unallocated_after=Decimal("0.00"),
                    currency=candidate.currency,
                    is_reference_match=candidate.is_reference_match,
                    date_difference_days=date_diff,
                    evidence_signals=signals,
                )
                matching_hypotheses.append(hypothesis)
            else:
                # effective_amount > candidate.outstanding_amount
                overpayment_candidates_count += 1

        # Enforce deterministic total order on matching hypotheses:
        # 1. is_reference_match DESC
        # 2. date_difference_days ASC
        # 3. invoice_number ASC
        # 4. invoice_id ASC
        matching_hypotheses.sort(
            key=lambda h: (
                not h.is_reference_match,
                h.date_difference_days,
                h.invoice_number,
                h.invoice_id.int,
            )
        )

        # 6. Check Excluded Candidates for Truncation Ambiguity
        # If candidate universe was truncated to max_candidates (K), verify whether any truncated
        # candidate also could have accepted this partial payment!
        truncated_partial_matches = 0
        if is_truncated:
            for excl in universe.excluded_candidates:
                if (
                    FilterExclusionReason.TRUNCATED_BY_LIMIT in excl.exclusion_reasons
                    and effective_amount < excl.outstanding_amount
                ):
                    truncated_partial_matches += 1

        total_partial_found = len(matching_hypotheses) + truncated_partial_matches

        # 7. Decisioning: Zero, Multiple, or Single Partial Match
        if total_partial_found == 0:
            diag_reason = PartialMatchReasonCode.NO_CANDIDATE_ELIGIBLE_FOR_PARTIAL
            diag_desc = (
                f"No candidate invoice has outstanding amount greater than payment amount "
                f"({effective_amount} {payment.currency})."
            )
            if exact_candidates_count > 0 and overpayment_candidates_count == 0:
                diag_reason = PartialMatchReasonCode.EXACT_MATCH_DETECTED
                diag_desc = (
                    f"Payment amount ({effective_amount} {payment.currency}) exactly equals "
                    f"candidate invoice balance (governed by Phase 14.5 Exact Matching)."
                )
            elif overpayment_candidates_count > 0 and exact_candidates_count == 0:
                diag_reason = PartialMatchReasonCode.OVERPAYMENT_DETECTED
                diag_desc = (
                    f"Payment amount ({effective_amount}) exceeds outstanding balance of "
                    f"all {overpayment_candidates_count} candidate invoices (potential multi-invoice or overpayment for Phase 14.7)."
                )

            return PartialMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=PartialMatchStatus.NO_PARTIAL_MATCH,
                matched_candidate=None,
                competing_candidates=[],
                total_partial_candidates_found=0,
                reason_code=diag_reason,
                reason_description=diag_desc,
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        if total_partial_found > 1:
            # Ambiguity detected: multiple candidates can accept this partial payment.
            # Must strictly be flagged for human review or downstream disambiguation.
            # No autonomous picking.
            reason_code = (
                PartialMatchReasonCode.TRUNCATED_UNIVERSE_AMBIGUITY
                if truncated_partial_matches > 0
                else PartialMatchReasonCode.MULTIPLE_PARTIAL_CANDIDATES
            )
            reason_desc = (
                f"Multiple candidate invoices ({total_partial_found}) can accept this "
                f"partial payment of {effective_amount} {payment.currency}. "
                f"Flagged for human review or downstream scoring."
            )
            return PartialMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=PartialMatchStatus.AMBIGUOUS_PARTIAL_MATCH,
                matched_candidate=None,
                competing_candidates=matching_hypotheses,
                total_partial_candidates_found=total_partial_found,
                reason_code=reason_code,
                reason_description=reason_desc,
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # 8. Exactly One Partial Match Found (total_partial_found == 1)
        # Check truncation risk: if universe was truncated and the single match lacks reference match
        if is_truncated and not matching_hypotheses[0].is_reference_match:
            return PartialMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=PartialMatchStatus.AMBIGUOUS_PARTIAL_MATCH,
                matched_candidate=None,
                competing_candidates=matching_hypotheses,
                total_partial_candidates_found=1,
                reason_code=PartialMatchReasonCode.TRUNCATED_UNIVERSE_AMBIGUITY,
                reason_description=(
                    f"Single partial candidate found, but candidate universe was truncated "
                    f"and candidate lacks explicit invoice number reference match to guarantee uniqueness."
                ),
                is_universe_truncated=True,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # Single unambiguous partial match!
        primary_match = matching_hypotheses[0]
        reason_code = (
            PartialMatchReasonCode.PARTIAL_AMOUNT_WITH_REFERENCE_MATCH
            if primary_match.is_reference_match
            else PartialMatchReasonCode.PARTIAL_AMOUNT_MATCH_FOUND
        )
        reason_desc = (
            f"Unique partial 1:1 match found for invoice {primary_match.invoice_number} "
            f"(applied: {primary_match.matched_amount}, remaining: {primary_match.invoice_outstanding_after} {payment.currency})."
        )
        return PartialMatchResult(
            payment_id=payment.payment_id,
            company_id=payment.company_id,
            customer_id=universe.customer_id,
            status=PartialMatchStatus.PARTIAL_MATCH,
            matched_candidate=primary_match,
            competing_candidates=[],
            total_partial_candidates_found=1,
            reason_code=reason_code,
            reason_description=reason_desc,
            is_universe_truncated=is_truncated,
            candidate_count_evaluated=len(universe.retained_candidates),
        )
