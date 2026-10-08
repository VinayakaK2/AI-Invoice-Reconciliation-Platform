"""Domain entities, value objects, scoring configuration, and pure rule engine for Phase 14.11 Matching & Scoring.

Phase 14.11 establishes a deterministic mathematical scoring layer that consumes
canonical normalized evidence produced by Phase 14.10 (CanonicalEvidenceObject,
NormalizedCandidateBundle, EvidenceNormalizationResult) and computes an explainable,
reproducible candidate score (0.00 to 100.00) for each evaluated reconciliation candidate.

CRITICAL DOMAIN INVARIANTS:
1. Separation of Concerns:
   Evidence ≠ Score ≠ Confidence ≠ Decision ≠ Approval ≠ Mutation.
2. A score is an evidence aggregation metric (0.00 to 100.00); it is strictly NOT
   a calibrated probability of correctness (deferred to Phase 14.12) and NOT a financial
   disposition or decision (deferred to Phase 14.13).
3. Observed Financial Mutation: NONE (Strictly read-only domain evaluation).
4. Pure Determinism: 100% deterministic, zero LLM authority, zero floating-point arithmetic.
   Uses exact Decimal for calculations.
5. Mathematical Bounds: 0.00 <= candidate_score <= 100.00.
6. Provenance & Explainability: Every score contribution is traceable to its source evidence,
   rule version, and algorithm version.
7. Anti-Double Counting: Mutually exclusive / alias evidence signals representing the same
   underlying fact (e.g. PAYMENT_REFERENCE vs INVOICE_NUMBER_IN_NARRATION) award points only once.
8. Conflicting & Missing Signals:
   - Missing evidence receives 0 points (no positive contribution).
   - Conflicting evidence on an evaluation dimension completely suppresses positive score
     contributions for that dimension (e.g. currency mismatch suppresses amount match points).
9. Empirical Validation Status: The scoring engine implementation and mathematical invariants
   are verified. Weight effectiveness and real-world calibration remain explicitly provisional
   and subject to empirical validation against production datasets.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from app.modules.reconciliation.domain.evidence_collection import EvidenceClassification
from app.modules.reconciliation.domain.evidence_normalization import (
    CanonicalEvidenceObject,
    EvidenceNormalizationResult,
    NormalizedCandidateBundle,
    NormalizedEvidenceResult,
    NormalizedEvidenceSource,
    NormalizedEvidenceStrength,
    NormalizedEvidenceType,
    NormalizedPaymentEvidenceContext,
    NormalizedRelevantIdentifiers,
    NormalizedTargetEntity,
)
from app.shared.exceptions import DomainError


# ==============================================================================
# Scoring Signals & Controlled Weight Configuration
# ==============================================================================


class ScoringSignalType(str, Enum):
    """Authoritative scoring signal dimensions grounded in Rule 2.4 of business-rules.md."""

    IDENTIFIER_MATCH = "IDENTIFIER_MATCH"              # Direct bank account, virtual account, or UPI VPA match (+40)
    INVOICE_NUMBER_MATCH = "INVOICE_NUMBER_MATCH"      # Explicit candidate invoice number in payment reference/narration (+35)
    AMOUNT_EXACT_MATCH = "AMOUNT_EXACT_MATCH"          # Payment effective amount exactly equals invoice outstanding (+30)
    CUSTOMER_NAME_MATCH = "CUSTOMER_NAME_MATCH"        # Verified customer legal name or registered alias match (+20)
    DATE_PROXIMITY_MATCH = "DATE_PROXIMITY_MATCH"      # Payment date within acceptable proximity of invoice due date (+10)
    HISTORICAL_PATTERN = "HISTORICAL_PATTERN"          # Recurring customer payment pattern match (+5, optional)


@dataclass(frozen=True)
class ScoringWeightsConfig:
    """Authoritative weight configuration defined by business-rules.md Rule 2.4.

    Mathematical Invariant:
    max_score = 100.00
    score = min(100.00, sum(active_unconflicted_weights))
    """

    identifier_match_weight: Decimal = Decimal("40.00")
    invoice_number_match_weight: Decimal = Decimal("35.00")
    amount_exact_match_weight: Decimal = Decimal("30.00")
    customer_name_match_weight: Decimal = Decimal("20.00")
    date_proximity_match_weight: Decimal = Decimal("10.00")
    historical_pattern_weight: Decimal = Decimal("5.00")
    max_score: Decimal = Decimal("100.00")
    rule_version: str = "1.0.0"
    algorithm_version: str = "14.11.0"

    def __post_init__(self) -> None:
        """Validate configuration integrity."""
        if self.max_score <= Decimal("0.00"):
            raise DomainError("max_score must be positive Decimal.")
        for weight_name, val in [
            ("identifier_match_weight", self.identifier_match_weight),
            ("invoice_number_match_weight", self.invoice_number_match_weight),
            ("amount_exact_match_weight", self.amount_exact_match_weight),
            ("customer_name_match_weight", self.customer_name_match_weight),
            ("date_proximity_match_weight", self.date_proximity_match_weight),
            ("historical_pattern_weight", self.historical_pattern_weight),
        ]:
            if val < Decimal("0.00"):
                raise DomainError(f"{weight_name} cannot be negative.")


# Default authoritative instance
DEFAULT_SCORING_WEIGHTS = ScoringWeightsConfig()


# ==============================================================================
# Domain Entities & Score Breakdown Value Objects
# ==============================================================================


@dataclass(frozen=True)
class ScoreContribution:
    """Traceable, explainable contribution of a single evidence signal to a candidate's score.

    Answers exactly 'Why did this candidate receive points for this dimension?'
    """

    signal_type: ScoringSignalType
    weight: Decimal
    applied: bool
    evidence_type: NormalizedEvidenceType
    source: NormalizedEvidenceSource
    result: NormalizedEvidenceResult
    reason: str
    target_entity: NormalizedTargetEntity
    matched_value: Optional[str] = None
    expected_value: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate invariant."""
        if not self.reason or not self.reason.strip():
            raise DomainError("ScoreContribution reason must be non-empty string.")

    def to_dict(self) -> Dict[str, Any]:
        """Convert contribution to serializable dictionary."""
        return {
            "signal_type": self.signal_type.value,
            "weight": str(self.weight),
            "applied": self.applied,
            "evidence_type": self.evidence_type.value,
            "source": self.source.value,
            "result": self.result.value,
            "reason": self.reason,
            "target_entity": self.target_entity.value,
            "matched_value": self.matched_value,
            "expected_value": self.expected_value,
        }


@dataclass(frozen=True)
class CandidateScoreResult:
    """Authoritative score evaluation result for a single candidate invoice.

    Guarantees:
    - 0.00 <= total_score <= 100.00
    - Exact Decimal precision.
    - Zero probability or confidence claims (confidence deferred to Phase 14.12).
    - Zero financial decision claims (decision deferred to Phase 14.13).
    - Full provenance of applied and suppressed contributions.
    """

    invoice_id: UUID
    invoice_number: str
    total_score: Decimal
    raw_unclamped_score: Decimal
    contributions: List[ScoreContribution]
    has_conflicting_evidence: bool
    rule_version: str
    algorithm_version: str
    is_empirically_validated: bool = False

    def __post_init__(self) -> None:
        """Enforce mathematical scoring invariants."""
        if self.total_score < Decimal("0.00") or self.total_score > Decimal("100.00"):
            raise DomainError(f"total_score must be between 0.00 and 100.00, got {self.total_score}")
        if self.raw_unclamped_score < Decimal("0.00"):
            raise DomainError("raw_unclamped_score cannot be negative.")

    def to_dict(self) -> Dict[str, Any]:
        """Convert candidate score result to serializable dictionary."""
        return {
            "invoice_id": str(self.invoice_id),
            "invoice_number": self.invoice_number,
            "total_score": str(self.total_score),
            "raw_unclamped_score": str(self.raw_unclamped_score),
            "contributions": [c.to_dict() for c in self.contributions],
            "has_conflicting_evidence": self.has_conflicting_evidence,
            "rule_version": self.rule_version,
            "algorithm_version": self.algorithm_version,
            "is_empirically_validated": self.is_empirically_validated,
        }


@dataclass(frozen=True)
class MatchingScoringResult:
    """Aggregate result of Phase 14.11 Matching & Scoring across all candidates.

    Read-only evaluation over Phase 14.10 EvidenceNormalizationResult.
    """

    payment_id: UUID
    company_id: UUID
    candidate_scores: List[CandidateScoreResult]
    total_candidates_scored: int
    rule_version: str
    algorithm_version: str
    is_deterministic: bool = True
    is_empirically_validated: bool = False
    scored_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_deterministic_payload(self) -> Dict[str, Any]:
        """Convert to serializable payload containing ONLY deterministic evaluation facts.

        Excludes non-deterministic timestamps (scored_at).
        Guarantees 100-run bit-for-bit permutation invariance.
        """
        return {
            "payment_id": str(self.payment_id),
            "company_id": str(self.company_id),
            "candidate_scores": [c.to_dict() for c in self.candidate_scores],
            "total_candidates_scored": self.total_candidates_scored,
            "rule_version": self.rule_version,
            "algorithm_version": self.algorithm_version,
            "is_deterministic": self.is_deterministic,
            "is_empirically_validated": self.is_empirically_validated,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to serializable dictionary including execution metadata."""
        payload = self.to_deterministic_payload()
        payload["scored_at"] = self.scored_at.isoformat()
        return payload


# ==============================================================================
# Pure Domain Matching & Scoring Engine
# ==============================================================================


class MatchingScoringEngine:
    """Pure domain scoring engine for Phase 14.11.

    Consumes canonical normalized evidence produced by Phase 14.10 and calculates
    deterministic candidate scores adhering strictly to Rule 2.4 of business-rules.md:
      match_score = min(100.00, sum(Weights of Active Unconflicted Evidence))

    Key Guarantees:
    1. Determinism: Exactly identical scores across any repeated evaluation.
    2. Precision: Pure Decimal arithmetic. No float drift.
    3. Anti-Double Counting: Semantically redundant signals award points once.
    4. Anti-Fabrication: Missing signals award 0 points.
    5. Conflict Suppression: Unconflicted evidence only. Conflicting evidence suppresses
       points for that evaluation dimension.
    6. Non-Decision: Scores only. Never outputs AUTO_ELIGIBLE, MATCH_SUGGESTED, or allocation.
    7. Zero Financial Mutation: Strictly in-memory evaluation.
    """

    def __init__(
        self,
        config: Optional[ScoringWeightsConfig] = None,
        is_empirically_validated: bool = False,
    ) -> None:
        self.config = config or DEFAULT_SCORING_WEIGHTS
        # Empirical validation flag explicitly communicates readiness status
        self.is_empirically_validated = is_empirically_validated

    def score_candidate(
        self,
        bundle: NormalizedCandidateBundle,
        payment_evidence: NormalizedPaymentEvidenceContext,
    ) -> CandidateScoreResult:
        """Evaluate normalized evidence items for a single candidate bundle and calculate its score."""
        contributions: List[ScoreContribution] = []

        # Merge candidate bundle items with payment context items for holistic candidate scoring
        # Note: Payment-level counterparty items (bank account, name) inform whether this candidate's
        # counterparty is verified.
        bundle_items = list(bundle.items)
        payment_items = list(payment_evidence.items)

        # --------------------------------------------------------------------------
        # 1. IDENTIFIER_MATCH (+40 points)
        # Direct bank account, virtual account, or UPI VPA match
        # --------------------------------------------------------------------------
        identifier_match = False
        identifier_conflict = False
        matching_acc_item: Optional[CanonicalEvidenceObject] = None

        # Check for account conflict first
        for item in payment_items + bundle_items:
            if item.evidence_type in (
                NormalizedEvidenceType.BANK_ACCOUNT_IDENTIFIER,
                NormalizedEvidenceType.UPI_VPA_IDENTIFIER,
            ):
                if item.classification == EvidenceClassification.CONFLICTING or item.result == NormalizedEvidenceResult.CONFLICT:
                    identifier_conflict = True
                elif item.classification == EvidenceClassification.DIRECT and item.result == NormalizedEvidenceResult.MATCH:
                    identifier_match = True
                    matching_acc_item = item

        if identifier_match and not identifier_conflict and matching_acc_item:
            contributions.append(
                ScoreContribution(
                    signal_type=ScoringSignalType.IDENTIFIER_MATCH,
                    weight=self.config.identifier_match_weight,
                    applied=True,
                    evidence_type=matching_acc_item.evidence_type,
                    source=matching_acc_item.source,
                    result=matching_acc_item.result,
                    reason=f"Direct counterparty account identifier verified: {matching_acc_item.matched_value or 'MATCH'}",
                    target_entity=matching_acc_item.target_entity,
                    matched_value=matching_acc_item.matched_value,
                    expected_value=matching_acc_item.expected_value,
                )
            )

        # --------------------------------------------------------------------------
        # 2. INVOICE_NUMBER_MATCH (+35 points)
        # Candidate invoice number explicitly referenced in narration or payment reference
        # Anti-double counting: Narration invoice match and Payment reference match are combined (awarded once)
        # Source truth: If matched via payment reference, source must reflect payment.payment_reference
        # --------------------------------------------------------------------------
        invoice_ref_match = False
        invoice_ref_conflict = False
        matching_inv_item: Optional[CanonicalEvidenceObject] = None
        cand_num_upper = bundle.invoice_number.upper().strip()

        # Step 2a: Check candidate bundle items for explicit invoice match or conflict
        for item in bundle_items:
            if item.evidence_type in (
                NormalizedEvidenceType.INVOICE_NUMBER_IN_NARRATION,
                NormalizedEvidenceType.PAYMENT_REFERENCE,
            ):
                if item.classification == EvidenceClassification.CONFLICTING or item.result == NormalizedEvidenceResult.CONFLICT:
                    invoice_ref_conflict = True
                elif (
                    item.classification == EvidenceClassification.DIRECT
                    or item.result in (NormalizedEvidenceResult.MATCH, NormalizedEvidenceResult.PRESENT)
                ):
                    # Verify matched_value matches candidate invoice number
                    if item.matched_value and item.matched_value.upper().strip() == cand_num_upper:
                        invoice_ref_match = True
                        if matching_inv_item is None:
                            matching_inv_item = item

        # Step 2b: Check payment-level extracted references if candidate matches
        if not invoice_ref_conflict and cand_num_upper in payment_evidence.extracted_invoice_references:
            invoice_ref_match = True
            # Find evidence item in payment items to preserve truthful provenance
            # Check payment_reference first if it matches
            pay_ref_item = None
            narration_inv_item = None
            for item in payment_items:
                if (
                    item.evidence_type == NormalizedEvidenceType.PAYMENT_REFERENCE
                    and item.matched_value
                    and item.matched_value.upper().strip() == cand_num_upper
                ):
                    pay_ref_item = item
                elif (
                    item.evidence_type == NormalizedEvidenceType.INVOICE_NUMBER_IN_NARRATION
                    and item.result == NormalizedEvidenceResult.MATCH
                ):
                    narration_inv_item = item

            # Prioritize payment_reference if present in payment_reference
            if pay_ref_item is not None:
                matching_inv_item = pay_ref_item
            elif narration_inv_item is not None and matching_inv_item is None:
                matching_inv_item = narration_inv_item

        # Step 2c: If payment referenced DIFFERENT invoice numbers and not this candidate, conflict suppression
        if not invoice_ref_match and payment_evidence.extracted_invoice_references:
            # Different invoice number was referenced in payment
            invoice_ref_conflict = True

        if invoice_ref_match and not invoice_ref_conflict:
            # If matching_inv_item is still None, create a canonical fallback referencing payment_reference or narration
            evidence_type = (
                matching_inv_item.evidence_type
                if matching_inv_item
                else NormalizedEvidenceType.INVOICE_NUMBER_IN_NARRATION
            )
            source = (
                matching_inv_item.source
                if matching_inv_item
                else NormalizedEvidenceSource.PAYMENT_PAYMENT_REFERENCE
            )
            result = (
                matching_inv_item.result
                if matching_inv_item
                else NormalizedEvidenceResult.MATCH
            )
            target_entity = (
                matching_inv_item.target_entity
                if matching_inv_item
                else NormalizedTargetEntity.INVOICE
            )

            contributions.append(
                ScoreContribution(
                    signal_type=ScoringSignalType.INVOICE_NUMBER_MATCH,
                    weight=self.config.invoice_number_match_weight,
                    applied=True,
                    evidence_type=evidence_type,
                    source=source,
                    result=result,
                    reason=f"Candidate invoice number '{bundle.invoice_number}' explicitly referenced in payment",
                    target_entity=target_entity,
                    matched_value=bundle.invoice_number,
                    expected_value=bundle.invoice_number,
                )
            )

        # --------------------------------------------------------------------------
        # 3. AMOUNT_EXACT_MATCH (+30 points)
        # Payment effective amount exactly equals candidate invoice outstanding amount
        # Conflict check: Currency mismatch on candidate completely suppresses amount points
        # --------------------------------------------------------------------------
        amount_match = False
        currency_conflict = False
        matching_amt_item: Optional[CanonicalEvidenceObject] = None

        for item in bundle_items:
            if item.evidence_type == NormalizedEvidenceType.CURRENCY_CONSISTENCY:
                if item.classification == EvidenceClassification.CONFLICTING or item.result == NormalizedEvidenceResult.CONFLICT:
                    currency_conflict = True
            elif item.evidence_type == NormalizedEvidenceType.AMOUNT_EXACT_EQUALITY:
                if item.classification == EvidenceClassification.DIRECT and item.result == NormalizedEvidenceResult.MATCH:
                    amount_match = True
                    matching_amt_item = item

        if amount_match and not currency_conflict and matching_amt_item:
            contributions.append(
                ScoreContribution(
                    signal_type=ScoringSignalType.AMOUNT_EXACT_MATCH,
                    weight=self.config.amount_exact_match_weight,
                    applied=True,
                    evidence_type=matching_amt_item.evidence_type,
                    source=matching_amt_item.source,
                    result=matching_amt_item.result,
                    reason=f"Payment effective amount exactly equals candidate invoice outstanding: {matching_amt_item.matched_value}",
                    target_entity=matching_amt_item.target_entity,
                    matched_value=matching_amt_item.matched_value,
                    expected_value=matching_amt_item.expected_value,
                )
            )

        # --------------------------------------------------------------------------
        # 4. CUSTOMER_NAME_MATCH (+20 points)
        # Verified customer name or registered alias match
        # Anti-double counting: Name token match and Alias match award points once
        # --------------------------------------------------------------------------
        cust_name_match = False
        matching_name_item: Optional[CanonicalEvidenceObject] = None

        for item in payment_items:
            if item.evidence_type in (
                NormalizedEvidenceType.CUSTOMER_NAME_TOKEN,
                NormalizedEvidenceType.CUSTOMER_ALIAS,
            ):
                if item.classification == EvidenceClassification.SUPPORTING and item.result == NormalizedEvidenceResult.MATCH:
                    cust_name_match = True
                    matching_name_item = item
                    break

        if cust_name_match and matching_name_item:
            contributions.append(
                ScoreContribution(
                    signal_type=ScoringSignalType.CUSTOMER_NAME_MATCH,
                    weight=self.config.customer_name_match_weight,
                    applied=True,
                    evidence_type=matching_name_item.evidence_type,
                    source=matching_name_item.source,
                    result=matching_name_item.result,
                    reason=f"Verified counterparty name / alias matched: {matching_name_item.matched_value or 'MATCH'}",
                    target_entity=matching_name_item.target_entity,
                    matched_value=matching_name_item.matched_value,
                    expected_value=matching_name_item.expected_value,
                )
            )

        # --------------------------------------------------------------------------
        # 5. DATE_PROXIMITY_MATCH (+10 points)
        # Payment date is within 30 days of invoice due date
        # Conflict check: Non-causal date causality suppresses date proximity points
        # --------------------------------------------------------------------------
        date_proximity_match = False
        date_causality_conflict = False
        matching_date_item: Optional[CanonicalEvidenceObject] = None

        for item in bundle_items:
            if item.evidence_type == NormalizedEvidenceType.DATE_CAUSALITY:
                if item.classification == EvidenceClassification.CONFLICTING or item.result == NormalizedEvidenceResult.CONFLICT:
                    date_causality_conflict = True
            elif item.evidence_type == NormalizedEvidenceType.DATE_PROXIMITY:
                if item.classification == EvidenceClassification.SUPPORTING and item.result == NormalizedEvidenceResult.SUPPORTED:
                    date_proximity_match = True
                    matching_date_item = item

        if date_proximity_match and not date_causality_conflict and matching_date_item:
            contributions.append(
                ScoreContribution(
                    signal_type=ScoringSignalType.DATE_PROXIMITY_MATCH,
                    weight=self.config.date_proximity_match_weight,
                    applied=True,
                    evidence_type=matching_date_item.evidence_type,
                    source=matching_date_item.source,
                    result=matching_date_item.result,
                    reason=f"Payment date within 30 days of invoice due date ({matching_date_item.details})",
                    target_entity=matching_date_item.target_entity,
                    matched_value=matching_date_item.matched_value,
                    expected_value=matching_date_item.expected_value,
                )
            )

        # --------------------------------------------------------------------------
        # 6. HISTORICAL_PATTERN (+5 points, optional)
        # Customer previously paid invoices in this pattern
        # --------------------------------------------------------------------------
        pattern_match = False
        matching_pattern_item: Optional[CanonicalEvidenceObject] = None

        for item in bundle_items:
            if item.evidence_type == NormalizedEvidenceType.HISTORICAL_CUSTOMER_PATTERN:
                if item.classification == EvidenceClassification.SUPPORTING and item.result in (
                    NormalizedEvidenceResult.MATCH,
                    NormalizedEvidenceResult.SUPPORTED,
                ):
                    pattern_match = True
                    matching_pattern_item = item
                    break

        if pattern_match and matching_pattern_item:
            contributions.append(
                ScoreContribution(
                    signal_type=ScoringSignalType.HISTORICAL_PATTERN,
                    weight=self.config.historical_pattern_weight,
                    applied=True,
                    evidence_type=matching_pattern_item.evidence_type,
                    source=matching_pattern_item.source,
                    result=matching_pattern_item.result,
                    reason=f"Historical payment pattern match: {matching_pattern_item.details}",
                    target_entity=matching_pattern_item.target_entity,
                    matched_value=matching_pattern_item.matched_value,
                    expected_value=matching_pattern_item.expected_value,
                )
            )

        # Calculate sum of active unconflicted evidence weights
        raw_sum = sum((c.weight for c in contributions if c.applied), Decimal("0.00"))

        # Clamp mathematically to [0.00, max_score] per Rule 2.4:
        # match_score = min(100.00, sum(weights))
        clamped_score = min(self.config.max_score, max(Decimal("0.00"), raw_sum))

        return CandidateScoreResult(
            invoice_id=bundle.invoice_id,
            invoice_number=bundle.invoice_number,
            total_score=clamped_score,
            raw_unclamped_score=raw_sum,
            contributions=contributions,
            has_conflicting_evidence=bundle.has_conflicting_evidence or payment_evidence.has_conflicting_identifiers,
            rule_version=self.config.rule_version,
            algorithm_version=self.config.algorithm_version,
            is_empirically_validated=self.is_empirically_validated,
        )

    def evaluate(
        self,
        normalization_result: EvidenceNormalizationResult,
    ) -> MatchingScoringResult:
        """Execute deterministic matching scoring across all candidate bundles in a normalization result.

        Guarantees:
        - 100% deterministic output.
        - Preserves Candidate order: sorted by (-total_score, invoice_number, str(invoice_id)).
        - Zero financial mutation.
        """
        scored_candidates: List[CandidateScoreResult] = []

        for bundle in normalization_result.candidate_bundles:
            cand_score = self.score_candidate(
                bundle=bundle,
                payment_evidence=normalization_result.payment_evidence,
            )
            scored_candidates.append(cand_score)

        # Deterministic ranking order:
        # Highest score first; ties broken by invoice_number, then invoice_id.
        # This guarantees bit-for-bit permutation invariance.
        scored_candidates.sort(
            key=lambda c: (
                -c.total_score,
                c.invoice_number,
                str(c.invoice_id),
            )
        )

        return MatchingScoringResult(
            payment_id=normalization_result.payment_id,
            company_id=normalization_result.company_id,
            candidate_scores=scored_candidates,
            total_candidates_scored=len(scored_candidates),
            rule_version=self.config.rule_version,
            algorithm_version=self.config.algorithm_version,
            is_deterministic=True,
            is_empirically_validated=self.is_empirically_validated,
        )
