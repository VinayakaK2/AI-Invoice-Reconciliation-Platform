"""Domain entities, criteria, exclusion taxonomy, and rule engine for candidate invoice filtering.

Phase 14.4 establishes deterministic winnowing of generated candidate invoices,
enforcing multi-tenant isolation, customer boundaries, deduplication, currency compatibility,
receivable lifecycle invariants, monetary balance conservation, and temporal causality policies
prior to combinatorial matching (Phases 14.5–14.8).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from app.modules.reconciliation.domain.candidate_invoices import (
    CandidateInvoice,
    CandidateInvoiceUniverse,
)
from app.modules.reconciliation.domain.invoice_rules import InvoiceCandidateContext
from app.shared.exceptions import DomainError, FinancialInvariantError


class FilterExclusionReason(str, Enum):
    """Authoritative taxonomy of deterministic exclusion reason codes for candidate filtering."""

    TENANT_MISMATCH = "TENANT_MISMATCH"
    CUSTOMER_MISMATCH = "CUSTOMER_MISMATCH"
    CUSTOMER_ARCHIVED = "CUSTOMER_ARCHIVED"
    ARCHIVED_INVOICE = "ARCHIVED_INVOICE"
    DUPLICATE_CANDIDATE = "DUPLICATE_CANDIDATE"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    STATUS_INELIGIBLE = "STATUS_INELIGIBLE"
    ZERO_OUTSTANDING_BALANCE = "ZERO_OUTSTANDING_BALANCE"
    NEGATIVE_OUTSTANDING_BALANCE = "NEGATIVE_OUTSTANDING_BALANCE"
    BALANCE_CONSERVATION_BREACH = "BALANCE_CONSERVATION_BREACH"
    NON_POSITIVE_TOTAL_AMOUNT = "NON_POSITIVE_TOTAL_AMOUNT"
    NON_CAUSAL_DATE = "NON_CAUSAL_DATE"
    DATE_OUT_OF_WINDOW = "DATE_OUT_OF_WINDOW"
    AMOUNT_BELOW_MINIMUM = "AMOUNT_BELOW_MINIMUM"
    AMOUNT_ABOVE_MAXIMUM = "AMOUNT_ABOVE_MAXIMUM"
    EXCEEDS_PAYMENT_AMOUNT = "EXCEEDS_PAYMENT_AMOUNT"
    REFERENCE_MISMATCH = "REFERENCE_MISMATCH"
    TRUNCATED_BY_LIMIT = "TRUNCATED_BY_LIMIT"


@dataclass(frozen=True)
class CandidateFilterCriteria:
    """Configurable criteria parameters for candidate invoice filtering.

    Defaults enforce accounting best practices:
    - Only PENDING and PARTIALLY_PAID invoices.
    - Strict temporal causality (payment date on or after invoice issue date).
    - 365-day historical lookback window (unless an explicit reference match is present).
    - Max 30 candidates to prevent downstream combinatorial explosion.
    """

    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    max_lookback_days: Optional[int] = 365
    max_advance_days: int = 0
    require_causality: bool = True
    allowed_statuses: Set[str] = field(
        default_factory=lambda: {"PENDING", "PARTIALLY_PAID"}
    )
    require_reference_match: bool = False
    disallow_overpayment: bool = False
    allow_expired_debt_with_reference: bool = True
    max_candidates: int = 30

    def __post_init__(self) -> None:
        """Enforce configuration validity invariants."""
        if self.min_amount is not None and self.min_amount < Decimal("0.00"):
            raise DomainError(f"min_amount must be non-negative. Got: {self.min_amount}")
        if self.max_amount is not None and self.max_amount < Decimal("0.00"):
            raise DomainError(f"max_amount must be non-negative. Got: {self.max_amount}")
        if (
            self.min_amount is not None
            and self.max_amount is not None
            and self.min_amount > self.max_amount
        ):
            raise DomainError(
                f"min_amount ({self.min_amount}) cannot exceed max_amount ({self.max_amount})."
            )
        if self.max_candidates < 1 or self.max_candidates > 100:
            raise DomainError(
                f"max_candidates must be between 1 and 100. Got: {self.max_candidates}"
            )
        if self.max_advance_days < 0:
            raise DomainError(
                f"max_advance_days must be non-negative. Got: {self.max_advance_days}"
            )


@dataclass(frozen=True)
class ExcludedCandidateInvoice:
    """Immutable audit record explaining why a candidate invoice was filtered out."""

    invoice_id: UUID
    invoice_number: str
    outstanding_amount: Decimal
    currency: str
    issue_date: date
    due_date: date
    exclusion_reasons: List[FilterExclusionReason]
    diagnostic_details: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert excluded candidate record to serializable dictionary."""
        return {
            "invoice_id": str(self.invoice_id),
            "invoice_number": self.invoice_number,
            "outstanding_amount": str(self.outstanding_amount),
            "currency": self.currency,
            "issue_date": self.issue_date.isoformat(),
            "due_date": self.due_date.isoformat(),
            "exclusion_reasons": [r.value for r in self.exclusion_reasons],
            "diagnostic_details": self.diagnostic_details,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class FilteredCandidateUniverse:
    """Bounded, deterministically filtered universe of candidate invoices.

    Guarantees:
    - Zero financial accounting mutation (Rule 4).
    - Complete machine-readable audit trail of retained and excluded candidates.
    - Strict total ordering and rank monotonicity.
    """

    payment_id: UUID
    company_id: UUID
    customer_id: Optional[UUID]
    retained_candidates: List[CandidateInvoice]
    excluded_candidates: List[ExcludedCandidateInvoice]
    filter_criteria: CandidateFilterCriteria
    total_evaluated: int
    total_retained: int
    total_excluded: int
    currency_mismatches_detected: int
    exclusion_breakdown: Dict[str, int]
    is_deterministic: bool = True
    status_code: str = "SUCCESS"  # SUCCESS, NO_ELIGIBLE_INVOICES, ALL_EXCLUDED, CURRENCY_MISMATCH, CUSTOMER_UNRESOLVED, NOT_ELIGIBLE
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert filtered candidate universe to serializable dictionary."""
        return {
            "payment_id": str(self.payment_id),
            "company_id": str(self.company_id),
            "customer_id": str(self.customer_id) if self.customer_id else None,
            "retained_candidates": [c.to_dict() for c in self.retained_candidates],
            "excluded_candidates": [e.to_dict() for e in self.excluded_candidates],
            "filter_criteria": {
                "min_amount": str(self.filter_criteria.min_amount)
                if self.filter_criteria.min_amount is not None
                else None,
                "max_amount": str(self.filter_criteria.max_amount)
                if self.filter_criteria.max_amount is not None
                else None,
                "max_lookback_days": self.filter_criteria.max_lookback_days,
                "max_advance_days": self.filter_criteria.max_advance_days,
                "require_causality": self.filter_criteria.require_causality,
                "allowed_statuses": sorted(list(self.filter_criteria.allowed_statuses)),
                "require_reference_match": self.filter_criteria.require_reference_match,
                "disallow_overpayment": self.filter_criteria.disallow_overpayment,
                "max_candidates": self.filter_criteria.max_candidates,
            },
            "total_evaluated": self.total_evaluated,
            "total_retained": self.total_retained,
            "total_excluded": self.total_excluded,
            "currency_mismatches_detected": self.currency_mismatches_detected,
            "exclusion_breakdown": self.exclusion_breakdown,
            "is_deterministic": self.is_deterministic,
            "status_code": self.status_code,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class CandidateFilterRuleEngine:
    """Pure deterministic rule engine for candidate invoice filtering.

    CRITICAL GUARANTEES:
    1. Zero Financial Mutation: 100% read-only evaluation.
    2. Zero LLM Authority: 100% deterministic rules and exact Decimal math.
    3. Fail-Closed Waterfall Pipeline: Rejection order protects ledger security and auditability.
    """

    @staticmethod
    def _normalize_currency(currency: Optional[str]) -> str:
        """Normalize currency string removing whitespace and converting to uppercase."""
        if not currency or not isinstance(currency, str):
            return ""
        return currency.strip().upper()

    @classmethod
    def evaluate_candidate_eligibility(
        cls,
        candidate: CandidateInvoice,
        payment_company_id: UUID,
        payment_currency: str,
        payment_date: date,
        payment_effective_amount: Decimal,
        criteria: CandidateFilterCriteria,
        resolved_customer_id: Optional[UUID] = None,
        candidate_company_id: Optional[UUID] = None,
        candidate_customer_id: Optional[UUID] = None,
        is_archived: bool = False,
        customer_is_archived: bool = False,
    ) -> List[FilterExclusionReason]:
        """Evaluate a single candidate against the fail-closed waterfall pipeline.

        Returns an ordered list of exclusion reasons. An empty list signifies candidate acceptance.
        """
        reasons: List[FilterExclusionReason] = []

        # 1. Gate 1: Tenant Isolation (Fail-closed IDOR protection)
        if candidate_company_id is not None and candidate_company_id != payment_company_id:
            reasons.append(FilterExclusionReason.TENANT_MISMATCH)

        # 2. Gate 2: Customer Identity Isolation
        if (
            resolved_customer_id is not None
            and candidate_customer_id is not None
            and candidate_customer_id != resolved_customer_id
        ):
            reasons.append(FilterExclusionReason.CUSTOMER_MISMATCH)

        # 3. Gate 3: Customer Archival Status
        if customer_is_archived:
            reasons.append(FilterExclusionReason.CUSTOMER_ARCHIVED)

        # 4. Gate 4: Invoice Archival Status
        if is_archived:
            reasons.append(FilterExclusionReason.ARCHIVED_INVOICE)

        # 5. Gate 5: Currency Compatibility & Format
        norm_inv_curr = cls._normalize_currency(candidate.currency)
        norm_pay_curr = cls._normalize_currency(payment_currency)
        if not norm_inv_curr or not re.match(r"^[A-Z]{3}$", norm_inv_curr) or norm_inv_curr != norm_pay_curr:
            reasons.append(FilterExclusionReason.CURRENCY_MISMATCH)

        # 6. Gate 6: Receivable Lifecycle Status
        if candidate.status not in criteria.allowed_statuses:
            reasons.append(FilterExclusionReason.STATUS_INELIGIBLE)

        # 7. Gate 7: Financial Balance Invariants
        if candidate.total_amount <= Decimal("0.00"):
            reasons.append(FilterExclusionReason.NON_POSITIVE_TOTAL_AMOUNT)
        if candidate.outstanding_amount < Decimal("0.00"):
            reasons.append(FilterExclusionReason.NEGATIVE_OUTSTANDING_BALANCE)
        elif candidate.outstanding_amount == Decimal("0.00"):
            reasons.append(FilterExclusionReason.ZERO_OUTSTANDING_BALANCE)
        if candidate.paid_amount + candidate.outstanding_amount != candidate.total_amount:
            reasons.append(FilterExclusionReason.BALANCE_CONSERVATION_BREACH)

        # 8. Gate 8: Amount Policy Boundaries
        if criteria.min_amount is not None and candidate.outstanding_amount < criteria.min_amount:
            reasons.append(FilterExclusionReason.AMOUNT_BELOW_MINIMUM)
        if criteria.max_amount is not None and candidate.outstanding_amount > criteria.max_amount:
            reasons.append(FilterExclusionReason.AMOUNT_ABOVE_MAXIMUM)
        if criteria.disallow_overpayment and candidate.outstanding_amount > payment_effective_amount:
            reasons.append(FilterExclusionReason.EXCEEDS_PAYMENT_AMOUNT)

        # 9. Gate 9: Temporal Policy & Causality
        # Inverted dates check: due_date < issue_date
        if candidate.due_date < candidate.issue_date:
            reasons.append(FilterExclusionReason.NON_CAUSAL_DATE)
        else:
            # Causality check: payment_date vs issue_date
            if criteria.require_causality:
                if candidate.issue_date > payment_date:
                    days_advance = (candidate.issue_date - payment_date).days
                    if days_advance > criteria.max_advance_days:
                        reasons.append(FilterExclusionReason.NON_CAUSAL_DATE)

            # Historical lookback check
            if criteria.max_lookback_days is not None:
                days_old = (payment_date - candidate.issue_date).days
                if days_old > criteria.max_lookback_days:
                    # Allow exception if explicit reference match exists and policy allows it
                    if not (criteria.allow_expired_debt_with_reference and candidate.is_reference_match):
                        reasons.append(FilterExclusionReason.DATE_OUT_OF_WINDOW)

        # 10. Gate 10: Reference Match Requirement
        if criteria.require_reference_match and not candidate.is_reference_match:
            reasons.append(FilterExclusionReason.REFERENCE_MISMATCH)

        return reasons

    def filter_universe(
        self,
        universe: CandidateInvoiceUniverse,
        payment_currency: str,
        payment_date: date,
        payment_effective_amount: Decimal,
        criteria: Optional[CandidateFilterCriteria] = None,
        customer_is_archived: bool = False,
    ) -> FilteredCandidateUniverse:
        """Deterministically filter a CandidateInvoiceUniverse into a FilteredCandidateUniverse."""
        criteria = criteria or CandidateFilterCriteria()

        retained_candidates: List[CandidateInvoice] = []
        excluded_candidates: List[ExcludedCandidateInvoice] = []
        seen_invoice_ids: Set[UUID] = set()
        currency_mismatches_detected = universe.currency_mismatches_detected
        exclusion_breakdown: Dict[str, int] = {}

        for candidate in universe.candidates:
            # Check Deduplication Gate first
            if candidate.invoice_id in seen_invoice_ids:
                dup_record = ExcludedCandidateInvoice(
                    invoice_id=candidate.invoice_id,
                    invoice_number=candidate.invoice_number,
                    outstanding_amount=candidate.outstanding_amount,
                    currency=candidate.currency,
                    issue_date=candidate.issue_date,
                    due_date=candidate.due_date,
                    exclusion_reasons=[FilterExclusionReason.DUPLICATE_CANDIDATE],
                    diagnostic_details="Duplicate candidate invoice instance pruned from candidate set.",
                )
                excluded_candidates.append(dup_record)
                code_str = FilterExclusionReason.DUPLICATE_CANDIDATE.value
                exclusion_breakdown[code_str] = exclusion_breakdown.get(code_str, 0) + 1
                continue

            seen_invoice_ids.add(candidate.invoice_id)

            # Extract candidate-level identity attributes if present (defense-in-depth on duck-typed objects)
            cand_company_id = getattr(candidate, "company_id", universe.company_id)
            cand_customer_id = getattr(candidate, "customer_id", universe.customer_id)
            cand_is_archived = getattr(candidate, "is_archived", False)

            # Evaluate full waterfall pipeline
            reasons = self.evaluate_candidate_eligibility(
                candidate=candidate,
                payment_company_id=universe.company_id,
                payment_currency=payment_currency,
                payment_date=payment_date,
                payment_effective_amount=payment_effective_amount,
                criteria=criteria,
                resolved_customer_id=universe.customer_id,
                candidate_company_id=cand_company_id,
                candidate_customer_id=cand_customer_id,
                is_archived=cand_is_archived,
                customer_is_archived=customer_is_archived,
            )

            if reasons:
                # Increment exclusion breakdown counts
                for r in reasons:
                    r_val = r.value
                    exclusion_breakdown[r_val] = exclusion_breakdown.get(r_val, 0) + 1
                    if r == FilterExclusionReason.CURRENCY_MISMATCH:
                        currency_mismatches_detected += 1

                diag_str = f"Candidate excluded by filter gates: {', '.join(r.value for r in reasons)}"
                excluded_candidates.append(
                    ExcludedCandidateInvoice(
                        invoice_id=candidate.invoice_id,
                        invoice_number=candidate.invoice_number,
                        outstanding_amount=candidate.outstanding_amount,
                        currency=candidate.currency,
                        issue_date=candidate.issue_date,
                        due_date=candidate.due_date,
                        exclusion_reasons=reasons,
                        diagnostic_details=diag_str,
                    )
                )
            else:
                retained_candidates.append(candidate)

        # Enforce 5-key deterministic sorting prior to bounding:
        # 1. retrieval_priority DESC
        # 2. is_exact_amount_match DESC
        # 3. is_reference_match DESC
        # 4. due_date ASC (FIFO preference)
        # 5. invoice_id ASC (Canonical tie-breaker)
        retained_candidates.sort(
            key=lambda c: (
                round(c.retrieval_priority, 4),
                c.is_exact_amount_match,
                c.is_reference_match,
                -c.due_date.toordinal(),
                -c.invoice_id.int,
            ),
            reverse=True,
        )

        # Enforce max_candidates bounding
        if len(retained_candidates) > criteria.max_candidates:
            overflow = retained_candidates[criteria.max_candidates :]
            retained_candidates = retained_candidates[: criteria.max_candidates]

            trunc_code = FilterExclusionReason.TRUNCATED_BY_LIMIT.value
            for c in overflow:
                exclusion_breakdown[trunc_code] = (
                    exclusion_breakdown.get(trunc_code, 0) + 1
                )
                excluded_candidates.append(
                    ExcludedCandidateInvoice(
                        invoice_id=c.invoice_id,
                        invoice_number=c.invoice_number,
                        outstanding_amount=c.outstanding_amount,
                        currency=c.currency,
                        issue_date=c.issue_date,
                        due_date=c.due_date,
                        exclusion_reasons=[FilterExclusionReason.TRUNCATED_BY_LIMIT],
                        diagnostic_details=(
                            f"Truncated to satisfy max_candidates limit of {criteria.max_candidates}."
                        ),
                    )
                )

        # Enforce deterministic total order on excluded candidates:
        # 1. due_date ASC (earlier due date first)
        # 2. outstanding_amount DESC (larger debt first)
        # 3. invoice_number ASC (alphabetical tie-breaker)
        # 4. invoice_id ASC (canonical UUID integer tie-breaker)
        excluded_candidates.sort(
            key=lambda e: (
                e.due_date.toordinal(),
                -e.outstanding_amount,
                e.invoice_number,
                e.invoice_id.int,
            )
        )

        # Re-assign sequential monotonic ranks 1..N
        ranked_retained = [
            CandidateInvoice(
                invoice_id=c.invoice_id,
                invoice_number=c.invoice_number,
                total_amount=c.total_amount,
                paid_amount=c.paid_amount,
                outstanding_amount=c.outstanding_amount,
                currency=c.currency,
                issue_date=c.issue_date,
                due_date=c.due_date,
                status=c.status,
                retrieval_priority=c.retrieval_priority,
                evidence_signals=c.evidence_signals,
                is_exact_amount_match=c.is_exact_amount_match,
                is_partial_amount_match=c.is_partial_amount_match,
                is_reference_match=c.is_reference_match,
                rank=idx + 1,
            )
            for idx, c in enumerate(retained_candidates)
        ]

        # Determine diagnostic status code
        if ranked_retained:
            status_code = "SUCCESS"
        elif universe.status_code in ("NOT_ELIGIBLE", "CUSTOMER_UNRESOLVED"):
            status_code = universe.status_code
        elif currency_mismatches_detected > 0 and not retained_candidates:
            status_code = "CURRENCY_MISMATCH"
        elif excluded_candidates:
            status_code = "ALL_EXCLUDED"
        else:
            status_code = "NO_ELIGIBLE_INVOICES"

        return FilteredCandidateUniverse(
            payment_id=universe.payment_id,
            company_id=universe.company_id,
            customer_id=universe.customer_id,
            retained_candidates=ranked_retained,
            excluded_candidates=excluded_candidates,
            filter_criteria=criteria,
            total_evaluated=len(universe.candidates),
            total_retained=len(ranked_retained),
            total_excluded=len(excluded_candidates),
            currency_mismatches_detected=currency_mismatches_detected,
            exclusion_breakdown=exclusion_breakdown,
            status_code=status_code,
            is_deterministic=True,
        )

    def filter_contexts(
        self,
        invoices: List[InvoiceCandidateContext],
        payment_company_id: UUID,
        payment_currency: str,
        payment_date: date,
        payment_effective_amount: Decimal,
        criteria: Optional[CandidateFilterCriteria] = None,
        resolved_customer_id: Optional[UUID] = None,
        customer_is_archived: bool = False,
    ) -> List[InvoiceCandidateContext]:
        """Fast filter over raw InvoiceCandidateContext objects before full candidate scoring.

        Used for database pre-filtering verification and high-performance list pruning.
        """
        criteria = criteria or CandidateFilterCriteria()
        accepted: List[InvoiceCandidateContext] = []
        seen_ids: Set[UUID] = set()

        for inv in invoices:
            if inv.id in seen_ids:
                continue
            seen_ids.add(inv.id)

            # Gate 1: Tenant Isolation
            if inv.company_id != payment_company_id:
                continue

            # Gate 2: Customer Isolation
            if resolved_customer_id is not None and inv.customer_id != resolved_customer_id:
                continue

            # Gate 3 & 4: Archival
            if customer_is_archived or inv.is_archived:
                continue

            # Gate 5: Currency
            norm_inv = self._normalize_currency(inv.currency)
            norm_pay = self._normalize_currency(payment_currency)
            if not norm_inv or norm_inv != norm_pay:
                continue

            # Gate 6: Status
            if inv.status not in criteria.allowed_statuses:
                continue

            # Gate 7: Balance Invariants
            if inv.total_amount <= Decimal("0.00") or inv.outstanding_amount <= Decimal("0.00"):
                continue
            if inv.paid_amount + inv.outstanding_amount != inv.total_amount:
                continue

            # Gate 8: Amount Bounds
            if criteria.min_amount is not None and inv.outstanding_amount < criteria.min_amount:
                continue
            if criteria.max_amount is not None and inv.outstanding_amount > criteria.max_amount:
                continue
            if criteria.disallow_overpayment and inv.outstanding_amount > payment_effective_amount:
                continue

            # Gate 9: Temporal Policy
            if inv.due_date < inv.issue_date:
                continue
            if criteria.require_causality and inv.issue_date > payment_date:
                if (inv.issue_date - payment_date).days > criteria.max_advance_days:
                    continue
            if criteria.max_lookback_days is not None:
                if (payment_date - inv.issue_date).days > criteria.max_lookback_days:
                    continue

            accepted.append(inv)

            if len(accepted) >= criteria.max_candidates:
                break

        return accepted
