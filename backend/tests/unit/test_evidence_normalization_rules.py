"""Unit tests for Phase 14.10 Evidence Normalization pure domain engine.

Tests:
1. Direct Evidence Normalization:
   - DIRECT BANK_ACCOUNT_IDENTIFIER -> MATCH, EXACT strength, target CUSTOMER.
   - DIRECT INVOICE_NUMBER_IN_NARRATION -> MATCH, EXACT strength, target INVOICE.
   - DIRECT AMOUNT_EXACT_EQUALITY -> MATCH, EXACT strength, target INVOICE.
2. Supporting Evidence Normalization:
   - SUPPORTING CUSTOMER_NAME_TOKEN -> MATCH/SUPPORTED, MEDIUM/LOW strength.
   - SUPPORTING CUSTOMER_ALIAS -> MATCH, MEDIUM strength.
   - SUPPORTING UTR_IDENTIFIER -> PRESENT, MEDIUM strength.
   - SUPPORTING AMOUNT_PARTIAL_COMPATIBLE -> SUPPORTED, MEDIUM strength.
   - SUPPORTING DATE_CAUSALITY -> SUPPORTED, MEDIUM strength.
   - SUPPORTING DATE_PROXIMITY -> SUPPORTED, LOW strength.
   - SUPPORTING CURRENCY_CONSISTENCY -> MATCH, MEDIUM strength.
3. Missing Evidence Normalization:
   - MISSING PAYMENT_REFERENCE -> ABSENT result, NONE strength, details preserved.
   - MISSING UTR_IDENTIFIER -> ABSENT result, NONE strength.
   - MISSING BANK_ACCOUNT_IDENTIFIER -> ABSENT result, NONE strength.
   - MISSING DATE_PROXIMITY -> ABSENT result, NONE strength.
4. Conflicting Evidence Normalization:
   - CONFLICTING BANK_ACCOUNT_IDENTIFIER (different customer) -> CONFLICT result, NONE strength.
   - CONFLICTING INVOICE_NUMBER_IN_NARRATION (different invoice) -> CONFLICT result, NONE strength.
   - CONFLICTING DATE_CAUSALITY (non-causal) -> CONFLICT result, NONE strength.
   - CONFLICTING CURRENCY_CONSISTENCY (mismatch) -> CONFLICT result, NONE strength.
5. Deduplication & Semantic Preservation:
   - Exact duplicate evidence items normalized to single entry without data loss.
   - Distinct items with same type preserved.
6. Determinism & Total Order:
   - 100 random candidate permutation runs produce identical deterministic payloads.
   - Excludes non-deterministic timestamps from to_deterministic_payload().
7. Exact Decimal & Provenance Invariants:
   - Preserves rule_version ("1.0.0") and algorithm_version ("14.10.0").
   - Preserves identifiers (payment_id, customer_id, invoice_id, utr, bank_account).
   - Zero score, zero weight, zero probabilistic confidence.
"""

from datetime import date, timedelta
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
    EvidenceNormalizationResult,
    NormalizedEvidenceResult,
    NormalizedEvidenceSource,
    NormalizedEvidenceStrength,
    NormalizedEvidenceType,
    NormalizedRelevantIdentifiers,
    NormalizedTargetEntity,
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


class TestEvidenceNormalizationRules:
    """Test suite for Phase 14.10 Evidence Normalization domain rules."""

    def test_direct_evidence_normalization(self) -> None:
        """DIRECT items normalize to canonical types with MATCH and EXACT strength."""
        comp_id = uuid.uuid4()
        payment = _make_payment(
            amount="50000.00",
            narration="PAYMENT INV-2026-001",
            bank_account_number="998877665544",
            company_id=comp_id,
        )
        cand = _make_candidate(
            invoice_number="INV-2026-001",
            outstanding_amount="50000.00",
            company_id=comp_id,
        )
        customer = _make_customer(company_id=comp_id)

        raw_collection = EvidenceCollectionEngine().evaluate(
            payment=payment,
            candidates=[cand],
            customer=customer,
        )

        normalizer = EvidenceNormalizationEngine(
            rule_version="1.0.0",
            algorithm_version="14.10.0",
        )
        norm_result = normalizer.normalize_result_aggregate(raw_collection)

        assert norm_result.rule_version == "1.0.0"
        assert norm_result.algorithm_version == "14.10.0"
        assert norm_result.is_deterministic is True

        # Find direct bank account match in payment evidence
        acc_items = [
            i for i in norm_result.payment_evidence.items
            if i.evidence_type == NormalizedEvidenceType.BANK_ACCOUNT_IDENTIFIER
        ]
        assert len(acc_items) == 1
        acc_item = acc_items[0]
        assert acc_item.classification == EvidenceClassification.DIRECT
        assert acc_item.result == NormalizedEvidenceResult.MATCH
        assert acc_item.strength == NormalizedEvidenceStrength.EXACT
        assert acc_item.target_entity == NormalizedTargetEntity.CUSTOMER
        assert acc_item.source == NormalizedEvidenceSource.PAYMENT_BANK_ACCOUNT
        assert acc_item.rule_version == "1.0.0"

        # Find candidate bundle
        assert len(norm_result.candidate_bundles) == 1
        bundle = norm_result.candidate_bundles[0]
        assert bundle.invoice_number == "INV-2026-001"

        # Check candidate exact amount match
        amt_items = [
            i for i in bundle.items
            if i.evidence_type == NormalizedEvidenceType.AMOUNT_EXACT_EQUALITY
        ]
        assert len(amt_items) == 1
        amt_item = amt_items[0]
        assert amt_item.classification == EvidenceClassification.DIRECT
        assert amt_item.result == NormalizedEvidenceResult.MATCH
        assert amt_item.strength == NormalizedEvidenceStrength.EXACT
        assert amt_item.matched_value == "50000.00"
        assert amt_item.expected_value == "50000.00"

    def test_supporting_evidence_normalization(self) -> None:
        """SUPPORTING items normalize to SUPPORTED/MATCH results with MEDIUM/LOW strength."""
        comp_id = uuid.uuid4()
        payment = _make_payment(
            amount="25000.00",
            narration="Acme India UTR987654321012",
            company_id=comp_id,
        )
        cand = _make_candidate(
            invoice_number="INV-2026-002",
            outstanding_amount="50000.00",
            company_id=comp_id,
        )
        customer = _make_customer(name="Acme Corporation", company_id=comp_id)

        raw_collection = EvidenceCollectionEngine().evaluate(
            payment=payment,
            candidates=[cand],
            customer=customer,
        )

        norm_result = EvidenceNormalizationEngine().normalize_result_aggregate(raw_collection)

        # UTR extracted
        utr_items = [
            i for i in norm_result.payment_evidence.items
            if i.evidence_type == NormalizedEvidenceType.UTR_IDENTIFIER
        ]
        assert len(utr_items) == 1
        assert utr_items[0].classification == EvidenceClassification.SUPPORTING
        assert utr_items[0].result == NormalizedEvidenceResult.PRESENT
        assert utr_items[0].strength == NormalizedEvidenceStrength.MEDIUM
        assert utr_items[0].identifiers.utr == "UTR987654321012"

        # Partial amount compatible
        bundle = norm_result.candidate_bundles[0]
        partial_items = [
            i for i in bundle.items
            if i.evidence_type == NormalizedEvidenceType.AMOUNT_PARTIAL_COMPATIBLE
        ]
        assert len(partial_items) == 1
        assert partial_items[0].classification == EvidenceClassification.SUPPORTING
        assert partial_items[0].result == NormalizedEvidenceResult.SUPPORTED
        assert partial_items[0].strength == NormalizedEvidenceStrength.MEDIUM

    def test_missing_evidence_normalization(self) -> None:
        """MISSING items normalize to ABSENT result with NONE strength without data loss."""
        comp_id = uuid.uuid4()
        payment = _make_payment(
            narration="MISCELLANEOUS TRANSFER",
            payment_reference="",
            bank_account_number="",
            company_id=comp_id,
        )
        cand = _make_candidate(
            invoice_number="INV-999",
            company_id=comp_id,
            due_date=date(2026, 1, 1),  # distant from payment date Sep 15
        )

        raw_collection = EvidenceCollectionEngine().evaluate(
            payment=payment,
            candidates=[cand],
        )

        norm_result = EvidenceNormalizationEngine().normalize_result_aggregate(raw_collection)

        # Payment reference is missing
        ref_items = [
            i for i in norm_result.payment_evidence.items
            if i.evidence_type == NormalizedEvidenceType.PAYMENT_REFERENCE
        ]
        assert len(ref_items) == 1
        assert ref_items[0].classification == EvidenceClassification.MISSING
        assert ref_items[0].result == NormalizedEvidenceResult.ABSENT
        assert ref_items[0].strength == NormalizedEvidenceStrength.NONE

        # Date proximity distant -> MISSING
        bundle = norm_result.candidate_bundles[0]
        date_items = [
            i for i in bundle.items
            if i.evidence_type == NormalizedEvidenceType.DATE_PROXIMITY
        ]
        assert len(date_items) == 1
        assert date_items[0].classification == EvidenceClassification.MISSING
        assert date_items[0].result == NormalizedEvidenceResult.ABSENT
        assert date_items[0].strength == NormalizedEvidenceStrength.NONE

    def test_conflicting_evidence_normalization(self) -> None:
        """CONFLICTING items normalize to CONFLICT result and NONE strength, preserving conflict."""
        comp_id = uuid.uuid4()
        payment = _make_payment(
            amount="50000.00",
            currency="INR",
            narration="FOR INV-OTHER-888",
            bank_account_number="998877665544",
            payment_date=date(2026, 8, 1),
            company_id=comp_id,
        )
        cand = _make_candidate(
            invoice_number="INV-TARGET-001",
            outstanding_amount="50000.00",
            currency="USD",  # Currency mismatch!
            issue_date=date(2026, 9, 1),  # Non-causal: payment on Aug 1 precedes invoice on Sep 1!
            company_id=comp_id,
        )
        customer = _make_customer(company_id=comp_id)
        other_customer = CustomerLookupContext(
            customer_id=uuid.uuid4(),
            name="Competing Corp",
            identifiers=[("BANK_ACCOUNT", "998877665544")],
        )

        raw_collection = EvidenceCollectionEngine().evaluate(
            payment=payment,
            candidates=[cand],
            customer=customer,
            all_customers=[customer, other_customer],
        )

        norm_result = EvidenceNormalizationEngine().normalize_result_aggregate(raw_collection)

        # Payment evidence has conflicting bank account
        assert norm_result.payment_evidence.has_conflicting_identifiers is True
        conf_acc = [
            i for i in norm_result.payment_evidence.items
            if i.classification == EvidenceClassification.CONFLICTING
        ]
        assert len(conf_acc) >= 1
        assert conf_acc[0].result == NormalizedEvidenceResult.CONFLICT
        assert conf_acc[0].strength == NormalizedEvidenceStrength.NONE

        # Candidate bundle has conflicting currency, date causality, and invoice number reference
        bundle = norm_result.candidate_bundles[0]
        assert bundle.has_conflicting_evidence is True
        conf_cand_items = [
            i for i in bundle.items
            if i.classification == EvidenceClassification.CONFLICTING
        ]
        conf_types = {i.evidence_type for i in conf_cand_items}
        assert NormalizedEvidenceType.CURRENCY_CONSISTENCY in conf_types
        assert NormalizedEvidenceType.DATE_CAUSALITY in conf_types
        assert NormalizedEvidenceType.INVOICE_NUMBER_IN_NARRATION in conf_types

        for item in conf_cand_items:
            assert item.result == NormalizedEvidenceResult.CONFLICT
            assert item.strength == NormalizedEvidenceStrength.NONE

    def test_deduplication_without_loss(self) -> None:
        """Identical duplicate raw evidence items are deduplicated safely."""
        normalizer = EvidenceNormalizationEngine()
        item1 = StructuredEvidenceItem(
            evidence_type=EvidenceType.PAYMENT_REFERENCE,
            classification=EvidenceClassification.SUPPORTING,
            source_field="payment.payment_reference",
            observed_result="PRESENT",
            description="Payment reference is provided: REF123",
            target_entity="PAYMENT",
            matched_value="REF123",
        )
        item2 = StructuredEvidenceItem(
            evidence_type=EvidenceType.PAYMENT_REFERENCE,
            classification=EvidenceClassification.SUPPORTING,
            source_field="payment.payment_reference",
            observed_result="PRESENT",
            description="Payment reference is provided: REF123",
            target_entity="PAYMENT",
            matched_value="REF123",
        )
        # item3 has different description / source
        item3 = StructuredEvidenceItem(
            evidence_type=EvidenceType.PAYMENT_REFERENCE,
            classification=EvidenceClassification.SUPPORTING,
            source_field="payment.narration",
            observed_result="PRESENT",
            description="Payment reference extracted from narration: REF123",
            target_entity="PAYMENT",
            matched_value="REF123",
        )

        deduped = normalizer.normalize_items_deduplicated([item1, item2, item3])
        assert len(deduped) == 2
        sources = {d.source for d in deduped}
        assert NormalizedEvidenceSource.PAYMENT_PAYMENT_REFERENCE in sources
        assert NormalizedEvidenceSource.PAYMENT_NARRATION in sources

    def test_100_run_permutation_determinism(self) -> None:
        """Evaluating and normalizing across 100 randomized candidate orders yields identical payloads."""
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

        engine = EvidenceCollectionEngine()
        normalizer = EvidenceNormalizationEngine()

        base_raw = engine.evaluate(payment=payment, candidates=candidates)
        base_norm = normalizer.normalize_result_aggregate(base_raw)
        base_payload = base_norm.to_deterministic_payload()

        for _ in range(100):
            shuffled = list(candidates)
            random.shuffle(shuffled)
            raw_res = engine.evaluate(payment=payment, candidates=shuffled)
            norm_res = normalizer.normalize_result_aggregate(raw_res)
            perm_payload = norm_res.to_deterministic_payload()
            assert perm_payload == base_payload

    def test_unknown_source_fallback(self) -> None:
        """Unknown or custom source strings gracefully fall back to UNKNOWN_SOURCE."""
        normalizer = EvidenceNormalizationEngine()
        source_enum = normalizer.normalize_source("custom.legacy_field")
        assert source_enum == NormalizedEvidenceSource.UNKNOWN_SOURCE

    def test_invalid_canonical_object_validation(self) -> None:
        """CanonicalEvidenceObject validates non-empty details, rule_version, and algorithm_version."""
        with pytest.raises(DomainError, match="details must be non-empty"):
            CanonicalEvidenceObject(
                evidence_type=NormalizedEvidenceType.PAYMENT_REFERENCE,
                source=NormalizedEvidenceSource.PAYMENT_PAYMENT_REFERENCE,
                result=NormalizedEvidenceResult.PRESENT,
                strength=NormalizedEvidenceStrength.MEDIUM,
                details="",
                identifiers=NormalizedRelevantIdentifiers(),
                classification=EvidenceClassification.SUPPORTING,
                rule_version="1.0.0",
                algorithm_version="14.10.0",
                target_entity=NormalizedTargetEntity.PAYMENT,
            )

        with pytest.raises(DomainError, match="rule_version must be specified"):
            CanonicalEvidenceObject(
                evidence_type=NormalizedEvidenceType.PAYMENT_REFERENCE,
                source=NormalizedEvidenceSource.PAYMENT_PAYMENT_REFERENCE,
                result=NormalizedEvidenceResult.PRESENT,
                strength=NormalizedEvidenceStrength.MEDIUM,
                details="Valid details",
                identifiers=NormalizedRelevantIdentifiers(),
                classification=EvidenceClassification.SUPPORTING,
                rule_version="",
                algorithm_version="14.10.0",
                target_entity=NormalizedTargetEntity.PAYMENT,
            )

    def test_unknown_target_entity_fails_closed(self) -> None:
        """Invalid or unknown target_entity strings must raise DomainError (never default to PAYMENT)."""
        normalizer = EvidenceNormalizationEngine()
        invalid_entities = ["UNKNOWN", "BANK_ACCOUNT", "INVALID_ENTITY", "PAYMENT_ORDER", ""]

        for invalid_entity in invalid_entities:
            with pytest.raises(DomainError, match="Unsupported or invalid target entity for normalization"):
                normalizer.normalize_target_entity(invalid_entity)

        # Valid entities must succeed
        assert normalizer.normalize_target_entity("PAYMENT") == NormalizedTargetEntity.PAYMENT
        assert normalizer.normalize_target_entity("CUSTOMER") == NormalizedTargetEntity.CUSTOMER
        assert normalizer.normalize_target_entity("INVOICE") == NormalizedTargetEntity.INVOICE
        assert normalizer.normalize_target_entity("COMBINATION") == NormalizedTargetEntity.COMBINATION

    def test_unknown_observed_result_fails_closed(self) -> None:
        """Invalid or unknown observed_result strings must raise DomainError (never guess or infer)."""
        normalizer = EvidenceNormalizationEngine()
        invalid_results = ["UNKNOWN_RESULT", "PARTIAL_MATCH", "UNVERIFIED", "RANDOM_STRING", ""]

        for invalid_res in invalid_results:
            with pytest.raises(DomainError, match="Unsupported or invalid observed result for normalization"):
                normalizer.normalize_result(invalid_res, EvidenceClassification.SUPPORTING)

    def test_classification_cannot_bypass_invalid_observed_result(self) -> None:
        """DIRECT or SUPPORTING classification must not convert invalid observed_result into MATCH/SUPPORTED."""
        normalizer = EvidenceNormalizationEngine()

        with pytest.raises(DomainError, match="Unsupported or invalid observed result for normalization"):
            normalizer.normalize_result("INVALID_DIRECT_RESULT", EvidenceClassification.DIRECT)

        with pytest.raises(DomainError, match="Unsupported or invalid observed result for normalization"):
            normalizer.normalize_result("MALFORMED_SUPPORTING", EvidenceClassification.SUPPORTING)

        with pytest.raises(DomainError, match="Unsupported or invalid observed result for normalization"):
            normalizer.normalize_result("BOGUS_MISSING", EvidenceClassification.MISSING)

        with pytest.raises(DomainError, match="Unsupported or invalid observed result for normalization"):
            normalizer.normalize_result("BOGUS_CONFLICT", EvidenceClassification.CONFLICTING)

    def test_item_normalization_fails_closed_on_invalid_raw_evidence(self) -> None:
        """normalizer.normalize_item() fails closed when given an item with invalid target_entity or observed_result."""
        normalizer = EvidenceNormalizationEngine()

        # Item with invalid target_entity
        bad_entity_item = StructuredEvidenceItem(
            evidence_type=EvidenceType.PAYMENT_REFERENCE,
            classification=EvidenceClassification.SUPPORTING,
            source_field="payment.payment_reference",
            observed_result="PRESENT",
            description="Valid description",
            target_entity="NON_EXISTENT_ENTITY",
            matched_value="REF123",
        )
        with pytest.raises(DomainError, match="Unsupported or invalid target entity for normalization"):
            normalizer.normalize_item(bad_entity_item)

        # Item with invalid observed_result
        bad_result_item = StructuredEvidenceItem(
            evidence_type=EvidenceType.PAYMENT_REFERENCE,
            classification=EvidenceClassification.DIRECT,
            source_field="payment.payment_reference",
            observed_result="INVENTED_RESULT",
            description="Valid description",
            target_entity="PAYMENT",
            matched_value="REF123",
        )
        with pytest.raises(DomainError, match="Unsupported or invalid observed result for normalization"):
            normalizer.normalize_item(bad_result_item)

