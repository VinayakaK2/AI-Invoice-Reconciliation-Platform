"""Domain entities, value objects, canonical policy, and deterministic engine for Phase 14.12 Confidence Engine.

Phase 14.12 establishes an objective, bounded, deterministic confidence assessment layer that consumes
upstream Phase 14.11 MatchingScoringResult and Phase 14.10 EvidenceNormalizationResult and produces
a canonical confidence evaluation (0.00 to 100.00) for each reconciliation candidate.

CRITICAL DOMAIN INVARIANTS:
1. Separation of Concerns:
   Evidence ≠ Score ≠ Confidence ≠ Decision ≠ Approval ≠ Financial Mutation.
2. Confidence is a deterministic evidence assessment; it is strictly NOT automatically
   a calibrated statistical probability of correctness (is_statistically_calibrated=False),
   and it is NOT a financial disposition or decision (deferred strictly to Phase 14.13).
3. Observed Financial Mutation: NONE (Strictly read-only domain evaluation).
4. Pure Determinism: 100% deterministic, zero LLM authority, zero randomness, zero floating-point math.
   Uses exact Python Decimal for all calculations.
5. Mathematical Bounds: 0.00 <= confidence_score <= 100.00.
6. Anti-Double Counting: Base score from Phase 14.11 represents the aggregated evidence weight.
   Phase 14.12 does NOT recompute or double-award base signal weights.
7. Semantic Honesty & Direct Grounding:
   - Direct evidence presence is surfaced factually (e.g. Rule P-1 bank account / UPI VPA match).
   - Conflicting evidence presence is surfaced factually from upstream normalizer & scoring.
   - Zero invented penalties, ceilings, or dampening formulas.
8. Versioning & Provenance: Every assessment records rule_version ("1.0.0"), scoring_algorithm_version,
   and confidence_algorithm_version ("14.12.0").
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.modules.reconciliation.domain.evidence_normalization import (
    EvidenceNormalizationResult,
    NormalizedCandidateBundle,
    NormalizedPaymentEvidenceContext,
)
from app.modules.reconciliation.domain.matching_scoring import (
    CandidateScoreResult,
    MatchingScoringResult,
    ScoringSignalType,
)
from app.shared.exceptions import DomainError


# ==============================================================================
# Canonical Confidence Levels & Enums
# ==============================================================================


class ConfidenceLevel(str, Enum):
    """Canonical qualitative confidence rating for evidence support.
    
    Semantic Meaning:
    - VERY_HIGH: Deterministic direct counterparty identifier match (Rule P-1).
    - HIGH:      Strong candidate support without direct identifier (e.g. invoice reference + exact amount).
    - MEDIUM:    Moderate evidence support (e.g. exact amount or name match only).
    - LOW:       Weak circumstantial support.
    - NONE:      Zero match score or absent supporting evidence.
    """

    VERY_HIGH = "VERY_HIGH"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class ConfidenceFactorType(str, Enum):
    """Traceable rationale factor categories contributing to the confidence assessment."""

    BASE_MATCH_SCORE = "BASE_MATCH_SCORE"
    DIRECT_IDENTIFIER_GROUNDING = "DIRECT_IDENTIFIER_GROUNDING"
    CONFLICT_OBSERVED = "CONFLICT_OBSERVED"


# ==============================================================================
# Value Objects & Canonical Policy
# ==============================================================================


@dataclass(frozen=True)
class ConfidenceAdjustmentFactor:
    """Explainable factor documenting confidence assessment rationale and provenance."""

    factor_type: ConfidenceFactorType
    delta: Decimal
    description: str

    def __post_init__(self) -> None:
        if not self.description or not self.description.strip():
            raise DomainError("ConfidenceAdjustmentFactor description must be non-empty.")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "factor_type": self.factor_type.value,
            "delta": str(self.delta),
            "description": self.description,
        }


@dataclass(frozen=True)
class ConfidencePolicy:
    """Canonical versioned policy for the Phase 14.12 Confidence Engine.

    Guarantees:
    - Pure Decimal precision.
    - Zero probability claims (is_statistically_calibrated = False).
    - Immutable, canonical defaults. No hidden arbitrary penalties or dampening.
    """

    max_confidence: Decimal = Decimal("100.00")
    min_confidence: Decimal = Decimal("0.00")

    # Authoritative qualitative rating score boundaries
    very_high_threshold: Decimal = Decimal("90.00")
    high_threshold: Decimal = Decimal("70.00")
    medium_threshold: Decimal = Decimal("40.00")

    rule_version: str = "1.0.0"
    algorithm_version: str = "14.12.0"

    def __post_init__(self) -> None:
        if self.max_confidence <= Decimal("0.00"):
            raise DomainError("max_confidence must be positive.")
        if self.min_confidence < Decimal("0.00"):
            raise DomainError("min_confidence cannot be negative.")
        if self.very_high_threshold <= self.high_threshold:
            raise DomainError("very_high_threshold must be strictly greater than high_threshold.")
        if self.high_threshold <= self.medium_threshold:
            raise DomainError("high_threshold must be strictly greater than medium_threshold.")


# Authoritative canonical policy instance
CANONICAL_CONFIDENCE_POLICY = ConfidencePolicy()

# Backwards-compatible aliases for legacy imports
ConfidenceConfig = ConfidencePolicy
DEFAULT_CONFIDENCE_CONFIG = CANONICAL_CONFIDENCE_POLICY


# ==============================================================================
# Assessment Result Aggregates
# ==============================================================================


@dataclass(frozen=True)
class CandidateConfidenceAssessment:
    """Authoritative confidence evaluation for an individual candidate invoice.

    Guarantees:
    - 0.00 <= confidence_score <= 100.00
    - Exact Decimal precision.
    - Complete provenance traceability to Phase 14.11 score and Phase 14.10 evidence.
    - Explicit statement: is_statistically_calibrated=False.
    """

    invoice_id: UUID
    invoice_number: str
    confidence_score: Decimal
    confidence_level: ConfidenceLevel
    base_match_score: Decimal
    has_direct_evidence: bool
    has_conflicting_evidence: bool
    adjustment_factors: List[ConfidenceAdjustmentFactor]
    rule_version: str
    scoring_algorithm_version: str
    confidence_algorithm_version: str
    is_statistically_calibrated: bool = False

    def __post_init__(self) -> None:
        if self.confidence_score < Decimal("0.00") or self.confidence_score > Decimal("100.00"):
            raise DomainError(f"confidence_score must be between 0.00 and 100.00, got {self.confidence_score}")
        if self.base_match_score < Decimal("0.00") or self.base_match_score > Decimal("100.00"):
            raise DomainError(f"base_match_score must be between 0.00 and 100.00, got {self.base_match_score}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "invoice_id": str(self.invoice_id),
            "invoice_number": self.invoice_number,
            "confidence_score": str(self.confidence_score),
            "confidence_level": self.confidence_level.value,
            "base_match_score": str(self.base_match_score),
            "has_direct_evidence": self.has_direct_evidence,
            "has_conflicting_evidence": self.has_conflicting_evidence,
            "adjustment_factors": [f.to_dict() for f in self.adjustment_factors],
            "rule_version": self.rule_version,
            "scoring_algorithm_version": self.scoring_algorithm_version,
            "confidence_algorithm_version": self.confidence_algorithm_version,
            "is_statistically_calibrated": self.is_statistically_calibrated,
        }


@dataclass(frozen=True)
class ConfidenceEngineResult:
    """Aggregate result of Phase 14.12 Confidence Engine evaluation across all candidates.

    Consumes Phase 14.11 MatchingScoringResult and Phase 14.10 EvidenceNormalizationResult.
    Read-only in-memory evaluation. Zero database mutations.
    """

    payment_id: UUID
    company_id: UUID
    candidate_assessments: List[CandidateConfidenceAssessment]
    total_candidates_evaluated: int
    rule_version: str
    scoring_algorithm_version: str
    confidence_algorithm_version: str
    is_deterministic: bool = True
    is_statistically_calibrated: bool = False
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_deterministic_payload(self) -> Dict[str, Any]:
        """Convert to serializable payload containing ONLY deterministic evaluation facts.

        Excludes non-deterministic timestamps (evaluated_at).
        Guarantees 100-run bit-for-bit permutation invariance.
        """
        return {
            "payment_id": str(self.payment_id),
            "company_id": str(self.company_id),
            "candidate_assessments": [c.to_dict() for c in self.candidate_assessments],
            "total_candidates_evaluated": self.total_candidates_evaluated,
            "rule_version": self.rule_version,
            "scoring_algorithm_version": self.scoring_algorithm_version,
            "confidence_algorithm_version": self.confidence_algorithm_version,
            "is_deterministic": self.is_deterministic,
            "is_statistically_calibrated": self.is_statistically_calibrated,
        }

    def to_dict(self) -> Dict[str, Any]:
        payload = self.to_deterministic_payload()
        payload["evaluated_at"] = self.evaluated_at.isoformat()
        return payload


# ==============================================================================
# Pure Domain Confidence Engine
# ==============================================================================


class ConfidenceEngine:
    """Pure domain Confidence Engine for Phase 14.12.

    Consumes already-evaluated candidate scores from Phase 14.11 and normalized evidence
    bundles from Phase 14.10 to produce a deterministic confidence assessment for each
    candidate invoice.

    Key Guarantees:
    1. Deterministic Calculation: Bit-for-bit identical outcomes across repeated executions.
    2. Zero Probability Claims: Produces a deterministic confidence score [0.00, 100.00];
       explicitly flags is_statistically_calibrated=False.
    3. Anti-Double Counting: Preserves Phase 14.11 total_score directly; does NOT invent
       or apply unauthorized mathematical penalties, ceilings, or dampening formulas.
    4. Direct Evidence Grounding: Factually determines presence of direct counterparty
       evidence (Rule P-1).
    5. Non-Decision: Evaluates confidence only; never outputs AUTO_ELIGIBLE, MATCH_SUGGESTED,
       or financial allocations (deferred strictly to Phase 14.13).
    6. Zero Financial Mutation: 100% in-memory read-only evaluation.
    """

    def __init__(
        self,
        policy: ConfidencePolicy = CANONICAL_CONFIDENCE_POLICY,
        config: Optional[ConfidencePolicy] = None,
    ) -> None:
        self.policy = config if config is not None else policy

    def _determine_confidence_level(
        self,
        score: Decimal,
        has_direct_evidence: bool,
    ) -> ConfidenceLevel:
        """Derive qualitative confidence level from numeric score and direct evidence grounding.
        
        Authoritative mapping:
        - VERY_HIGH: Score >= 90.00 AND direct identifier evidence present (Rule P-1).
        - HIGH:      Score >= 70.00 (strong evidence support).
        - MEDIUM:    Score >= 40.00 (moderate evidence support).
        - LOW:       Score > 0.00 (weak circumstantial support).
        - NONE:      Score == 0.00 (no supporting evidence).
        """
        if score >= self.policy.very_high_threshold:
            # VERY_HIGH strictly requires direct counterparty evidence grounding
            if has_direct_evidence:
                return ConfidenceLevel.VERY_HIGH
            return ConfidenceLevel.HIGH

        if score >= self.policy.high_threshold:
            return ConfidenceLevel.HIGH

        if score >= self.policy.medium_threshold:
            return ConfidenceLevel.MEDIUM

        if score > Decimal("0.00"):
            return ConfidenceLevel.LOW

        return ConfidenceLevel.NONE

    def assess_candidate(
        self,
        candidate_score: CandidateScoreResult,
        bundle: Optional[NormalizedCandidateBundle],
        payment_evidence: NormalizedPaymentEvidenceContext,
    ) -> CandidateConfidenceAssessment:
        """Assess confidence for a single candidate invoice.

        Parameters:
        - candidate_score: Evaluated Phase 14.11 score for this candidate.
        - bundle: Corresponding Phase 14.10 normalized candidate bundle (optional for robustness).
        - payment_evidence: Payment-level normalized evidence context.
        """
        base_score = candidate_score.total_score
        adjustments: List[ConfidenceAdjustmentFactor] = []

        # 1. Base Score Anchor (Preserves Phase 14.11 score without double counting)
        adjustments.append(
            ConfidenceAdjustmentFactor(
                factor_type=ConfidenceFactorType.BASE_MATCH_SCORE,
                delta=Decimal("0.00"),
                description=f"Confidence anchored to deterministic Phase 14.11 match score ({base_score:.2f})",
            )
        )

        # 2. Direct Evidence Grounding Check (e.g. Rule P-1 Bank Account or UPI VPA)
        has_direct = False
        if bundle is not None:
            has_direct = bundle.direct_evidence_count > 0
        else:
            has_direct = any(
                c.applied and c.signal_type == ScoringSignalType.IDENTIFIER_MATCH
                for c in candidate_score.contributions
            )

        if has_direct:
            adjustments.append(
                ConfidenceAdjustmentFactor(
                    factor_type=ConfidenceFactorType.DIRECT_IDENTIFIER_GROUNDING,
                    delta=Decimal("0.00"),
                    description="Direct counterparty identifier verified (Rule P-1)",
                )
            )

        # 3. Conflict Observation (Factually surfaced without inventing unauthorized penalties)
        has_conflict = candidate_score.has_conflicting_evidence or payment_evidence.has_conflicting_identifiers
        if bundle is not None and bundle.has_conflicting_evidence:
            has_conflict = True

        if has_conflict:
            adjustments.append(
                ConfidenceAdjustmentFactor(
                    factor_type=ConfidenceFactorType.CONFLICT_OBSERVED,
                    delta=Decimal("0.00"),
                    description="Contradictory or conflicting evidence observed in candidate bundle",
                )
            )

        # 4. Final Mathematical Score (Exact Decimal, bounded to [0.00, 100.00])
        final_confidence = min(self.policy.max_confidence, max(self.policy.min_confidence, base_score))
        final_confidence = final_confidence.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 5. Qualitative Rating Assignment
        confidence_level = self._determine_confidence_level(
            score=final_confidence,
            has_direct_evidence=has_direct,
        )

        return CandidateConfidenceAssessment(
            invoice_id=candidate_score.invoice_id,
            invoice_number=candidate_score.invoice_number,
            confidence_score=final_confidence,
            confidence_level=confidence_level,
            base_match_score=base_score,
            has_direct_evidence=has_direct,
            has_conflicting_evidence=has_conflict,
            adjustment_factors=adjustments,
            rule_version=self.policy.rule_version,
            scoring_algorithm_version=candidate_score.algorithm_version,
            confidence_algorithm_version=self.policy.algorithm_version,
            is_statistically_calibrated=False,
        )

    def evaluate(
        self,
        scoring_result: MatchingScoringResult,
        normalization_result: Optional[EvidenceNormalizationResult] = None,
    ) -> ConfidenceEngineResult:
        """Evaluate deterministic confidence assessments across all candidate scores.

        Guarantees:
        - 100% deterministic output.
        - Preserves Candidate order: sorted by (-confidence_score, -base_match_score, invoice_number, str(invoice_id)).
        - Zero financial state mutation.
        """
        bundles_by_id: Dict[UUID, NormalizedCandidateBundle] = {}
        payment_evidence = None

        if normalization_result is not None:
            if normalization_result.payment_id != scoring_result.payment_id:
                raise DomainError(
                    f"Mismatched payment_id between scoring ({scoring_result.payment_id}) "
                    f"and normalization ({normalization_result.payment_id})"
                )
            if normalization_result.company_id != scoring_result.company_id:
                raise DomainError(
                    f"Mismatched company_id between scoring ({scoring_result.company_id}) "
                    f"and normalization ({normalization_result.company_id})"
                )
            for b in normalization_result.candidate_bundles:
                bundles_by_id[b.invoice_id] = b
            payment_evidence = normalization_result.payment_evidence

        if payment_evidence is None:
            payment_evidence = NormalizedPaymentEvidenceContext(
                payment_id=scoring_result.payment_id,
                company_id=scoring_result.company_id,
                items=[],
                extracted_invoice_references=[],
                has_conflicting_identifiers=False,
            )

        assessments: List[CandidateConfidenceAssessment] = []
        for cand_score in scoring_result.candidate_scores:
            bundle = bundles_by_id.get(cand_score.invoice_id)
            assessment = self.assess_candidate(
                candidate_score=cand_score,
                bundle=bundle,
                payment_evidence=payment_evidence,
            )
            assessments.append(assessment)

        # Deterministic Ranking Order:
        # Highest confidence first; ties broken by base_match_score descending,
        # invoice_number ascending, then str(invoice_id) ascending.
        assessments.sort(
            key=lambda a: (
                -a.confidence_score,
                -a.base_match_score,
                a.invoice_number,
                str(a.invoice_id),
            )
        )

        return ConfidenceEngineResult(
            payment_id=scoring_result.payment_id,
            company_id=scoring_result.company_id,
            candidate_assessments=assessments,
            total_candidates_evaluated=len(assessments),
            rule_version=self.policy.rule_version,
            scoring_algorithm_version=scoring_result.algorithm_version,
            confidence_algorithm_version=self.policy.algorithm_version,
            is_deterministic=True,
            is_statistically_calibrated=False,
        )
