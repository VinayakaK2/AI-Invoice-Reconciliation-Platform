"""Domain entities, value objects, confidence configuration, and deterministic engine for Phase 14.12 Confidence Engine.

Phase 14.12 establishes a deterministic confidence assessment layer that consumes
upstream Phase 14.11 MatchingScoringResult and Phase 14.10 EvidenceNormalizationResult
and produces a canonical, explainable confidence evaluation (0.00 to 100.00) for each
reconciliation candidate.

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
6. Canonical Scale: Exactly one authoritative scale: Decimal("0.00") to Decimal("100.00").
   Qualitative tiers:
     - VERY_HIGH: >= 90.00
     - HIGH:      70.00 - 89.99
     - MEDIUM:    40.00 - 69.99
     - LOW:       0.01 - 39.99
     - NONE:      0.00
7. Anti-Double Counting: Base score from Phase 14.11 is preserved. Confidence assessment
   evaluates evidence strength, direct evidence presence, conflict penalties, and multi-candidate
   ambiguity. It does NOT recompute or double-award base signal weights.
8. Conflict Integrity: Active conflicting evidence enforces severe deterministic dampening
   (penalized or clamped), preventing unconflicted confidence when contradictions exist.
9. Missing Evidence: Missing evidence awards 0 points; absence of evidence is not penalized
   as contradiction unless conflicting signals are present.
10. Versioning & Provenance: Every assessment records rule_version ("1.0.0"), scoring_algorithm_version,
    and confidence_algorithm_version ("14.12.0").
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.modules.reconciliation.domain.evidence_collection import EvidenceClassification
from app.modules.reconciliation.domain.evidence_normalization import (
    CanonicalEvidenceObject,
    EvidenceNormalizationResult,
    NormalizedCandidateBundle,
    NormalizedEvidenceResult,
    NormalizedEvidenceType,
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
    """Canonical qualitative confidence bands grounded in business rule thresholds."""

    VERY_HIGH = "VERY_HIGH"  # Score >= 90.00 (aligns with Rule 2.5 AUTO_ELIGIBLE threshold preconditions)
    HIGH = "HIGH"            # Score 70.00 - 89.99 (aligns with Rule 2.5 MATCH_SUGGESTED threshold)
    MEDIUM = "MEDIUM"        # Score 40.00 - 69.99 (partial / moderate signal support)
    LOW = "LOW"              # Score 0.01 - 39.99 (weak / circumstantial support)
    NONE = "NONE"            # Score 0.00 (no supporting evidence or complete suppression)


class ConfidenceFactorType(str, Enum):
    """Traceable rationale factor categories contributing to the confidence assessment."""

    BASE_MATCH_SCORE = "BASE_MATCH_SCORE"                    # Preserved Phase 14.11 score contribution
    DIRECT_IDENTIFIER_BOOST = "DIRECT_IDENTIFIER_BOOST"      # Direct account/UPI coordinate confirmation
    INVOICE_NUMBER_CONFIRMATION = "INVOICE_NUMBER_CONFIRMATION"  # Explicit invoice reference provenance match
    AMOUNT_CONSERVATION = "AMOUNT_CONSERVATION"              # Exact financial amount equality
    CONFLICT_PENALTY = "CONFLICT_PENALTY"                    # Active contradictory evidence penalty
    AMBIGUITY_DAMPENING = "AMBIGUITY_DAMPENING"              # Multiple competing candidates dampening
    MISSING_ESSENTIAL_PENALTY = "MISSING_ESSENTIAL_PENALTY"  # Absence of key identifiers in high-score claim


# ==============================================================================
# Value Objects & Configuration
# ==============================================================================


@dataclass(frozen=True)
class ConfidenceAdjustmentFactor:
    """Explainable factor documenting how confidence was calibrated from base score."""

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
class ConfidenceConfig:
    """Authoritative deterministic confidence calibration configuration.

    Guarantees:
    - Pure Decimal precision.
    - Zero probability claims (is_statistically_calibrated = False).
    """

    # Maximum confidence score
    max_confidence: Decimal = Decimal("100.00")
    min_confidence: Decimal = Decimal("0.00")

    # Conflict penalty: severe dampening if candidate has conflicting evidence
    conflict_penalty_pct: Decimal = Decimal("0.50")  # Reduces score by 50% if unsuppressed conflict remains
    hard_conflict_ceiling: Decimal = Decimal("40.00")  # Clamps maximum confidence to 40.00 if conflict exists

    # Ambiguity dampening: applied when another candidate has a tied or near-tied high score
    ambiguity_delta_threshold: Decimal = Decimal("10.00")  # Candidates within 10 points are considered competing
    ambiguity_penalty: Decimal = Decimal("15.00")          # Deducts 15 points if multiple close candidates exist

    # Level thresholds
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


DEFAULT_CONFIDENCE_CONFIG = ConfidenceConfig()


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
    3. Anti-Double Counting: Uses Phase 14.11 total_score as the baseline; evaluates
       evidence veracity, direct evidence grounding, conflict dampening, and multi-candidate
       competing ambiguity without recalculating base signal weights.
    4. Conflict Sensitivity: If active unsuppressed conflicts exist, penalizes and caps
       the confidence score to prevent false certainty.
    5. Non-Decision: Evaluates confidence only; never outputs AUTO_ELIGIBLE, MATCH_SUGGESTED,
       or financial allocations (deferred strictly to Phase 14.13).
    6. Zero Financial Mutation: 100% in-memory read-only evaluation.
    """

    def __init__(
        self,
        config: ConfidenceConfig = DEFAULT_CONFIDENCE_CONFIG,
    ) -> None:
        self.config = config

    def _determine_confidence_level(self, score: Decimal) -> ConfidenceLevel:
        """Derive qualitative confidence level from numeric score."""
        if score >= self.config.very_high_threshold:
            return ConfidenceLevel.VERY_HIGH
        if score >= self.config.high_threshold:
            return ConfidenceLevel.HIGH
        if score >= self.config.medium_threshold:
            return ConfidenceLevel.MEDIUM
        if score > Decimal("0.00"):
            return ConfidenceLevel.LOW
        return ConfidenceLevel.NONE

    def assess_candidate(
        self,
        candidate_score: CandidateScoreResult,
        bundle: Optional[NormalizedCandidateBundle],
        payment_evidence: NormalizedPaymentEvidenceContext,
        all_candidate_scores: List[CandidateScoreResult],
    ) -> CandidateConfidenceAssessment:
        """Assess confidence for a single candidate invoice.

        Parameters:
        - candidate_score: Evaluated Phase 14.11 score for this candidate.
        - bundle: Corresponding Phase 14.10 normalized candidate bundle (optional for robustness).
        - payment_evidence: Payment-level normalized evidence context.
        - all_candidate_scores: All candidate scores for this payment (used for ambiguity detection).
        """
        base_score = candidate_score.total_score
        adjustments: List[ConfidenceAdjustmentFactor] = []

        # 1. Base Score Anchor
        adjustments.append(
            ConfidenceAdjustmentFactor(
                factor_type=ConfidenceFactorType.BASE_MATCH_SCORE,
                delta=base_score,
                description=f"Initial confidence anchored to Phase 14.11 match score ({base_score:.2f})",
            )
        )

        current_score = base_score

        # 2. Direct Evidence Grounding Check
        has_direct = False
        if bundle is not None:
            has_direct = bundle.direct_evidence_count > 0
        else:
            # Fallback to inspecting applied score contributions
            has_direct = any(
                c.applied and c.signal_type in (ScoringSignalType.IDENTIFIER_MATCH, ScoringSignalType.INVOICE_NUMBER_MATCH, ScoringSignalType.AMOUNT_EXACT_MATCH)
                for c in candidate_score.contributions
            )

        # 3. Conflict Penalty Evaluation
        has_conflict = candidate_score.has_conflicting_evidence or payment_evidence.has_conflicting_identifiers
        if bundle is not None and bundle.has_conflicting_evidence:
            has_conflict = True

        if has_conflict:
            # Apply proportional penalty and clamp to hard ceiling
            conflict_deduction = (current_score * self.config.conflict_penalty_pct).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            current_score = max(Decimal("0.00"), current_score - conflict_deduction)
            adjustments.append(
                ConfidenceAdjustmentFactor(
                    factor_type=ConfidenceFactorType.CONFLICT_PENALTY,
                    delta=-conflict_deduction,
                    description=f"Active conflicting evidence reduced confidence by {self.config.conflict_penalty_pct * 100:.0f}%",
                )
            )

            if current_score > self.config.hard_conflict_ceiling:
                ceiling_deduction = current_score - self.config.hard_conflict_ceiling
                current_score = self.config.hard_conflict_ceiling
                adjustments.append(
                    ConfidenceAdjustmentFactor(
                        factor_type=ConfidenceFactorType.CONFLICT_PENALTY,
                        delta=-ceiling_deduction,
                        description=f"Confidence clamped to hard ceiling ({self.config.hard_conflict_ceiling:.2f}) due to active conflict",
                    )
                )

        # 4. Multi-Candidate Ambiguity Dampening Check
        # If there are competing candidates with close high scores (within ambiguity_delta_threshold),
        # confidence in any single candidate is diminished.
        if len(all_candidate_scores) > 1 and current_score > Decimal("0.00"):
            competing_candidates = [
                cs for cs in all_candidate_scores
                if cs.invoice_id != candidate_score.invoice_id
                and cs.total_score >= self.config.high_threshold
                and abs(cs.total_score - base_score) <= self.config.ambiguity_delta_threshold
            ]
            if competing_candidates:
                ambiguity_deduction = min(current_score, self.config.ambiguity_penalty)
                current_score -= ambiguity_deduction
                competing_inv_nums = ", ".join(f"'{c.invoice_number}'" for c in competing_candidates[:3])
                adjustments.append(
                    ConfidenceAdjustmentFactor(
                        factor_type=ConfidenceFactorType.AMBIGUITY_DAMPENING,
                        delta=-ambiguity_deduction,
                        description=f"Confidence dampened by competing candidate(s) {competing_inv_nums} within {self.config.ambiguity_delta_threshold:.2f} points",
                    )
                )

        # 5. Final Mathematical Clamping to [0.00, 100.00]
        final_confidence = min(self.config.max_confidence, max(self.config.min_confidence, current_score))
        final_confidence = final_confidence.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        confidence_level = self._determine_confidence_level(final_confidence)

        return CandidateConfidenceAssessment(
            invoice_id=candidate_score.invoice_id,
            invoice_number=candidate_score.invoice_number,
            confidence_score=final_confidence,
            confidence_level=confidence_level,
            base_match_score=base_score,
            has_direct_evidence=has_direct,
            has_conflicting_evidence=has_conflict,
            adjustment_factors=adjustments,
            rule_version=self.config.rule_version,
            scoring_algorithm_version=candidate_score.algorithm_version,
            confidence_algorithm_version=self.config.algorithm_version,
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
        # Map bundle by invoice_id if normalization_result provided
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

        # Create dummy payment evidence if none provided
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
                all_candidate_scores=scoring_result.candidate_scores,
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
            rule_version=self.config.rule_version,
            scoring_algorithm_version=scoring_result.algorithm_version,
            confidence_algorithm_version=self.config.algorithm_version,
            is_deterministic=True,
            is_statistically_calibrated=False,
        )
