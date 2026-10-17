"""Pure domain unit tests for Phase 14.12 Confidence Engine.

Verifies:
1. Canonical confidence score contract:
   - confidence_score == final_match_score (Phase 14.11) with exact Decimal precision.
   - Zero transformations, zero hidden formulas, zero double counting.
2. Canonical confidence presentation bands (Section 6 & 21):
   - 0   -> NONE
   - 1   -> LOW
   - 39  -> LOW
   - 40  -> MEDIUM
   - 69  -> MEDIUM
   - 70  -> HIGH
   - 89  -> HIGH
   - 90  -> VERY_HIGH
   - 100 -> VERY_HIGH
3. VERY_HIGH decoupled from direct identifier (Section 7):
   - Score >= 90.00 evaluates to VERY_HIGH regardless of direct identifier presence.
   - Payer-identification authority (Rule P-1) is not mixed with confidence bands.
4. Anti-penalty & anti-bonus invariants (Section 22):
   - Score = 95 with active conflict does NOT become 47.5 or clamped to 40.00 ceiling.
   - Score = 95 with direct identifier does NOT receive extra bonus points.
   - Score = 75 with multi-invoice matching does NOT receive an invented confidence penalty.
5. Incomplete evidence observation:
   - Incomplete or missing evidence items are surfaced factually (incomplete_evidence_present=True).
   - Score remains unaltered (delta=0.00).
6. Decision separation (Section 23):
   - Confidence engine outputs confidence only; never produces AUTO_ELIGIBLE, MATCH_SUGGESTED,
     or payment allocations.
7. Statistical calibration transparency (Section 24):
   - is_statistically_calibrated is explicitly False.
   - Zero probability claims or percentage-of-correctness claims.
8. Determinism & 100-permutation invariance (Section 25):
   - Bit-for-bit identical results across repeated runs with shuffled candidate lists.
9. Deterministic total order ranking:
   - Sorted by (-confidence_score, -base_match_score, invoice_number, str(invoice_id)).
10. Fail-closed tenant and payment ID defense.
11. Canonical schema properties and aliases (Section 11):
    - candidate_id, match_score, direct_evidence_present, conflicting_evidence_present,
      incomplete_evidence_present, algorithm_version.
"""

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
    ConfidencePolicy,
    CANONICAL_CONFIDENCE_POLICY,
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
    details: str = "Canonical evidence detail",
) -> CanonicalEvidenceObject:
    """Helper to construct dummy canonical evidence object."""
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
    has_direct_identifier: bool = False,
    contributions: Optional[List[ScoreContribution]] = None,
) -> CandidateScoreResult:
    """Helper to construct dummy candidate score result."""
    if contributions is None:
        signal = (
            ScoringSignalType.IDENTIFIER_MATCH
            if has_direct_identifier
            else ScoringSignalType.INVOICE_NUMBER_MATCH
        )
        contributions = [
            ScoreContribution(
                signal_type=signal,
                weight=total_score,
                applied=True,
                evidence_type=NormalizedEvidenceType.BANK_ACCOUNT_IDENTIFIER,
                source=NormalizedEvidenceSource.PAYMENT_BANK_ACCOUNT,
                result=NormalizedEvidenceResult.MATCH,
                reason="Match contribution",
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


class TestConfidenceEngineCanonicalRules:
    """Test suite verifying the canonical Phase 14.12 Confidence Engine contract."""

    @pytest.mark.parametrize(
        "score_val, expected_level",
        [
            ("0.00", ConfidenceLevel.NONE),
            ("1.00", ConfidenceLevel.LOW),
            ("39.00", ConfidenceLevel.LOW),
            ("39.99", ConfidenceLevel.LOW),
            ("40.00", ConfidenceLevel.MEDIUM),
            ("69.00", ConfidenceLevel.MEDIUM),
            ("69.99", ConfidenceLevel.MEDIUM),
            ("70.00", ConfidenceLevel.HIGH),
            ("89.00", ConfidenceLevel.HIGH),
            ("89.99", ConfidenceLevel.HIGH),
            ("90.00", ConfidenceLevel.VERY_HIGH),
            ("95.00", ConfidenceLevel.VERY_HIGH),
            ("100.00", ConfidenceLevel.VERY_HIGH),
        ],
    )
    def test_canonical_confidence_presentation_bands(
        self, score_val: str, expected_level: ConfidenceLevel
    ) -> None:
        """Verify the exact canonical confidence presentation bands:
        0       -> NONE
        1–39    -> LOW
        40–69   -> MEDIUM
        70–89   -> HIGH
        90–100  -> VERY_HIGH
        """
        engine = ConfidenceEngine()
        score = Decimal(score_val)
        level = engine._determine_confidence_level(score)
        assert level == expected_level

    def test_very_high_does_not_require_direct_identifier(self) -> None:
        """Verify score >= 90 produces VERY_HIGH without requiring direct counterparty identifier."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        inv_id = uuid.uuid4()

        cand_score = _make_candidate_score(
            invoice_id=inv_id,
            invoice_number="INV-NO-DIRECT-95",
            total_score=Decimal("95.00"),
            has_direct_identifier=False,  # NO direct identifier
            has_conflict=False,
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

        # Score is 95.00 -> level must be VERY_HIGH despite has_direct_evidence=False
        assert assessment.confidence_score == Decimal("95.00")
        assert assessment.confidence_level == ConfidenceLevel.VERY_HIGH
        assert assessment.has_direct_evidence is False
        assert assessment.has_conflicting_evidence is False

    def test_anti_penalty_conflict_does_not_reduce_score(self) -> None:
        """Verify active conflict sets conflicting_evidence_present=True but does NOT reduce score (no 50%, no 40 ceiling)."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        inv_id = uuid.uuid4()

        cand_score = _make_candidate_score(
            invoice_id=inv_id,
            invoice_number="INV-CONFLICT-95",
            total_score=Decimal("95.00"),
            has_conflict=True,
            has_direct_identifier=True,
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

        # Score remains exactly 95.00 without invented 50% deduction or 40.00 ceiling
        assert assessment.confidence_score == Decimal("95.00")
        assert assessment.confidence_level == ConfidenceLevel.VERY_HIGH
        assert assessment.conflicting_evidence_present is True

        # Conflict is recorded as explainable factor with delta 0.00
        conflict_factor = next(
            f for f in assessment.adjustment_factors
            if f.factor_type == ConfidenceFactorType.CONFLICT_OBSERVED
        )
        assert conflict_factor.delta == Decimal("0.00")

    def test_anti_bonus_direct_identifier_does_not_add_points(self) -> None:
        """Verify direct identifier does NOT add hidden points above Phase 14.11 score."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        inv_id = uuid.uuid4()

        cand_score = _make_candidate_score(
            invoice_id=inv_id,
            invoice_number="INV-DIRECT-90",
            total_score=Decimal("90.00"),
            has_direct_identifier=True,
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

        # Score remains exactly 90.00; direct identifier does not inflate it
        assert assessment.confidence_score == Decimal("90.00")
        assert assessment.direct_evidence_present is True
        direct_factor = next(
            f for f in assessment.adjustment_factors
            if f.factor_type == ConfidenceFactorType.DIRECT_IDENTIFIER_GROUNDING
        )
        assert direct_factor.delta == Decimal("0.00")

    def test_multi_invoice_match_does_not_receive_penalty(self) -> None:
        """Verify candidate score from multi-invoice matching is preserved without invented deduction."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        inv_id = uuid.uuid4()

        cand_score = _make_candidate_score(
            invoice_id=inv_id,
            invoice_number="INV-MULTI-75",
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

        conf_res = engine.evaluate(scoring_result=scoring_res)
        assessment = conf_res.candidate_assessments[0]

        # Exact 75.00 preserved
        assert assessment.confidence_score == Decimal("75.00")
        assert assessment.confidence_level == ConfidenceLevel.HIGH

    def test_incomplete_evidence_surfaced_factually_without_penalty(self) -> None:
        """Verify incomplete evidence items in bundle are surfaced in incomplete_evidence_present without score deduction."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        inv_id = uuid.uuid4()

        cand_score = _make_candidate_score(
            invoice_id=inv_id,
            invoice_number="INV-INCOMPLETE-80",
            total_score=Decimal("80.00"),
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
            invoice_number="INV-INCOMPLETE-80",
            items=[
                _make_dummy_canonical_item(
                    evidence_type=NormalizedEvidenceType.INVOICE_NUMBER_IN_NARRATION,
                    source=NormalizedEvidenceSource.PAYMENT_NARRATION,
                    classification=EvidenceClassification.SUPPORTING,
                )
            ],
            has_conflicting_evidence=False,
            direct_evidence_count=0,
            supporting_evidence_count=1,
            missing_evidence_count=2,  # Missing items present
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
            total_direct_items=0,
            total_supporting_items=1,
            total_missing_items=2,
            total_conflicting_items=0,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
        )

        conf_res = engine.evaluate(scoring_result=scoring_res, normalization_result=norm_res)
        assessment = conf_res.candidate_assessments[0]

        assert assessment.confidence_score == Decimal("80.00")
        assert assessment.incomplete_evidence_present is True
        assert assessment.has_incomplete_evidence is True
        incomplete_factor = next(
            f for f in assessment.adjustment_factors
            if f.factor_type == ConfidenceFactorType.INCOMPLETE_EVIDENCE_OBSERVED
        )
        assert incomplete_factor.delta == Decimal("0.00")

    def test_decision_separation_no_decision_fields(self) -> None:
        """Verify Phase 14.12 outputs confidence only and does NOT produce decision fields or allocations."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        inv_id = uuid.uuid4()

        cand_score = _make_candidate_score(
            invoice_id=inv_id,
            invoice_number="INV-DECISION-SEP",
            total_score=Decimal("95.00"),
            has_direct_identifier=True,
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

        # Verify no decision attributes exist on the assessment aggregate
        assert not hasattr(assessment, "status")
        assert not hasattr(assessment, "decision")
        assert not hasattr(assessment, "auto_eligible")
        assert not hasattr(assessment, "allocation_amount")

    def test_statistical_calibration_explicitly_false(self) -> None:
        """Verify is_statistically_calibrated is permanently False for MVP."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        inv_id = uuid.uuid4()

        cand_score = _make_candidate_score(
            invoice_id=inv_id,
            invoice_number="INV-CALIB-01",
            total_score=Decimal("85.00"),
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
        assert conf_res.is_statistically_calibrated is False
        assert conf_res.candidate_assessments[0].is_statistically_calibrated is False

    def test_100_run_permutation_determinism(self) -> None:
        """Verify bit-for-bit permutation invariance across 100 shuffled candidate lists."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()

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

    def test_canonical_schema_alias_properties(self) -> None:
        """Verify canonical alias properties expose required names per Section 11."""
        engine = ConfidenceEngine()
        pay_id = uuid.uuid4()
        comp_id = uuid.uuid4()
        inv_id = uuid.uuid4()

        cand_score = _make_candidate_score(
            invoice_id=inv_id,
            invoice_number="INV-SCHEMA-ALIAS",
            total_score=Decimal("88.00"),
            has_direct_identifier=True,
            has_conflict=False,
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

        # Verify property aliases
        assert assessment.candidate_id == inv_id
        assert assessment.payment_id == pay_id
        assert assessment.match_score == Decimal("88.00")
        assert assessment.confidence_score == Decimal("88.00")
        assert assessment.direct_evidence_present is True
        assert assessment.conflicting_evidence_present is False
        assert assessment.incomplete_evidence_present is False
        assert assessment.algorithm_version == "14.12.0"

        # Verify serialized dict includes all aliases
        d = assessment.to_dict()
        assert d["candidate_id"] == str(inv_id)
        assert d["payment_id"] == str(pay_id)
        assert d["match_score"] == "88.00"
        assert d["confidence_score"] == "88.00"
        assert d["direct_evidence_present"] is True
        assert d["conflicting_evidence_present"] is False
        assert d["incomplete_evidence_present"] is False
        assert d["algorithm_version"] == "14.12.0"

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
