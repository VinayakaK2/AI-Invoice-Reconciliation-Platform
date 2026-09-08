"""Domain entities, value objects, and evidence signals for candidate invoice generation.

Phase 13.2 establishes deterministic evaluation, ranking, and bounded candidate universe
generation for open invoices belonging to an identified customer.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.modules.reconciliation.domain.entities import SignalStrength
from app.shared.exceptions import DomainError, FinancialInvariantError


class InvoiceEvidenceType(str, Enum):
    """Authoritative taxonomy of deterministic evidence signals for candidate invoices."""

    INVOICE_NUMBER_MATCH = "INVOICE_NUMBER_MATCH"
    EXACT_AMOUNT_MATCH = "EXACT_AMOUNT_MATCH"
    EXACT_ORIGINAL_AMOUNT_MATCH = "EXACT_ORIGINAL_AMOUNT_MATCH"
    PARTIAL_AMOUNT_COMPATIBLE = "PARTIAL_AMOUNT_COMPATIBLE"
    DATE_RELEVANCE = "DATE_RELEVANCE"


@dataclass(frozen=True)
class CandidateInvoiceEvidenceSignal:
    """Immutable atomic evidence unit explaining why an invoice is a candidate."""

    evidence_type: InvoiceEvidenceType
    signal_strength: SignalStrength
    matched_value: str
    source_field: str
    weight: float
    confidence_delta: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert signal to serializable dictionary."""
        return {
            "evidence_type": self.evidence_type.value,
            "signal_strength": self.signal_strength.value,
            "matched_value": self.matched_value,
            "source_field": self.source_field,
            "weight": self.weight,
            "confidence_delta": self.confidence_delta,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class CandidateInvoice:
    """Evaluated candidate invoice with deterministic retrieval ranking.

    CRITICAL DOMAIN INVARIANT:
    `retrieval_priority` is a deterministic ranking heuristic for candidate universe
    ordering and truncation. It is STRICTLY NOT a reconciliation match confidence score!
    Authoritative match confidence calculation and allocation are deferred to Phase 13.3+.
    """

    invoice_id: UUID
    invoice_number: str
    total_amount: Decimal
    paid_amount: Decimal
    outstanding_amount: Decimal
    currency: str
    issue_date: date
    due_date: date
    status: str  # PENDING or PARTIALLY_PAID
    retrieval_priority: float  # 0.0 to 100.0 (deterministic sort score)
    evidence_signals: List[CandidateInvoiceEvidenceSignal]
    is_exact_amount_match: bool
    is_partial_amount_match: bool
    is_reference_match: bool
    rank: int = 1

    def __post_init__(self) -> None:
        """Enforce domain invariants upon initialization."""
        if self.outstanding_amount <= Decimal("0.00"):
            raise FinancialInvariantError(
                f"Candidate invoice {self.invoice_number} must have positive outstanding balance. "
                f"Received: {self.outstanding_amount}"
            )
        if self.paid_amount + self.outstanding_amount != self.total_amount:
            raise FinancialInvariantError(
                f"Candidate invoice {self.invoice_number} violates balance conservation: "
                f"paid ({self.paid_amount}) + outstanding ({self.outstanding_amount}) != "
                f"total ({self.total_amount})"
            )
        if self.status not in ("PENDING", "PARTIALLY_PAID"):
            raise DomainError(
                f"Invalid candidate invoice status '{self.status}'. "
                "Only PENDING or PARTIALLY_PAID invoices can be candidates."
            )
        if not (0.0 <= self.retrieval_priority <= 100.0):
            raise DomainError(
                f"Retrieval priority must be between 0.0 and 100.0. Got: {self.retrieval_priority}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Convert candidate invoice to serializable dictionary."""
        return {
            "invoice_id": str(self.invoice_id),
            "invoice_number": self.invoice_number,
            "total_amount": str(self.total_amount),
            "paid_amount": str(self.paid_amount),
            "outstanding_amount": str(self.outstanding_amount),
            "currency": self.currency,
            "issue_date": self.issue_date.isoformat(),
            "due_date": self.due_date.isoformat(),
            "status": self.status,
            "retrieval_priority": round(self.retrieval_priority, 2),
            "evidence_signals": [s.to_dict() for s in self.evidence_signals],
            "is_exact_amount_match": self.is_exact_amount_match,
            "is_partial_amount_match": self.is_partial_amount_match,
            "is_reference_match": self.is_reference_match,
            "rank": self.rank,
        }


@dataclass(frozen=True)
class CandidateInvoiceUniverse:
    """Bounded, deterministically ranked universe of candidate invoices for a payment.

    Guarantees tenant isolation, explicit truncation metrics, and determinism.
    """

    payment_id: UUID
    company_id: UUID
    customer_id: Optional[UUID]
    candidates: List[CandidateInvoice]
    total_eligible_invoices: int
    truncated: bool
    candidate_limit: int
    truncation_reason: Optional[str]
    is_deterministic: bool = True
    currency_mismatches_detected: int = 0
    status_code: str = "SUCCESS"  # SUCCESS, NO_ELIGIBLE_INVOICES, CURRENCY_MISMATCH, CUSTOMER_UNRESOLVED, NOT_ELIGIBLE
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert candidate universe result to serializable dictionary."""
        return {
            "payment_id": str(self.payment_id),
            "company_id": str(self.company_id),
            "customer_id": str(self.customer_id) if self.customer_id else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "total_eligible_invoices": self.total_eligible_invoices,
            "truncated": self.truncated,
            "candidate_limit": self.candidate_limit,
            "truncation_reason": self.truncation_reason,
            "is_deterministic": self.is_deterministic,
            "currency_mismatches_detected": self.currency_mismatches_detected,
            "status_code": self.status_code,
            "evaluated_at": self.evaluated_at.isoformat(),
        }
