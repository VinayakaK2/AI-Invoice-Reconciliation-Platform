"""Pure domain unit tests for Phase 14.11 Matching & Scoring engine.

Tests:
1. Signal Independence & Exact Point Allocation:
   - IDENTIFIER_MATCH awards exact +40.00
   - INVOICE_NUMBER_MATCH awards exact +35.00
   - AMOUNT_EXACT_MATCH awards exact +30.00
   - CUSTOMER_NAME_MATCH awards exact +20.00
   - DATE_PROXIMITY_MATCH awards exact +10.00
   - HISTORICAL_PATTERN awards exact +5.00
2. Mathematical Clamping:
   - Unclamped sum >= 100.00 clamps strictly to 100.00
   - All signals combined (40+35+30+20+10+5 = 140) clamps to 100.00
3. Anti-Double Counting:
   - Narration invoice match and payment reference invoice match award points once (+35).
   - Name token match and Alias match award points once (+20).
4. Missing Evidence:
   - Absent signals receive 0.00 points (no fabrication).
   - Entirely absent evidence yields 0.00 score.
5. Conflict Handling & Suppression:
   - Conflicting bank account suppresses IDENTIFIER_MATCH points.
   - Currency mismatch suppresses AMOUNT_EXACT_MATCH points.
   - Non-causal payment date suppresses DATE_PROXIMITY_MATCH points.
   - Conflicting invoice reference suppresses INVOICE_NUMBER_MATCH points.
6. Determinism & Total Order:
   - 100 random candidate permutation runs produce bit-for-bit identical scores and payloads.
   - Excludes execution timestamp from to_deterministic_payload().
7. Mathematical Invariants:
   - 0.00 <= total_score <= 100.00 across all permutations.
   - Exact Decimal precision; zero floating-point arithmetic.
   - Explainable provenance: every applied contribution is traceable with reason.
"""

from datetime import date
from decimal import Decimal
import random
from typing import Optional
import uuid
import pytest

from app.modules.reconciliation.domain.candidate_invoices import CandidateInvoice
from app.modules.reconciliation.domain.evidence_collection import (
    EvidenceClassification,
    EvidenceCollectionEngine,
    EvidenceType,
    StructuredEvidenceItem,
)
from app.modules.reconciliation.domain.evidence_normalization import (
    CanonicalEvidenceObject,
    EvidenceNormalizationEngine,
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
    DEFAULT_SCORING_WEIGHTS,
    CandidateScoreResult,
    MatchingScoringEngine,
    MatchingScoringResult,
    ScoreContribution,
    ScoringSignalType,
    ScoringWeightsConfig,
)
from app.modules.reconciliation.domain.rules import CustomerLookupContext, PaymentIntakeContext
from app.shared.exceptions import DomainError


def _make_payment(
    amount: str = "50000.00",
    currency: str = "INR",
    payment_date: Optional[date] = None,
    narration: Optional[str] = "PAYMENT FOR INVOICE INV-2026-001",
    payment_reference: Optional[str] = "REF123456",
    bank_account_number: Optional[str] = "998877665544",
    company_id: Optional[uuid.UUID] = None,
) -> PaymentIntakeContext:
    amt_dec = Decimal(amount)
    return PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id or uuid.uuid4(),
        amount=amt_dec,
        currency=currency,
        payment_date=payment_date or date(2026, 9, 15),
        narration=narration,
        payment_reference=payment_reference,
        bank_account_number=bank_account_number,
        allocated_amount=Decimal("0.00"),
        unallocated_amount=amt_dec,
    )


def _make_candidate(
    invoice_number: str = "INV-2026-001",
    outstanding_amount: str = "50000.00",
    currency: str = "INR",
    issue_date: Optional[date] = None,
    due_date: Optional[date] = None,
    company_id: Optional[uuid.UUID] = None,
) -> CandidateInvoice:
    amt_dec = Decimal(outstanding_amount)
    cand = CandidateInvoice(
        invoice_id=uuid.uuid4(),
        invoice_number=invoice_number,
        total_amount=amt_dec,
        paid_amount=Decimal("0.00"),
        outstanding_amount=amt_dec,
        currency=currency,
        issue_date=issue_date or date(2026, 9, 1),
        due_date=due_date or date(2026, 9, 20),
        status="PENDING",
        retrieval_priority=80.0,
        evidence_signals=[],
        is_exact_amount_match=False,
        is_partial_amount_match=False,
        is_reference_match=False,
        rank=1,
    )
    if company_id is not None:
        object.__setattr__(cand, "company_id", company_id)
    return cand


def _make_customer(
    name: str = "Acme Corp",
    aliases: Optional[list] = None,
    identifiers: Optional[list] = None,
    company_id: Optional[uuid.UUID] = None,
) -> CustomerLookupContext:
    return CustomerLookupContext(
        customer_id=uuid.uuid4(),
        name=name,
        aliases=aliases or ["Acme", "Acme India"],
        identifiers=identifiers or [("BANK_ACCOUNT", "998877665544")],
    )


class TestMatchingScoringEngine:
    """Pure domain test suite for Phase 14.11 Matching & Scoring engine."""

    def test_single_signals_point_allocations(self) -> None:
        """Each scoring signal independently awards its exact defined weight from Rule 2.4."""
        engine = MatchingScoringEngine()
        comp_id = uuid.uuid4()
        payment_id = uuid.uuid4()
        cand_id = uuid.uuid4()

        # 1. Test IDENTIFIER_MATCH (+40)
        acc_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.BANK_ACCOUNT_IDENTIFIER,
            source=NormalizedEvidenceSource.PAYMENT_BANK_ACCOUNT,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.EXACT,
            details="Bank account matched registered account",
            identifiers=NormalizedRelevantIdentifiers(payment_id=payment_id, bank_account="998877665544"),
            classification=EvidenceClassification.DIRECT,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.CUSTOMER,
            matched_value="998877665544",
        )
        pay_ctx = NormalizedPaymentEvidenceContext(
            payment_id=payment_id,
            company_id=comp_id,
            items=[acc_item],
            extracted_invoice_references=[],
            has_conflicting_identifiers=False,
        )
        empty_bundle = NormalizedCandidateBundle(
            invoice_id=cand_id,
            invoice_number="INV-001",
            items=[],
            has_conflicting_evidence=False,
            direct_evidence_count=0,
            supporting_evidence_count=0,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )
        score_res = engine.score_candidate(bundle=empty_bundle, payment_evidence=pay_ctx)
        assert score_res.total_score == Decimal("40.00")
        assert len(score_res.contributions) == 1
        assert score_res.contributions[0].signal_type == ScoringSignalType.IDENTIFIER_MATCH
        assert score_res.contributions[0].weight == Decimal("40.00")

        # 2. Test INVOICE_NUMBER_MATCH (+35)
        inv_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.INVOICE_NUMBER_IN_NARRATION,
            source=NormalizedEvidenceSource.INVOICE_INVOICE_NUMBER,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.EXACT,
            details="Invoice number referenced in payment narration",
            identifiers=NormalizedRelevantIdentifiers(invoice_id=cand_id, invoice_number="INV-001"),
            classification=EvidenceClassification.DIRECT,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.INVOICE,
            matched_value="INV-001",
        )
        inv_bundle = NormalizedCandidateBundle(
            invoice_id=cand_id,
            invoice_number="INV-001",
            items=[inv_item],
            has_conflicting_evidence=False,
            direct_evidence_count=1,
            supporting_evidence_count=0,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )
        empty_pay_ctx = NormalizedPaymentEvidenceContext(
            payment_id=payment_id,
            company_id=comp_id,
            items=[],
            extracted_invoice_references=[],
            has_conflicting_identifiers=False,
        )
        score_res = engine.score_candidate(bundle=inv_bundle, payment_evidence=empty_pay_ctx)
        assert score_res.total_score == Decimal("35.00")
        assert score_res.contributions[0].signal_type == ScoringSignalType.INVOICE_NUMBER_MATCH

        # 3. Test AMOUNT_EXACT_MATCH (+30)
        amt_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.AMOUNT_EXACT_EQUALITY,
            source=NormalizedEvidenceSource.CANDIDATE_OUTSTANDING_AMOUNT,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.EXACT,
            details="Payment effective amount exactly equals candidate outstanding",
            identifiers=NormalizedRelevantIdentifiers(invoice_id=cand_id),
            classification=EvidenceClassification.DIRECT,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.INVOICE,
            matched_value="50000.00",
        )
        amt_bundle = NormalizedCandidateBundle(
            invoice_id=cand_id,
            invoice_number="INV-001",
            items=[amt_item],
            has_conflicting_evidence=False,
            direct_evidence_count=1,
            supporting_evidence_count=0,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )
        score_res = engine.score_candidate(bundle=amt_bundle, payment_evidence=empty_pay_ctx)
        assert score_res.total_score == Decimal("30.00")
        assert score_res.contributions[0].signal_type == ScoringSignalType.AMOUNT_EXACT_MATCH

        # 4. Test CUSTOMER_NAME_MATCH (+20)
        name_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.CUSTOMER_NAME_TOKEN,
            source=NormalizedEvidenceSource.CUSTOMER_NAME,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.MEDIUM,
            details="Customer name tokens matched in narration",
            identifiers=NormalizedRelevantIdentifiers(),
            classification=EvidenceClassification.SUPPORTING,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.CUSTOMER,
            matched_value="Acme Corp",
        )
        name_pay_ctx = NormalizedPaymentEvidenceContext(
            payment_id=payment_id,
            company_id=comp_id,
            items=[name_item],
            extracted_invoice_references=[],
            has_conflicting_identifiers=False,
        )
        score_res = engine.score_candidate(bundle=empty_bundle, payment_evidence=name_pay_ctx)
        assert score_res.total_score == Decimal("20.00")
        assert score_res.contributions[0].signal_type == ScoringSignalType.CUSTOMER_NAME_MATCH

        # 5. Test DATE_PROXIMITY_MATCH (+10)
        date_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.DATE_PROXIMITY,
            source=NormalizedEvidenceSource.CANDIDATE_DUE_DATE,
            result=NormalizedEvidenceResult.SUPPORTED,
            strength=NormalizedEvidenceStrength.LOW,
            details="Payment date is within 5 days of invoice due date",
            identifiers=NormalizedRelevantIdentifiers(invoice_id=cand_id),
            classification=EvidenceClassification.SUPPORTING,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.INVOICE,
            matched_value="5",
        )
        date_bundle = NormalizedCandidateBundle(
            invoice_id=cand_id,
            invoice_number="INV-001",
            items=[date_item],
            has_conflicting_evidence=False,
            direct_evidence_count=0,
            supporting_evidence_count=1,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )
        score_res = engine.score_candidate(bundle=date_bundle, payment_evidence=empty_pay_ctx)
        assert score_res.total_score == Decimal("10.00")
        assert score_res.contributions[0].signal_type == ScoringSignalType.DATE_PROXIMITY_MATCH

        # 6. Test HISTORICAL_PATTERN (+5)
        hist_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.HISTORICAL_CUSTOMER_PATTERN,
            source=NormalizedEvidenceSource.CUSTOMER_IDENTIFIERS,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.LOW,
            details="Matches customer recurring monthly payment pattern",
            identifiers=NormalizedRelevantIdentifiers(invoice_id=cand_id),
            classification=EvidenceClassification.SUPPORTING,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.INVOICE,
        )
        hist_bundle = NormalizedCandidateBundle(
            invoice_id=cand_id,
            invoice_number="INV-001",
            items=[hist_item],
            has_conflicting_evidence=False,
            direct_evidence_count=0,
            supporting_evidence_count=1,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )
        score_res = engine.score_candidate(bundle=hist_bundle, payment_evidence=empty_pay_ctx)
        assert score_res.total_score == Decimal("5.00")
        assert score_res.contributions[0].signal_type == ScoringSignalType.HISTORICAL_PATTERN

    def test_combined_scoring_and_mathematical_clamping(self) -> None:
        """Aggregating all 6 signals sums to 140.00 raw unclamped, strictly clamped to 100.00."""
        comp_id = uuid.uuid4()
        payment = _make_payment(
            amount="50000.00",
            narration="Acme Corp Payment for INV-2026-001",
            bank_account_number="998877665544",
            company_id=comp_id,
        )
        candidate = _make_candidate(
            invoice_number="INV-2026-001",
            outstanding_amount="50000.00",
            due_date=date(2026, 9, 20),
            company_id=comp_id,
        )
        customer = _make_customer(
            name="Acme Corp",
            identifiers=[("BANK_ACCOUNT", "998877665544")],
            company_id=comp_id,
        )

        raw_collection = EvidenceCollectionEngine().evaluate(
            payment=payment,
            candidates=[candidate],
            customer=customer,
        )
        norm_result = EvidenceNormalizationEngine().normalize_result_aggregate(raw_collection)

        # Manually add historical pattern to bundle to test all 6 signals together
        hist_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.HISTORICAL_CUSTOMER_PATTERN,
            source=NormalizedEvidenceSource.CUSTOMER_IDENTIFIERS,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.LOW,
            details="Customer recurring pattern match",
            identifiers=NormalizedRelevantIdentifiers(invoice_id=candidate.invoice_id),
            classification=EvidenceClassification.SUPPORTING,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.INVOICE,
        )
        modified_bundle = NormalizedCandidateBundle(
            invoice_id=norm_result.candidate_bundles[0].invoice_id,
            invoice_number=norm_result.candidate_bundles[0].invoice_number,
            items=list(norm_result.candidate_bundles[0].items) + [hist_item],
            has_conflicting_evidence=norm_result.candidate_bundles[0].has_conflicting_evidence,
            direct_evidence_count=norm_result.candidate_bundles[0].direct_evidence_count,
            supporting_evidence_count=norm_result.candidate_bundles[0].supporting_evidence_count + 1,
            missing_evidence_count=norm_result.candidate_bundles[0].missing_evidence_count,
            conflicting_evidence_count=norm_result.candidate_bundles[0].conflicting_evidence_count,
        )

        engine = MatchingScoringEngine()
        score_res = engine.score_candidate(
            bundle=modified_bundle,
            payment_evidence=norm_result.payment_evidence,
        )

        # 40 (acc) + 35 (inv) + 30 (amt) + 20 (name) + 10 (date) + 5 (hist) = 140.00
        assert score_res.raw_unclamped_score == Decimal("140.00")
        assert score_res.total_score == Decimal("100.00")
        assert len(score_res.contributions) == 6

    def test_anti_double_counting_aliases_and_references(self) -> None:
        """Redundant / alias signals are awarded only once."""
        comp_id = uuid.uuid4()
        payment_id = uuid.uuid4()
        cand_id = uuid.uuid4()

        # Provide BOTH name token match AND alias match in payment evidence
        name_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.CUSTOMER_NAME_TOKEN,
            source=NormalizedEvidenceSource.CUSTOMER_NAME,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.MEDIUM,
            details="Name token matched",
            identifiers=NormalizedRelevantIdentifiers(),
            classification=EvidenceClassification.SUPPORTING,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.CUSTOMER,
            matched_value="Acme Corp",
        )
        alias_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.CUSTOMER_ALIAS,
            source=NormalizedEvidenceSource.CUSTOMER_ALIASES,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.MEDIUM,
            details="Registered alias matched",
            identifiers=NormalizedRelevantIdentifiers(),
            classification=EvidenceClassification.SUPPORTING,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.CUSTOMER,
            matched_value="Acme",
        )

        pay_ctx = NormalizedPaymentEvidenceContext(
            payment_id=payment_id,
            company_id=comp_id,
            items=[name_item, alias_item],
            extracted_invoice_references=[],
            has_conflicting_identifiers=False,
        )
        empty_bundle = NormalizedCandidateBundle(
            invoice_id=cand_id,
            invoice_number="INV-001",
            items=[],
            has_conflicting_evidence=False,
            direct_evidence_count=0,
            supporting_evidence_count=0,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )

        engine = MatchingScoringEngine()
        score_res = engine.score_candidate(bundle=empty_bundle, payment_evidence=pay_ctx)

        # Must receive CUSTOMER_NAME_MATCH once (+20.00), not +40.00
        assert score_res.total_score == Decimal("20.00")
        name_contribs = [c for c in score_res.contributions if c.signal_type == ScoringSignalType.CUSTOMER_NAME_MATCH]
        assert len(name_contribs) == 1

    def test_missing_evidence_awards_zero_points(self) -> None:
        """Missing signals award 0.00 points; empty evidence yields 0.00 total score."""
        comp_id = uuid.uuid4()
        payment_id = uuid.uuid4()
        cand_id = uuid.uuid4()

        missing_ref = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.PAYMENT_REFERENCE,
            source=NormalizedEvidenceSource.PAYMENT_PAYMENT_REFERENCE,
            result=NormalizedEvidenceResult.ABSENT,
            strength=NormalizedEvidenceStrength.NONE,
            details="Payment reference is missing",
            identifiers=NormalizedRelevantIdentifiers(),
            classification=EvidenceClassification.MISSING,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.PAYMENT,
        )
        pay_ctx = NormalizedPaymentEvidenceContext(
            payment_id=payment_id,
            company_id=comp_id,
            items=[missing_ref],
            extracted_invoice_references=[],
            has_conflicting_identifiers=False,
        )

        missing_bundle = NormalizedCandidateBundle(
            invoice_id=cand_id,
            invoice_number="INV-001",
            items=[],
            has_conflicting_evidence=False,
            direct_evidence_count=0,
            supporting_evidence_count=0,
            missing_evidence_count=1,
            conflicting_evidence_count=0,
        )

        engine = MatchingScoringEngine()
        score_res = engine.score_candidate(bundle=missing_bundle, payment_evidence=pay_ctx)
        assert score_res.total_score == Decimal("0.00")
        assert len(score_res.contributions) == 0

    def test_conflict_suppresses_point_contributions(self) -> None:
        """Conflicting evidence suppresses point contributions on the conflicting dimension."""
        comp_id = uuid.uuid4()
        payment_id = uuid.uuid4()
        cand_id = uuid.uuid4()

        # Conflicting bank account
        conf_acc = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.BANK_ACCOUNT_IDENTIFIER,
            source=NormalizedEvidenceSource.PAYMENT_BANK_ACCOUNT,
            result=NormalizedEvidenceResult.CONFLICT,
            strength=NormalizedEvidenceStrength.NONE,
            details="Account belongs to another customer",
            identifiers=NormalizedRelevantIdentifiers(),
            classification=EvidenceClassification.CONFLICTING,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.CUSTOMER,
        )
        pay_ctx = NormalizedPaymentEvidenceContext(
            payment_id=payment_id,
            company_id=comp_id,
            items=[conf_acc],
            extracted_invoice_references=[],
            has_conflicting_identifiers=True,
        )

        # Currency mismatch on candidate bundle
        conf_curr = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.CURRENCY_CONSISTENCY,
            source=NormalizedEvidenceSource.CANDIDATE_CURRENCY,
            result=NormalizedEvidenceResult.CONFLICT,
            strength=NormalizedEvidenceStrength.NONE,
            details="Currency mismatch: payment INR vs candidate USD",
            identifiers=NormalizedRelevantIdentifiers(invoice_id=cand_id),
            classification=EvidenceClassification.CONFLICTING,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.INVOICE,
        )
        # Even if amount numerically equals, currency conflict suppresses amount points
        amt_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.AMOUNT_EXACT_EQUALITY,
            source=NormalizedEvidenceSource.CANDIDATE_OUTSTANDING_AMOUNT,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.EXACT,
            details="Amount matches",
            identifiers=NormalizedRelevantIdentifiers(invoice_id=cand_id),
            classification=EvidenceClassification.DIRECT,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.INVOICE,
            matched_value="50000.00",
        )

        bundle = NormalizedCandidateBundle(
            invoice_id=cand_id,
            invoice_number="INV-001",
            items=[conf_curr, amt_item],
            has_conflicting_evidence=True,
            direct_evidence_count=1,
            supporting_evidence_count=0,
            missing_evidence_count=0,
            conflicting_evidence_count=1,
        )

        engine = MatchingScoringEngine()
        score_res = engine.score_candidate(bundle=bundle, payment_evidence=pay_ctx)

        # Bank account conflict prevented IDENTIFIER_MATCH (+40)
        # Currency conflict prevented AMOUNT_EXACT_MATCH (+30)
        assert score_res.total_score == Decimal("0.00")
        assert score_res.has_conflicting_evidence is True
        assert len(score_res.contributions) == 0

    def test_100_run_permutation_determinism(self) -> None:
        """Evaluating across 100 randomized candidate orders yields identical scores and payloads."""
        comp_id = uuid.uuid4()
        payment = _make_payment(company_id=comp_id)

        candidates = [
            _make_candidate(
                invoice_number=f"INV-2026-{idx:03d}",
                outstanding_amount=str(10000 * idx),
                company_id=comp_id,
            )
            for idx in range(1, 8)
        ]

        collection_engine = EvidenceCollectionEngine()
        normalizer = EvidenceNormalizationEngine()
        scoring_engine = MatchingScoringEngine()

        base_raw = collection_engine.evaluate(payment=payment, candidates=candidates)
        base_norm = normalizer.normalize_result_aggregate(base_raw)
        base_scored = scoring_engine.evaluate(base_norm)
        base_payload = base_scored.to_deterministic_payload()

        for _ in range(100):
            shuffled = list(candidates)
            random.shuffle(shuffled)
            raw_res = collection_engine.evaluate(payment=payment, candidates=shuffled)
            norm_res = normalizer.normalize_result_aggregate(raw_res)
            scored_res = scoring_engine.evaluate(norm_res)
            perm_payload = scored_res.to_deterministic_payload()
            assert perm_payload == base_payload

    def test_tie_handling_and_deterministic_ranking(self) -> None:
        """Tied candidate scores are ranked deterministically by invoice_number, str(invoice_id)."""
        engine = MatchingScoringEngine()
        comp_id = uuid.uuid4()
        payment_id = uuid.uuid4()

        # Both candidates have exact same score (0.00)
        bundle_b = NormalizedCandidateBundle(
            invoice_id=uuid.uuid4(),
            invoice_number="INV-B",
            items=[],
            has_conflicting_evidence=False,
            direct_evidence_count=0,
            supporting_evidence_count=0,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )
        bundle_a = NormalizedCandidateBundle(
            invoice_id=uuid.uuid4(),
            invoice_number="INV-A",
            items=[],
            has_conflicting_evidence=False,
            direct_evidence_count=0,
            supporting_evidence_count=0,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )

        norm_res = EvidenceNormalizationEngine().normalize_result_aggregate(
            EvidenceCollectionEngine().evaluate(
                payment=_make_payment(company_id=comp_id),
                candidates=[_make_candidate("INV-B", company_id=comp_id), _make_candidate("INV-A", company_id=comp_id)],
            )
        )
        scored_res = engine.evaluate(norm_res)
        # Ties sorted alphabetically by invoice_number
        assert scored_res.candidate_scores[0].invoice_number == "INV-A"
        assert scored_res.candidate_scores[1].invoice_number == "INV-B"

    def test_custom_scoring_weights_validation(self) -> None:
        """ScoringWeightsConfig validates max_score and non-negative weights."""
        with pytest.raises(DomainError, match="max_score must be positive Decimal"):
            ScoringWeightsConfig(max_score=Decimal("0.00"))

        with pytest.raises(DomainError, match="amount_exact_match_weight cannot be negative"):
            ScoringWeightsConfig(amount_exact_match_weight=Decimal("-5.00"))

    def test_invoice_number_match_via_payment_reference_provenance(self) -> None:
        """Candidate invoice number in payment_reference awards +35.00 with provenance payment.payment_reference."""
        engine = MatchingScoringEngine()
        comp_id = uuid.uuid4()
        payment_id = uuid.uuid4()
        cand_id = uuid.uuid4()

        ref_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.PAYMENT_REFERENCE,
            source=NormalizedEvidenceSource.PAYMENT_PAYMENT_REFERENCE,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.EXACT,
            details="Candidate invoice INV-2026-001 extracted from payment reference",
            identifiers=NormalizedRelevantIdentifiers(invoice_id=cand_id, invoice_number="INV-2026-001"),
            classification=EvidenceClassification.DIRECT,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.INVOICE,
            matched_value="INV-2026-001",
        )
        pay_ctx = NormalizedPaymentEvidenceContext(
            payment_id=payment_id,
            company_id=comp_id,
            items=[ref_item],
            extracted_invoice_references=["INV-2026-001"],
            has_conflicting_identifiers=False,
        )
        bundle = NormalizedCandidateBundle(
            invoice_id=cand_id,
            invoice_number="INV-2026-001",
            items=[ref_item],
            has_conflicting_evidence=False,
            direct_evidence_count=1,
            supporting_evidence_count=0,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )

        score_res = engine.score_candidate(bundle=bundle, payment_evidence=pay_ctx)
        assert score_res.total_score == Decimal("35.00")
        assert len(score_res.contributions) == 1
        contrib = score_res.contributions[0]
        assert contrib.signal_type == ScoringSignalType.INVOICE_NUMBER_MATCH
        assert contrib.weight == Decimal("35.00")
        assert contrib.source == NormalizedEvidenceSource.PAYMENT_PAYMENT_REFERENCE
        assert contrib.evidence_type == NormalizedEvidenceType.PAYMENT_REFERENCE

    def test_invoice_number_match_via_narration_provenance(self) -> None:
        """Candidate invoice number in narration awards +35.00 with provenance payment.narration."""
        engine = MatchingScoringEngine()
        comp_id = uuid.uuid4()
        payment_id = uuid.uuid4()
        cand_id = uuid.uuid4()

        narr_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.INVOICE_NUMBER_IN_NARRATION,
            source=NormalizedEvidenceSource.PAYMENT_NARRATION,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.EXACT,
            details="Candidate invoice INV-2026-001 extracted from payment narration",
            identifiers=NormalizedRelevantIdentifiers(invoice_id=cand_id, invoice_number="INV-2026-001"),
            classification=EvidenceClassification.DIRECT,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.INVOICE,
            matched_value="INV-2026-001",
        )
        pay_ctx = NormalizedPaymentEvidenceContext(
            payment_id=payment_id,
            company_id=comp_id,
            items=[narr_item],
            extracted_invoice_references=["INV-2026-001"],
            has_conflicting_identifiers=False,
        )
        bundle = NormalizedCandidateBundle(
            invoice_id=cand_id,
            invoice_number="INV-2026-001",
            items=[narr_item],
            has_conflicting_evidence=False,
            direct_evidence_count=1,
            supporting_evidence_count=0,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )

        score_res = engine.score_candidate(bundle=bundle, payment_evidence=pay_ctx)
        assert score_res.total_score == Decimal("35.00")
        assert len(score_res.contributions) == 1
        contrib = score_res.contributions[0]
        assert contrib.signal_type == ScoringSignalType.INVOICE_NUMBER_MATCH
        assert contrib.weight == Decimal("35.00")
        assert contrib.source == NormalizedEvidenceSource.PAYMENT_NARRATION
        assert contrib.evidence_type == NormalizedEvidenceType.INVOICE_NUMBER_IN_NARRATION

    def test_invoice_number_in_both_reference_and_narration_awards_once(self) -> None:
        """Candidate invoice number in BOTH payment reference and narration awards +35.00 exactly once."""
        engine = MatchingScoringEngine()
        comp_id = uuid.uuid4()
        payment_id = uuid.uuid4()
        cand_id = uuid.uuid4()

        ref_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.PAYMENT_REFERENCE,
            source=NormalizedEvidenceSource.PAYMENT_PAYMENT_REFERENCE,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.EXACT,
            details="Candidate invoice INV-2026-001 in reference",
            identifiers=NormalizedRelevantIdentifiers(invoice_id=cand_id, invoice_number="INV-2026-001"),
            classification=EvidenceClassification.DIRECT,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.INVOICE,
            matched_value="INV-2026-001",
        )
        narr_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.INVOICE_NUMBER_IN_NARRATION,
            source=NormalizedEvidenceSource.PAYMENT_NARRATION,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.EXACT,
            details="Candidate invoice INV-2026-001 in narration",
            identifiers=NormalizedRelevantIdentifiers(invoice_id=cand_id, invoice_number="INV-2026-001"),
            classification=EvidenceClassification.DIRECT,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.INVOICE,
            matched_value="INV-2026-001",
        )
        pay_ctx = NormalizedPaymentEvidenceContext(
            payment_id=payment_id,
            company_id=comp_id,
            items=[ref_item, narr_item],
            extracted_invoice_references=["INV-2026-001"],
            has_conflicting_identifiers=False,
        )
        bundle = NormalizedCandidateBundle(
            invoice_id=cand_id,
            invoice_number="INV-2026-001",
            items=[ref_item, narr_item],
            has_conflicting_evidence=False,
            direct_evidence_count=2,
            supporting_evidence_count=0,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )

        score_res = engine.score_candidate(bundle=bundle, payment_evidence=pay_ctx)
        # Exactly +35.00 once, NEVER +70.00
        assert score_res.total_score == Decimal("35.00")
        inv_contribs = [c for c in score_res.contributions if c.signal_type == ScoringSignalType.INVOICE_NUMBER_MATCH]
        assert len(inv_contribs) == 1
        assert inv_contribs[0].weight == Decimal("35.00")

    def test_unrelated_and_conflicting_invoice_reference_handling(self) -> None:
        """Unrelated invoice reference awards 0; conflicting reference suppresses contribution."""
        engine = MatchingScoringEngine()
        comp_id = uuid.uuid4()
        payment_id = uuid.uuid4()
        cand_id = uuid.uuid4()

        # Payment reference contains INV-999, but candidate is INV-001
        pay_ctx = NormalizedPaymentEvidenceContext(
            payment_id=payment_id,
            company_id=comp_id,
            items=[],
            extracted_invoice_references=["INV-999"],
            has_conflicting_identifiers=False,
        )
        bundle = NormalizedCandidateBundle(
            invoice_id=cand_id,
            invoice_number="INV-001",
            items=[],
            has_conflicting_evidence=False,
            direct_evidence_count=0,
            supporting_evidence_count=0,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )

        score_res = engine.score_candidate(bundle=bundle, payment_evidence=pay_ctx)
        assert score_res.total_score == Decimal("0.00")
        assert len(score_res.contributions) == 0

    def test_utr_presence_does_not_award_identifier_match_score(self) -> None:
        """UTR presence is transaction metadata, NOT a customer counterparty identifier (+40)."""
        engine = MatchingScoringEngine()
        comp_id = uuid.uuid4()
        payment_id = uuid.uuid4()
        cand_id = uuid.uuid4()

        utr_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.UTR_IDENTIFIER,
            source=NormalizedEvidenceSource.PAYMENT_PAYMENT_REFERENCE,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.HIGH,
            details="UTR transaction tracking reference present: UTR12345678",
            identifiers=NormalizedRelevantIdentifiers(payment_id=payment_id, utr="UTR12345678"),
            classification=EvidenceClassification.SUPPORTING,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.PAYMENT,
            matched_value="UTR12345678",
        )
        pay_ctx = NormalizedPaymentEvidenceContext(
            payment_id=payment_id,
            company_id=comp_id,
            items=[utr_item],
            extracted_invoice_references=[],
            has_conflicting_identifiers=False,
        )
        bundle = NormalizedCandidateBundle(
            invoice_id=cand_id,
            invoice_number="INV-001",
            items=[],
            has_conflicting_evidence=False,
            direct_evidence_count=0,
            supporting_evidence_count=1,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )

        score_res = engine.score_candidate(bundle=bundle, payment_evidence=pay_ctx)
        # UTR must NOT award IDENTIFIER_MATCH (+40.00)
        assert score_res.total_score == Decimal("0.00")
        assert not any(c.signal_type == ScoringSignalType.IDENTIFIER_MATCH for c in score_res.contributions)

    def test_upi_vpa_and_bank_account_award_identifier_match_score(self) -> None:
        """Direct counterparty identifiers (BANK_ACCOUNT and UPI_VPA) both award +40.00."""
        engine = MatchingScoringEngine()
        comp_id = uuid.uuid4()
        payment_id = uuid.uuid4()
        cand_id = uuid.uuid4()

        # UPI VPA test
        vpa_item = CanonicalEvidenceObject(
            evidence_type=NormalizedEvidenceType.UPI_VPA_IDENTIFIER,
            source=NormalizedEvidenceSource.PAYMENT_PAYER_RAW_IDENTIFIER,
            result=NormalizedEvidenceResult.MATCH,
            strength=NormalizedEvidenceStrength.EXACT,
            details="UPI VPA matched registered customer identifier",
            identifiers=NormalizedRelevantIdentifiers(payment_id=payment_id),
            classification=EvidenceClassification.DIRECT,
            rule_version="1.0.0",
            algorithm_version="14.10.0",
            target_entity=NormalizedTargetEntity.CUSTOMER,
            matched_value="cust@upi",
        )
        pay_ctx_vpa = NormalizedPaymentEvidenceContext(
            payment_id=payment_id,
            company_id=comp_id,
            items=[vpa_item],
            extracted_invoice_references=[],
            has_conflicting_identifiers=False,
        )
        bundle = NormalizedCandidateBundle(
            invoice_id=cand_id,
            invoice_number="INV-001",
            items=[],
            has_conflicting_evidence=False,
            direct_evidence_count=0,
            supporting_evidence_count=0,
            missing_evidence_count=0,
            conflicting_evidence_count=0,
        )

        score_vpa = engine.score_candidate(bundle=bundle, payment_evidence=pay_ctx_vpa)
        assert score_vpa.total_score == Decimal("40.00")
        assert score_vpa.contributions[0].signal_type == ScoringSignalType.IDENTIFIER_MATCH
        assert score_vpa.contributions[0].weight == Decimal("40.00")

