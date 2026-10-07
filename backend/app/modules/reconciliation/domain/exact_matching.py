"""Domain entities, value objects, evidence signals, and rule engine for exact 1:1 matching.

Phase 14.5 establishes deterministic one-payment-to-one-invoice matching on top of the
bounded, 11-gate-filtered candidate universe produced by Phase 14.4.

CRITICAL INVARIANTS:
1. Deterministic Systems Decide: 100% deterministic rules, zero LLM authority.
2. Authoritative Outstanding Amount: Compares payment.effective_amount == candidate.outstanding_amount.
   Never compares against original total amount if prior partial payments exist.
3. Zero Financial Mutation: Strictly in-memory evaluation producing a matching fact / hypothesis.
   Zero writes to invoices, payments, allocations, or ledgers.
4. Bounded & Incompleteness-Aware: Consumes Phase 14.4 FilteredCandidateUniverse without re-querying
   unbounded sets, while faithfully preserving truncation / incompleteness signals.
5. Strict Ambiguity Preservation: When multiple distinct candidates match the exact amount,
   the engine emits AMBIGUOUS_EXACT_MATCH without arbitrary tie-breaking.
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
from app.shared.domain.money import Money
from app.shared.exceptions import DomainError, FinancialInvariantError


class ExactMatchStatus(str, Enum):
    """Authoritative outcome of Phase 14.5 1:1 exact matching evaluation."""

    EXACT_MATCH = "EXACT_MATCH"
    NO_EXACT_MATCH = "NO_EXACT_MATCH"
    AMBIGUOUS_EXACT_MATCH = "AMBIGUOUS_EXACT_MATCH"


class ExactMatchReasonCode(str, Enum):
    """Authoritative machine-readable reason taxonomy for exact 1:1 matching."""

    EXACT_AMOUNT_MATCH_FOUND = "EXACT_AMOUNT_MATCH_FOUND"
    EXACT_AMOUNT_WITH_REFERENCE_MATCH = "EXACT_AMOUNT_WITH_REFERENCE_MATCH"
    NO_CANDIDATE_MATCHES_AMOUNT = "NO_CANDIDATE_MATCHES_AMOUNT"
    MULTIPLE_EXACT_AMOUNT_MATCHES = "MULTIPLE_EXACT_AMOUNT_MATCHES"
    TRUNCATED_UNIVERSE_AMBIGUITY = "TRUNCATED_UNIVERSE_AMBIGUITY"
    PARTIAL_PAYMENT_DETECTED = "PARTIAL_PAYMENT_DETECTED"
    OVERPAYMENT_DETECTED = "OVERPAYMENT_DETECTED"
    PAYMENT_INELIGIBLE = "PAYMENT_INELIGIBLE"
    CUSTOMER_UNRESOLVED = "CUSTOMER_UNRESOLVED"
    NO_RETAINED_CANDIDATES = "NO_RETAINED_CANDIDATES"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    ZERO_OUTSTANDING_BALANCE = "ZERO_OUTSTANDING_BALANCE"


class ExactMatchEvidenceType(str, Enum):
    """Categorization of evidence signals supporting an exact 1:1 match."""

    EXACT_AMOUNT_MATCH = "EXACT_AMOUNT_MATCH"
    INVOICE_NUMBER_MATCH = "INVOICE_NUMBER_MATCH"
    IDENTIFIER_MATCH = "IDENTIFIER_MATCH"
    CUSTOMER_NAME_MATCH = "CUSTOMER_NAME_MATCH"
    DATE_PROXIMITY_MATCH = "DATE_PROXIMITY_MATCH"


@dataclass(frozen=True)
class ExactMatchEvidenceSignal:
    """Structured, immutable atomic evidence unit supporting an exact match hypothesis."""

    evidence_type: ExactMatchEvidenceType
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
class ExactMatchCriteria:
    """Configurable evaluation criteria for exact 1:1 matching."""

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
class ExactMatchHypothesis:
    """Evaluated match pairing between a payment and an eligible candidate invoice.

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
    match_type: str = "EXACT_ONE_TO_ONE"
    is_reference_match: bool = False
    date_difference_days: int = 0
    evidence_signals: List[ExactMatchEvidenceSignal] = field(default_factory=list)

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
        if self.invoice_outstanding_before != self.matched_amount:
            raise FinancialInvariantError(
                f"Exact 1:1 match requires matched_amount ({self.matched_amount}) == "
                f"invoice_outstanding_before ({self.invoice_outstanding_before})"
            )
        if self.invoice_outstanding_after != Decimal("0.00"):
            raise FinancialInvariantError(
                f"Projected invoice outstanding after exact match must be 0.00. "
                f"Got: {self.invoice_outstanding_after}"
            )
        if self.payment_unallocated_after != Decimal("0.00"):
            raise FinancialInvariantError(
                f"Projected payment unallocated after exact match must be 0.00. "
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
class ExactMatchResult:
    """Authoritative outcome of Phase 14.5 exact 1:1 matching evaluation.

    Guarantees:
    - Pure in-memory diagnostic fact (zero accounting mutation).
    - Preserves distinction between unique, no match, and ambiguous exact match.
    - Preserves candidate universe completeness / truncation metadata.
    """

    payment_id: UUID
    company_id: UUID
    customer_id: Optional[UUID]
    status: ExactMatchStatus
    matched_candidate: Optional[ExactMatchHypothesis]
    competing_candidates: List[ExactMatchHypothesis]
    total_exact_candidates_found: int
    reason_code: ExactMatchReasonCode
    reason_description: str
    is_universe_truncated: bool
    candidate_count_evaluated: int
    is_deterministic: bool = True
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert exact match result to serializable dictionary."""
        return {
            "payment_id": str(self.payment_id),
            "company_id": str(self.company_id),
            "customer_id": str(self.customer_id) if self.customer_id else None,
            "status": self.status.value,
            "matched_candidate": (
                self.matched_candidate.to_dict() if self.matched_candidate else None
            ),
            "competing_candidates": [c.to_dict() for c in self.competing_candidates],
            "total_exact_candidates_found": self.total_exact_candidates_found,
            "reason_code": self.reason_code.value,
            "reason_description": self.reason_description,
            "is_universe_truncated": self.is_universe_truncated,
            "candidate_count_evaluated": self.candidate_count_evaluated,
            "is_deterministic": self.is_deterministic,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class ExactMatchRuleEngine:
    """Pure deterministic domain engine for Phase 14.5 exact 1:1 matching.

    Guarantees:
    1. Zero Financial Mutation: strictly read-only evaluation.
    2. Zero Floating-Point Arithmetic: strict Decimal comparisons.
    3. Authoritative Outstanding Amount: uses candidate.outstanding_amount.
    4. Deterministic Output: identical inputs produce identical results.
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
        criteria: ExactMatchCriteria,
    ) -> List[ExactMatchEvidenceSignal]:
        """Compute structured evidence signals supporting exact match hypothesis."""
        signals: List[ExactMatchEvidenceSignal] = []

        # Signal 1: Exact Amount Match (+30.0, STRONG)
        signals.append(
            ExactMatchEvidenceSignal(
                evidence_type=ExactMatchEvidenceType.EXACT_AMOUNT_MATCH,
                signal_strength=SignalStrength.STRONG,
                description=f"Payment unallocated amount equals invoice outstanding balance ({candidate.outstanding_amount} {candidate.currency}).",
                matched_value=str(candidate.outstanding_amount),
                source_field="outstanding_amount",
                weight=30.0,
            )
        )

        # Signal 2: Explicit Invoice Number Match (+35.0, STRONG)
        if candidate.is_reference_match:
            signals.append(
                ExactMatchEvidenceSignal(
                    evidence_type=ExactMatchEvidenceType.INVOICE_NUMBER_MATCH,
                    signal_strength=SignalStrength.STRONG,
                    description=f"Payment narration contains explicit invoice number '{candidate.invoice_number}'.",
                    matched_value=candidate.invoice_number,
                    source_field="raw_narration",
                    weight=35.0,
                )
            )

        # Signal 3: Customer Context Established (+20.0, MEDIUM)
        signals.append(
            ExactMatchEvidenceSignal(
                evidence_type=ExactMatchEvidenceType.CUSTOMER_NAME_MATCH,
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
                ExactMatchEvidenceSignal(
                    evidence_type=ExactMatchEvidenceType.DATE_PROXIMITY_MATCH,
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
        criteria: Optional[ExactMatchCriteria] = None,
    ) -> ExactMatchResult:
        """Evaluate exact 1:1 match between payment and filtered candidate universe."""
        criteria = criteria or ExactMatchCriteria()

        # 1. Financial Sanity on Payment Effective Amount
        effective_amount = getattr(payment, "effective_amount", payment.amount)
        if effective_amount <= Decimal("0.00"):
            return ExactMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=ExactMatchStatus.NO_EXACT_MATCH,
                matched_candidate=None,
                competing_candidates=[],
                total_exact_candidates_found=0,
                reason_code=ExactMatchReasonCode.PAYMENT_INELIGIBLE,
                reason_description=f"Payment effective amount must be strictly positive. Got: {effective_amount}",
                is_universe_truncated=False,
                candidate_count_evaluated=0,
            )

        # 2. Check Upstream Universe Status Codes
        if universe.status_code == "CUSTOMER_UNRESOLVED":
            return ExactMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=None,
                status=ExactMatchStatus.NO_EXACT_MATCH,
                matched_candidate=None,
                competing_candidates=[],
                total_exact_candidates_found=0,
                reason_code=ExactMatchReasonCode.CUSTOMER_UNRESOLVED,
                reason_description="Payer customer could not be resolved from payment narration.",
                is_universe_truncated=False,
                candidate_count_evaluated=0,
            )

        if universe.status_code == "NOT_ELIGIBLE":
            return ExactMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=ExactMatchStatus.NO_EXACT_MATCH,
                matched_candidate=None,
                competing_candidates=[],
                total_exact_candidates_found=0,
                reason_code=ExactMatchReasonCode.PAYMENT_INELIGIBLE,
                reason_description="Payment is not eligible for reconciliation intake.",
                is_universe_truncated=False,
                candidate_count_evaluated=0,
            )

        if universe.status_code == "CURRENCY_MISMATCH":
            return ExactMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=ExactMatchStatus.NO_EXACT_MATCH,
                matched_candidate=None,
                competing_candidates=[],
                total_exact_candidates_found=0,
                reason_code=ExactMatchReasonCode.CURRENCY_MISMATCH,
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
            return ExactMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=ExactMatchStatus.NO_EXACT_MATCH,
                matched_candidate=None,
                competing_candidates=[],
                total_exact_candidates_found=0,
                reason_code=ExactMatchReasonCode.NO_RETAINED_CANDIDATES,
                reason_description="No open candidate invoices remained after candidate filtering.",
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=universe.total_evaluated,
            )

        # 4. Currency Compatibility Check
        norm_pay_curr = self._normalize_currency(payment.currency)
        if criteria.require_exact_currency:
            if not norm_pay_curr or not re.match(r"^[A-Z]{3}$", norm_pay_curr):
                return ExactMatchResult(
                    payment_id=payment.payment_id,
                    company_id=payment.company_id,
                    customer_id=universe.customer_id,
                    status=ExactMatchStatus.NO_EXACT_MATCH,
                    matched_candidate=None,
                    competing_candidates=[],
                    total_exact_candidates_found=0,
                    reason_code=ExactMatchReasonCode.CURRENCY_MISMATCH,
                    reason_description=f"Invalid or unsupported payment currency code: '{payment.currency}'",
                    is_universe_truncated=is_truncated,
                    candidate_count_evaluated=len(universe.retained_candidates),
                )

        # 5. Evaluate Exact Amount Matches Over Retained Candidates
        matching_hypotheses: List[ExactMatchHypothesis] = []
        partial_candidates_count = 0
        overpayment_candidates_count = 0

        for candidate in universe.retained_candidates:
            # Currency assertion (defense-in-depth)
            norm_cand_curr = self._normalize_currency(candidate.currency)
            if criteria.require_exact_currency and norm_cand_curr != norm_pay_curr:
                continue

            # Invariant check: candidate balance must be positive
            if candidate.outstanding_amount <= Decimal("0.00"):
                continue

            diff = abs(effective_amount - candidate.outstanding_amount)
            if diff <= criteria.amount_tolerance:
                signals = self._build_evidence_signals(
                    payment=payment,
                    candidate=candidate,
                    criteria=criteria,
                )
                date_diff = abs((payment.payment_date - candidate.due_date).days)
                hypothesis = ExactMatchHypothesis(
                    invoice_id=candidate.invoice_id,
                    invoice_number=candidate.invoice_number,
                    matched_amount=candidate.outstanding_amount,
                    invoice_outstanding_before=candidate.outstanding_amount,
                    invoice_outstanding_after=Decimal("0.00"),
                    payment_unallocated_before=effective_amount,
                    payment_unallocated_after=Decimal("0.00"),
                    currency=candidate.currency,
                    is_reference_match=candidate.is_reference_match,
                    date_difference_days=date_diff,
                    evidence_signals=signals,
                )
                matching_hypotheses.append(hypothesis)
            elif effective_amount < candidate.outstanding_amount:
                partial_candidates_count += 1
            else:
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
        # candidate had the exact same outstanding amount as the payment!
        truncated_exact_matches = 0
        if is_truncated:
            for excl in universe.excluded_candidates:
                if (
                    FilterExclusionReason.TRUNCATED_BY_LIMIT in excl.exclusion_reasons
                    and abs(effective_amount - excl.outstanding_amount)
                    <= criteria.amount_tolerance
                ):
                    truncated_exact_matches += 1

        total_exact_found = len(matching_hypotheses) + truncated_exact_matches

        # 7. Preserving Ambiguity vs Disambiguation
        if total_exact_found == 0:
            diag_reason = ExactMatchReasonCode.NO_CANDIDATE_MATCHES_AMOUNT
            diag_desc = (
                f"No candidate invoice has outstanding amount equal to payment amount "
                f"({effective_amount} {payment.currency})."
            )
            if partial_candidates_count > 0 and overpayment_candidates_count == 0:
                diag_reason = ExactMatchReasonCode.PARTIAL_PAYMENT_DETECTED
                diag_desc = (
                    f"Payment amount ({effective_amount}) is less than outstanding balance of "
                    f"all {partial_candidates_count} candidate invoices (potential partial payment for Phase 14.6)."
                )
            elif overpayment_candidates_count > 0 and partial_candidates_count == 0:
                diag_reason = ExactMatchReasonCode.OVERPAYMENT_DETECTED
                diag_desc = (
                    f"Payment amount ({effective_amount}) exceeds outstanding balance of "
                    f"all {overpayment_candidates_count} candidate invoices (potential multi-invoice or overpayment for Phase 14.7)."
                )

            return ExactMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=ExactMatchStatus.NO_EXACT_MATCH,
                matched_candidate=None,
                competing_candidates=[],
                total_exact_candidates_found=0,
                reason_code=diag_reason,
                reason_description=diag_desc,
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        if total_exact_found > 1:
            # Ambiguity detected: multiple candidates have the exact same outstanding amount.
            # Under business rule §2.5 line 64, exact match logic must never automatically pick
            # between multiple candidates with the same amount. Multiple exact matches must strictly
            # be flagged for human review (AMBIGUOUS_EXACT_MATCH). No autonomous reference tie-breaking.
            reason_code = (
                ExactMatchReasonCode.TRUNCATED_UNIVERSE_AMBIGUITY
                if truncated_exact_matches > 0
                else ExactMatchReasonCode.MULTIPLE_EXACT_AMOUNT_MATCHES
            )
            reason_desc = (
                f"Multiple candidate invoices ({total_exact_found}) have the exact same "
                f"outstanding balance of {effective_amount} {payment.currency}. "
                f"Flagged for human review under business rules."
            )
            return ExactMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=ExactMatchStatus.AMBIGUOUS_EXACT_MATCH,
                matched_candidate=None,
                competing_candidates=matching_hypotheses,
                total_exact_candidates_found=total_exact_found,
                reason_code=reason_code,
                reason_description=reason_desc,
                is_universe_truncated=is_truncated,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # 8. Exactly One Match Found (total_exact_found == 1)
        # Check truncation risk: if universe was truncated and the single match lacks reference match
        if is_truncated and not matching_hypotheses[0].is_reference_match:
            # Incomplete universe risk: an un-retrieved candidate might also share this common balance
            return ExactMatchResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                customer_id=universe.customer_id,
                status=ExactMatchStatus.AMBIGUOUS_EXACT_MATCH,
                matched_candidate=None,
                competing_candidates=matching_hypotheses,
                total_exact_candidates_found=1,
                reason_code=ExactMatchReasonCode.TRUNCATED_UNIVERSE_AMBIGUITY,
                reason_description=(
                    f"Single exact amount candidate found, but candidate universe was truncated "
                    f"and candidate lacks explicit invoice number reference match to guarantee uniqueness."
                ),
                is_universe_truncated=True,
                candidate_count_evaluated=len(universe.retained_candidates),
            )

        # Single unambiguous exact match!
        primary_match = matching_hypotheses[0]
        reason_code = (
            ExactMatchReasonCode.EXACT_AMOUNT_WITH_REFERENCE_MATCH
            if primary_match.is_reference_match
            else ExactMatchReasonCode.EXACT_AMOUNT_MATCH_FOUND
        )
        reason_desc = (
            f"Unique exact 1:1 amount match found for invoice {primary_match.invoice_number} "
            f"({effective_amount} {payment.currency})."
        )
        return ExactMatchResult(
            payment_id=payment.payment_id,
            company_id=payment.company_id,
            customer_id=universe.customer_id,
            status=ExactMatchStatus.EXACT_MATCH,
            matched_candidate=primary_match,
            competing_candidates=[],
            total_exact_candidates_found=1,
            reason_code=reason_code,
            reason_description=reason_desc,
            is_universe_truncated=is_truncated,
            candidate_count_evaluated=len(universe.retained_candidates),
        )
