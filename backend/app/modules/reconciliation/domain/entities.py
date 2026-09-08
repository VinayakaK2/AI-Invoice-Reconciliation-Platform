"""Domain entities and value objects for payment counterparty identification."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID


class IdentificationStatus(str, Enum):
    """Authoritative classification of counterparty payer identification."""

    IDENTIFIED = "IDENTIFIED"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING = "CONFLICTING"
    UNKNOWN = "UNKNOWN"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


class EvidenceType(str, Enum):
    """Categorization of evidence used to establish counterparty context."""

    EXACT_BANK_ACCOUNT = "EXACT_BANK_ACCOUNT"
    EXACT_UPI_VPA = "EXACT_UPI_VPA"
    EXACT_VIRTUAL_ACCOUNT = "EXACT_VIRTUAL_ACCOUNT"
    EXACT_ALIAS = "EXACT_ALIAS"
    NORMALIZED_LEGAL_NAME = "NORMALIZED_LEGAL_NAME"
    PAYER_RAW_NAME_EXACT = "PAYER_RAW_NAME_EXACT"
    NARRATION_TOKEN_OVERLAP = "NARRATION_TOKEN_OVERLAP"
    REFERENCE_MATCH = "REFERENCE_MATCH"


class SignalStrength(str, Enum):
    """Strength level of an individual evidence signal."""

    STRONG = "STRONG"
    MEDIUM = "MEDIUM"
    WEAK = "WEAK"


@dataclass(frozen=True)
class EvidenceSignal:
    """Structured, immutable evidence atomic unit."""

    evidence_type: EvidenceType
    signal_strength: SignalStrength
    matched_value: str
    source_field: str
    weight: float
    confidence_delta: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert evidence signal to serializable dictionary."""
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
class CustomerMatchCandidate:
    """Evaluated customer candidate with matched signals and score."""

    customer_id: UUID
    customer_name: str
    composite_score: float
    evidence_signals: List[EvidenceSignal] = field(default_factory=list)
    rank: int = 1

    def to_dict(self) -> Dict[str, Any]:
        """Convert candidate to serializable dictionary."""
        return {
            "customer_id": str(self.customer_id),
            "customer_name": self.customer_name,
            "composite_score": round(self.composite_score, 2),
            "evidence_signals": [s.to_dict() for s in self.evidence_signals],
            "rank": self.rank,
        }


@dataclass(frozen=True)
class CustomerIdentificationResult:
    """Authoritative outcome of counterparty identification evaluation."""

    payment_id: UUID
    company_id: UUID
    status: IdentificationStatus
    primary_candidate: Optional[CustomerMatchCandidate]
    candidates: List[CustomerMatchCandidate]
    evidence_signals: List[EvidenceSignal]
    total_evidence_score: float
    reason_code: str
    reason_description: str
    is_deterministic: bool = True
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to serializable dictionary."""
        return {
            "payment_id": str(self.payment_id),
            "company_id": str(self.company_id),
            "status": self.status.value,
            "primary_candidate": (
                self.primary_candidate.to_dict() if self.primary_candidate else None
            ),
            "candidates": [c.to_dict() for c in self.candidates],
            "evidence_signals": [s.to_dict() for s in self.evidence_signals],
            "total_evidence_score": round(self.total_evidence_score, 2),
            "reason_code": self.reason_code,
            "reason_description": self.reason_description,
            "is_deterministic": self.is_deterministic,
            "evaluated_at": self.evaluated_at.isoformat(),
        }
