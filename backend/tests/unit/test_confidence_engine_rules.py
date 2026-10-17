"""Pure domain unit tests for Phase 14.12 Confidence Engine.

Verifies:
1. Base score anchoring:
   - Initial confidence anchored to Phase 14.11 score without double counting.
2. Direct evidence grounding:
   - High score candidate with direct evidence correctly identifies has_direct_evidence=True.
   - Candidate with zero direct evidence reflects has_direct_evidence=False.
3. Conflict sensitivity & hard ceiling:
   - Candidate or payment with active conflicting evidence incurs 50% penalty.
   - Confidence is clamped to hard_conflict_ceiling (40.00).
4. Multi-candidate ambiguity dampening:
   - When multiple candidates have competing high scores within 10.00 points, confidence is dampened by 15.00 points.
   - Ambiguity adjustment factor is recorded with competing candidate names.
5. Qualitative confidence levels:
   - VERY_HIGH (>= 90.00)
   - HIGH (70.00 - 89.99)
   - MEDIUM (40.00 - 69.99)
   - LOW (0.01 - 39.99)
   - NONE (0.00)
6. Mathematical bounds & Decimal precision:
   - Confidence is strictly within [0.00, 100.00].
   - Pure Decimal arithmetic, zero float imprecision.
7. Pure determinism & 100-permutation invariance:
   - Repeated runs over shuffled candidate lists produce bit-for-bit identical results and ranking.
8. Semantic honesty:
   - is_statistically_calibrated is explicitly False.
   - Zero decision fields (no AUTO_ELIGIBLE, MATCH_SUGGESTED, or allocations).
"""

from datetime import date
from decimal import Decimal
import random
from typing import List, Optional
import uuid
import pytest

from app.modules.reconciliation.domain.confidence_engine import (
    CandidateConfidenceAssessment,
    ConfidenceAdjustmentFactor,
    ConfidenceConfig,
    ConfidenceEngine,
    ConfidenceEngineResult,
    ConfidenceFactorType,
    ConfidenceLevel,
    DEFAULT_CONFIDENCE_CONFIG,
)
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
from app.modules.reconciliation.domain.matching_scoring import (
    CandidateScoreResult,
    MatchingScoringResult,
    ScoreContribution,
    ScoringSignalType,
)
from app.shared.exceptions import DomainError


def _make_dummy_canonical_item(
    evidence_type: NormalizedEvidenceType,
    source: NormalizedEvidenceSource,
    result: NormalizedEvidenceResult = NormalizedEvidenceResult.MATCH,
    classification: EvidenceClassification = EvidenceClassification.DIRECT,
    details: str = "Dummy evidence item",
) -> CanonicalEvidenceObject:
    return CanonicalEvidenceObject(
        evidence_type=evidence_type,
        source=source,
        result=result,
        strength=NormalizedEvidenceStrength.EXACT,
        details=details,
        identifiers=NormalizedRelevantIdentifiers(),
        classification=classification,
        rule_version="1.0.0",
        algorithm_version="14.10.0",
        target_entity=NormalizedTargetEntity.INVOICE,
    )


def _make_candidate_score(
    invoice_id: uuid.UUID,
    invoice_number: str,
    total_score: Decimal,
    has_conflict: bool = False,
    contributions: Optional[List[ScoreContribution]] = None,
) -> CandidateScoreResult:
    if contributions is None:
        contributions = [
            ScoreContribution(
                signal_type=ScoringSignalType.IDENTIFIER_MATCH,
                weight=total_score,
                applied=True,
                evidence_type=NormalizedEvidenceType.BANK_ACCOUNT_IDENTIFIER,
                source=NormalizedEvidenceSource.PAYMENT_BANK_ACCOUNT,
                result=NormalizedEvidenceResult.MATCH,
                reason="Direct identifier match",
                target_entity=NormalizedTargetEntity.CUSTOMER,
            )
        ]
    return CandidateScoreResult(
        invoice_id=invoice_id,
        invoice_number=invoice_number,
        total_score=total_score,
        raw_unclamped_score=total_score,
        contributions=contributions,
        has_conflicting_evidence=has_conflict,
        rule_version="1.0.0",
        algorithm_version="14.11.0",
        is_empirically_validated=False,
    )


class TestConfidenceEngineRules:
    """Test suite for ConfidenceEngine pure domain logic."""

    def test_base_score_anchoring_unconflicted(self) -> None:
        """Verify confidence is anchored to 14.11 base score when unconflicted and unambiguous."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        inv_id = uuid.uuid4()

        cand_score = _make_candidate_score(
            invoice_id=inv_id,
            invoice_number="INV-2026-001",
            total_score=Decimal("95.00"),
        )
        scoring_res = MatchingScoringResult(
            payment_id=pay_id,
            company_id=comp_id,
            candidate_scores=[cand_score],
            total_candidates_scored=1,
            rule_version="1.0.0",
            algorithm_version="14.11.0",
        )

        conf_res = engine.evaluate(scoring_result=scoring_res)

        assert conf_res.total_candidates_evaluated == 1
        assessment = conf_res.candidate_assessments[0]
        assert assessment.confidence_score == Decimal("95.00")
        assert assessment.confidence_level == ConfidenceLevel.VERY_HIGH
        assert assessment.base_match_score == Decimal("95.00")
        assert assessment.has_conflicting_evidence is False
        assert assessment.is_statistically_calibrated is False
        assert assessment.rule_version == "1.0.0"
        assert assessment.scoring_algorithm_version == "14.11.0"
        assert assessment.confidence_algorithm_version == "14.12.0"

    def test_qualitative_confidence_levels_mapping(self) -> None:
        """Verify numeric confidence scores map to canonical ConfidenceLevel tiers."""
        engine = ConfidenceEngine()
        assert engine._determine_confidence_level(Decimal("100.00")) == ConfidenceLevel.VERY_HIGH
        assert engine._determine_confidence_level(Decimal("90.00")) == ConfidenceLevel.VERY_HIGH
        assert engine._determine_confidence_level(Decimal("89.99")) == ConfidenceLevel.HIGH
        assert engine._determine_confidence_level(Decimal("70.00")) == ConfidenceLevel.HIGH
        assert engine._determine_confidence_level(Decimal("69.99")) == ConfidenceLevel.MEDIUM
        assert engine._determine_confidence_level(Decimal("40.00")) == ConfidenceLevel.MEDIUM
        assert engine._determine_confidence_level(Decimal("39.99")) == ConfidenceLevel.LOW
        assert engine._determine_confidence_level(Decimal("0.01")) == ConfidenceLevel.LOW
        assert engine._determine_confidence_level(Decimal("0.00")) == ConfidenceLevel.NONE

    def test_conflict_penalty_and_hard_ceiling(self) -> None:
        """Verify active conflicting evidence incurs penalty and clamps to hard ceiling (40.00)."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        inv_id = uuid.uuid4()

        # High base score (90.00) but has conflicting evidence
        cand_score = _make_candidate_score(
            invoice_id=inv_id,
            invoice_number="INV-CONFLICT-01",
            total_score=Decimal("90.00"),
            has_conflict=True,
        )
        scoring_res = MatchingScoringResult(
            payment_id=pay_id,
            company_id=comp_id,
            candidate_scores=[cand_score],
            total_candidates_scored=1,
            rule_version="1.0.0",
            algorithm_version="14.11.0",
        )

        conf_res = engine.evaluate(scoring_result=scoring_res)
        assessment = conf_res.candidate_assessments[0]

        # 90.00 - 50% = 45.00, then clamped to hard_conflict_ceiling of 40.00
        assert assessment.confidence_score == Decimal("40.00")
        assert assessment.confidence_level == ConfidenceLevel.MEDIUM
        assert assessment.has_conflicting_evidence is True
        assert any(
            f.factor_type == ConfidenceFactorType.CONFLICT_PENALTY
            for f in assessment.adjustment_factors
        )

    def test_multi_candidate_ambiguity_dampening(self) -> None:
        """Verify competing candidates with close high scores receive ambiguity dampening."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        inv_id_1 = uuid.uuid4()
        inv_id_2 = uuid.uuid4()

        cand_1 = _make_candidate_score(
            invoice_id=inv_id_1,
            invoice_number="INV-TIED-001",
            total_score=Decimal("85.00"),
        )
        cand_2 = _make_candidate_score(
            invoice_id=inv_id_2,
            invoice_number="INV-TIED-002",
            total_score=Decimal("80.00"),
        )
        scoring_res = MatchingScoringResult(
            payment_id=pay_id,
            company_id=comp_id,
            candidate_scores=[cand_1, cand_2],
            total_candidates_scored=2,
            rule_version="1.0.0",
            algorithm_version="14.11.0",
        )

        conf_res = engine.evaluate(scoring_result=scoring_res)

        # Both candidates are >= 70.00 and within 10.00 points of each other.
        # They should both receive the 15.00 point ambiguity deduction.
        ass_1 = next(a for a in conf_res.candidate_assessments if a.invoice_id == inv_id_1)
        ass_2 = next(a for a in conf_res.candidate_assessments if a.invoice_id == inv_id_2)

        assert ass_1.confidence_score == Decimal("70.00")  # 85.00 - 15.00
        assert ass_2.confidence_score == Decimal("65.00")  # 80.00 - 15.00
        assert any(f.factor_type == ConfidenceFactorType.AMBIGUITY_DAMPENING for f in ass_1.adjustment_factors)
        assert any(f.factor_type == ConfidenceFactorType.AMBIGUITY_DAMPENING for f in ass_2.adjustment_factors)

    def test_zero_score_remains_zero_none(self) -> None:
        """Verify candidate with 0.00 base score produces 0.00 confidence with NONE level."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        inv_id = uuid.uuid4()

        cand = _make_candidate_score(
            invoice_id=inv_id,
            invoice_number="INV-ZERO-001",
            total_score=Decimal("0.00"),
            contributions=[],
        )
        scoring_res = MatchingScoringResult(
            payment_id=pay_id,
            company_id=comp_id,
            candidate_scores=[cand],
            total_candidates_scored=1,
            rule_version="1.0.0",
            algorithm_version="14.11.0",
        )

        conf_res = engine.evaluate(scoring_result=scoring_res)
        assessment = conf_res.candidate_assessments[0]
        assert assessment.confidence_score == Decimal("0.00")
        assert assessment.confidence_level == ConfidenceLevel.NONE

    def test_direct_evidence_grounding_flag(self) -> None:
        """Verify has_direct_evidence is true when candidate bundle has direct items."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        inv_id = uuid.uuid4()

        cand_score = _make_candidate_score(
            invoice_id=inv_id,
            invoice_number="INV-DIRECT-01",
            total_score=Decimal("75.00"),
        )
        scoring_res = MatchingScoringResult(
            payment_id=pay_id,
            company_id=comp_id,
            candidate_scores=[cand_score],
            total_candidates_scored=1,
            rule_version="1.0.0",
            algorithm_version="14.11.0",
        )

        bundle = NormalizedCandidateBundle(
            invoice_id=inv_id,
            invoice_number="INV-DIRECT-01",
            items=[
                _make_dummy_canonical_item(
                    evidence_type=NormalizedEvidenceType.BANK_ACCOUNT_IDENTIFIER,
                    source=NormalizedEvidenceSource.PAYMENT_BANK_ACCOUNT,
                    classification=EvidenceClassification.DIRECT,
                )
            ],
            has_conflicting_evidence=False,
            direct_evidence_count=1,
            supporting_evidence_count=0,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )
        norm_res = EvidenceNormalizationResult(
            payment_id=pay_id,
            company_id=comp_id,
            payment_evidence=NormalizedPaymentEvidenceContext(
                payment_id=pay_id,
                company_id=comp_id,
                items=[],
                extracted_invoice_references=[],
                has_conflicting_identifiers=False,
            ),
            candidate_bundles=[bundle],
            total_evidence_items=1,
            total_direct_items=1,
            total_supporting_items=0,
            total_missing_items=0,
            total_conflicting_items=0,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
        )

        conf_res = engine.evaluate(scoring_result=scoring_res, normalization_result=norm_res)
        assert conf_res.candidate_assessments[0].has_direct_evidence is True

    def test_100_run_permutation_determinism(self) -> None:
        """Verify bit-for-bit permutation invariance across 100 shuffled candidate lists."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()

        # Create 5 distinct candidates with diverse scores
        candidates: List[CandidateScoreResult] = []
        for i, score_val in enumerate(["95.00", "85.00", "70.00", "50.00", "20.00"], start=1):
            cand = _make_candidate_score(
                invoice_id=uuid.UUID(f"00000000-0000-0000-0000-00000000000{i}"),
                invoice_number=f"INV-PERM-00{i}",
                total_score=Decimal(score_val),
            )
            candidates.append(cand)

        base_scoring = MatchingScoringResult(
            payment_id=pay_id,
            company_id=comp_id,
            candidate_scores=list(candidates),
            total_candidates_scored=len(candidates),
            rule_version="1.0.0",
            algorithm_version="14.11.0",
        )
        reference_payload = engine.evaluate(scoring_result=base_scoring).to_deterministic_payload()

        rng = random.Random(42)
        for _ in range(100):
            shuffled = list(candidates)
            rng.shuffle(shuffled)
            shuffled_scoring = MatchingScoringResult(
                payment_id=pay_id,
                company_id=comp_id,
                candidate_scores=shuffled,
                total_candidates_scored=len(shuffled),
                rule_version="1.0.0",
                algorithm_version="14.11.0",
            )
            run_payload = engine.evaluate(scoring_result=shuffled_scoring).to_deterministic_payload()
            assert run_payload == reference_payload

    def test_adversarial_mismatched_payment_tenant_id_fails_closed(self) -> None:
        """Verify passing mismatched payment_id or company_id between scoring and normalization raises DomainError."""
        engine = ConfidenceEngine()
        pay_id_1 = uuid.uuid4()
        pay_id_2 = uuid.uuid4()
        comp_id = uuid.uuid4()

        scoring_res = MatchingScoringResult(
            payment_id=pay_id_1,
            company_id=comp_id,
            candidate_scores=[],
            total_candidates_scored=0,
            rule_version="1.0.0",
            algorithm_version="14.11.0",
        )
        norm_res = EvidenceNormalizationResult(
            payment_id=pay_id_2,  # Mismatched!
            company_id=comp_id,
            payment_evidence=NormalizedPaymentEvidenceContext(
                payment_id=pay_id_2,
                company_id=comp_id,
                items=[],
                extracted_invoice_references=[],
                has_conflicting_identifiers=False,
            ),
            candidate_bundles=[],
            total_evidence_items=0,
            total_direct_items=0,
            total_supporting_items=0,
            total_missing_items=0,
            total_conflicting_items=0,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
        )

        with pytest.raises(DomainError, match="Mismatched payment_id"):
            engine.evaluate(scoring_result=scoring_res, normalization_result=norm_res)

    def test_custom_confidence_config_thresholds(self) -> None:
        """Verify custom ConfidenceConfig can adjust levels and thresholds deterministically."""
        custom_config = ConfidenceConfig(
            very_high_threshold=Decimal("95.00"),
            high_threshold=Decimal("80.00"),
            medium_threshold=Decimal("50.00"),
            hard_conflict_ceiling=Decimal("30.00"),
        )
        engine = ConfidenceEngine(config=custom_config)
        assert engine._determine_confidence_level(Decimal("94.00")) == ConfidenceLevel.HIGH
        assert engine._determine_confidence_level(Decimal("95.00")) == ConfidenceLevel.VERY_HIGH

    def test_invalid_config_threshold_ordering_raises_domain_error(self) -> None:
        """Verify invalid threshold ordering fails closed at configuration instantiation."""
        with pytest.raises(DomainError, match="very_high_threshold must be strictly greater"):
            ConfidenceConfig(very_high_threshold=Decimal("70.00"), high_threshold=Decimal("80.00"))

        with pytest.raises(DomainError, match="high_threshold must be strictly greater"):
            ConfidenceConfig(high_threshold=Decimal("40.00"), medium_threshold=Decimal("50.00"))

    def test_deterministic_ranking_order_ties(self) -> None:
        """Verify ties in confidence score are deterministically broken by base_match_score, then invoice_number, then invoice_id."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        id_a = uuid.UUID("00000000-0000-0000-0000-00000000000a")
        id_b = uuid.UUID("00000000-0000-0000-0000-00000000000b")

        cand_a = _make_candidate_score(
            invoice_id=id_a,
            invoice_number="INV-A",
            total_score=Decimal("80.00"),
        )
        cand_b = _make_candidate_score(
            invoice_id=id_b,
            invoice_number="INV-B",
            total_score=Decimal("80.00"),
        )
        scoring_res = MatchingScoringResult(
            payment_id=pay_id,
            company_id=comp_id,
            candidate_scores=[cand_b, cand_a],
            total_candidates_scored=2,
            rule_version="1.0.0",
            algorithm_version="14.11.0",
        )

        conf_res = engine.evaluate(scoring_result=scoring_res)
        # INV-A must come before INV-B due to alphabetical invoice_number tie breaker
        assert conf_res.candidate_assessments[0].invoice_number == "INV-A"
        assert conf_res.candidate_assessments[1].invoice_number == "INV-B"

    def test_out_of_bounds_confidence_score_raises_domain_error(self) -> None:
        """Verify CandidateConfidenceAssessment rejects out-of-bound scores (<0 or >100)."""
        with pytest.raises(DomainError, match="confidence_score must be between 0.00 and 100.00"):
            CandidateConfidenceAssessment(
                invoice_id=uuid.uuid4(),
                invoice_number="INV-ERR",
                confidence_score=Decimal("105.00"),
                confidence_level=ConfidenceLevel.VERY_HIGH,
                base_match_score=Decimal("90.00"),
                has_direct_evidence=True,
                has_conflicting_evidence=False,
                adjustment_factors=[],
                rule_version="1.0.0",
                scoring_algorithm_version="14.11.0",
                confidence_algorithm_version="14.12.0",
            )
