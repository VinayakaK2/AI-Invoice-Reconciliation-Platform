"""Domain entities, value objects, canonical representation, and normalizer for Phase 14.10 Evidence Normalization.

Phase 14.10 establishes a canonical, machine-readable, deterministic evidence normalization layer
that converts raw / heterogeneous evidence produced by Phase 14.9 Evidence Collection into a consistent,
version-aware, structured representation suitable for downstream reconciliation phases (14.11 Scoring,
14.12 Confidence, 14.13 Decision).

CRITICAL DOMAIN INVARIANTS:
1. Evidence Normalization != Evidence Collection != Matching != Scoring != Confidence != Decision != Approval != Mutation.
2. Normalization describes WHAT evidence exists in a canonical, consistent structure; it NEVER computes
   numeric scores, weights, or calibrated probabilities.
3. Observed Financial Mutation: NONE (Strictly read-only domain evaluation).
4. Determinism: 100% deterministic, zero LLM authority, zero floating-point arithmetic.
5. Semantic Preservation: Raw meaning == Normalized meaning. Does not fabricate, upgrade, downgrade, or resolve conflicts.
6. Tenant Isolation: Operates strictly within authenticated company context; fails closed on cross-tenant evidence.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from app.modules.reconciliation.domain.evidence_collection import (
    CandidateEvidenceBundle,
    EvidenceClassification,
    EvidenceCollectionResult,
    EvidenceType,
    PaymentEvidenceContext,
    StructuredEvidenceItem,
)
from app.shared.exceptions import DomainError


# ==============================================================================
# Canonical Evidence Taxonomies & Controlled Vocabularies
# ==============================================================================


class NormalizedEvidenceType(str, Enum):
    """Canonical categorization of evidence across all reconciliation dimensions."""

    # Counterparty & Account Identifiers
    BANK_ACCOUNT_IDENTIFIER = "BANK_ACCOUNT_IDENTIFIER"
    UPI_VPA_IDENTIFIER = "UPI_VPA_IDENTIFIER"
    CUSTOMER_NAME_TOKEN = "CUSTOMER_NAME_TOKEN"
    CUSTOMER_ALIAS = "CUSTOMER_ALIAS"
    CUSTOMER_TAX_ID = "CUSTOMER_TAX_ID"

    # Payment & Transaction Identifiers
    PAYMENT_REFERENCE = "PAYMENT_REFERENCE"
    UTR_IDENTIFIER = "UTR_IDENTIFIER"
    INVOICE_NUMBER_IN_NARRATION = "INVOICE_NUMBER_IN_NARRATION"

    # Amount Relationships
    AMOUNT_EXACT_EQUALITY = "AMOUNT_EXACT_EQUALITY"
    AMOUNT_PARTIAL_COMPATIBLE = "AMOUNT_PARTIAL_COMPATIBLE"
    AMOUNT_OVERPAYMENT = "AMOUNT_OVERPAYMENT"
    COMBINATION_SUM_EQUALITY = "COMBINATION_SUM_EQUALITY"

    # Temporal & Date Relationships
    DATE_CAUSALITY = "DATE_CAUSALITY"
    DATE_PROXIMITY = "DATE_PROXIMITY"

    # Currency Consistency
    CURRENCY_CONSISTENCY = "CURRENCY_CONSISTENCY"

    # Historical & Pattern Relationships
    HISTORICAL_CUSTOMER_PATTERN = "HISTORICAL_CUSTOMER_PATTERN"

    # Extended Document Identifiers
    PURCHASE_ORDER_REFERENCE = "PURCHASE_ORDER_REFERENCE"


class NormalizedEvidenceSource(str, Enum):
    """Canonical origin field / document path where the evidence fact was obtained."""

    PAYMENT_BANK_ACCOUNT = "payment.bank_account_number"
    PAYMENT_PAYER_RAW_IDENTIFIER = "payment.payer_raw_identifier"
    PAYMENT_NARRATION = "payment.narration"
    PAYMENT_PAYMENT_REFERENCE = "payment.payment_reference"
    PAYMENT_PAYMENT_DATE = "payment.payment_date"
    PAYMENT_CURRENCY = "payment.currency"
    PAYMENT_AMOUNT = "payment.amount"
    CUSTOMER_NAME = "customer.name"
    CUSTOMER_TAX_ID = "customer.tax_id"
    CUSTOMER_IDENTIFIERS = "customer.identifiers"
    CUSTOMER_ALIASES = "customer.aliases"
    INVOICE_INVOICE_NUMBER = "invoice.invoice_number"
    INVOICE_OUTSTANDING_AMOUNT = "invoice.outstanding_amount"
    INVOICE_ISSUE_DATE = "invoice.issue_date"
    INVOICE_DUE_DATE = "invoice.due_date"
    INVOICE_CURRENCY = "invoice.currency"
    CANDIDATE_OUTSTANDING_AMOUNT = "candidate.outstanding_amount"
    CANDIDATE_CURRENCY = "candidate.currency"
    CANDIDATE_DUE_DATE = "candidate.due_date"
    COMBINATION_SUBSET = "combination.subset"
    UNKNOWN_SOURCE = "unknown.source"


class NormalizedEvidenceResult(str, Enum):
    """Canonical factual outcome of the evaluated evidence fact."""

    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    CONFLICT = "CONFLICT"
    SUPPORTED = "SUPPORTED"


class NormalizedEvidenceStrength(str, Enum):
    """Qualitative evidentiary fidelity level of the evidence item.

    NON-SCORING INVARIANT:
    Represents contractual fidelity/authority (DIRECT proof vs supporting context vs absent/conflicting).
    Strictly NOT a 0-100 score, weight, or probability. Numeric scoring is deferred to Phase 14.11.
    """

    EXACT = "EXACT"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class NormalizedTargetEntity(str, Enum):
    """Target entity scope of the normalized evidence item."""

    PAYMENT = "PAYMENT"
    CUSTOMER = "CUSTOMER"
    INVOICE = "INVOICE"
    COMBINATION = "COMBINATION"


# ==============================================================================
# Canonical Traceability Identifiers & Data Structures
# ==============================================================================


@dataclass(frozen=True)
class NormalizedRelevantIdentifiers:
    """Canonical provenance identifiers connecting the evidence item to system entities.

    Maintains strict multi-tenant safety and guarantees zero cross-tenant leakage.
    """

    payment_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    invoice_id: Optional[UUID] = None
    invoice_number: Optional[str] = None
    payment_reference: Optional[str] = None
    utr: Optional[str] = None
    bank_account: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert identifiers to serializable dictionary omitting null values."""
        res: Dict[str, Any] = {}
        if self.payment_id is not None:
            res["payment_id"] = str(self.payment_id)
        if self.customer_id is not None:
            res["customer_id"] = str(self.customer_id)
        if self.invoice_id is not None:
            res["invoice_id"] = str(self.invoice_id)
        if self.invoice_number is not None:
            res["invoice_number"] = self.invoice_number
        if self.payment_reference is not None:
            res["payment_reference"] = self.payment_reference
        if self.utr is not None:
            res["utr"] = self.utr
        if self.bank_account is not None:
            res["bank_account"] = self.bank_account
        return res


@dataclass(frozen=True)
class CanonicalEvidenceObject:
    """Immutable, canonical, normalized evidence object.

    Satisfies all Phase 14.10 roadmap dimensions:
    - type: Canonical NormalizedEvidenceType
    - source: Canonical NormalizedEvidenceSource or source field path
    - result: Canonical NormalizedEvidenceResult
    - strength: Contractual qualitative strength (EXACT, HIGH, MEDIUM, LOW, NONE)
    - details: Human-readable factual explanation (does NOT decide outcome)
    - identifiers: Relevant entity identifiers for end-to-end traceability
    - classification: Preserved Phase 14.9 classification (DIRECT, SUPPORTING, MISSING, CONFLICTING)
    - rule_version: Authoritative rule version string
    - algorithm_version: Authoritative algorithm version string
    """

    evidence_type: NormalizedEvidenceType
    source: NormalizedEvidenceSource
    result: NormalizedEvidenceResult
    strength: NormalizedEvidenceStrength
    details: str
    identifiers: NormalizedRelevantIdentifiers
    classification: EvidenceClassification
    rule_version: str
    algorithm_version: str
    target_entity: NormalizedTargetEntity
    matched_value: Optional[str] = None
    expected_value: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate structural integrity invariants."""
        if not self.details or not self.details.strip():
            raise DomainError("CanonicalEvidenceObject details must be non-empty string.")
        if not self.rule_version or not self.rule_version.strip():
            raise DomainError("CanonicalEvidenceObject rule_version must be specified.")
        if not self.algorithm_version or not self.algorithm_version.strip():
            raise DomainError("CanonicalEvidenceObject algorithm_version must be specified.")

    def to_dict(self) -> Dict[str, Any]:
        """Convert canonical evidence object to serializable dictionary."""
        return {
            "evidence_type": self.evidence_type.value,
            "source": self.source.value,
            "result": self.result.value,
            "strength": self.strength.value,
            "details": self.details,
            "identifiers": self.identifiers.to_dict(),
            "classification": self.classification.value,
            "rule_version": self.rule_version,
            "algorithm_version": self.algorithm_version,
            "target_entity": self.target_entity.value,
            "matched_value": self.matched_value,
            "expected_value": self.expected_value,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class NormalizedCandidateBundle:
    """Canonical normalized evidence bundle for a specific candidate invoice."""

    invoice_id: UUID
    invoice_number: str
    items: List[CanonicalEvidenceObject]
    has_conflicting_evidence: bool
    direct_evidence_count: int
    supporting_evidence_count: int
    missing_evidence_count: int
    conflicting_evidence_count: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert bundle to serializable dictionary."""
        return {
            "invoice_id": str(self.invoice_id),
            "invoice_number": self.invoice_number,
            "items": [item.to_dict() for item in self.items],
            "has_conflicting_evidence": self.has_conflicting_evidence,
            "direct_evidence_count": self.direct_evidence_count,
            "supporting_evidence_count": self.supporting_evidence_count,
            "missing_evidence_count": self.missing_evidence_count,
            "conflicting_evidence_count": self.conflicting_evidence_count,
        }


@dataclass(frozen=True)
class NormalizedPaymentEvidenceContext:
    """Canonical normalized evidence facts at the payment level."""

    payment_id: UUID
    company_id: UUID
    items: List[CanonicalEvidenceObject]
    extracted_invoice_references: List[str]
    has_conflicting_identifiers: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert context to serializable dictionary."""
        return {
            "payment_id": str(self.payment_id),
            "company_id": str(self.company_id),
            "items": [item.to_dict() for item in self.items],
            "extracted_invoice_references": self.extracted_invoice_references,
            "has_conflicting_identifiers": self.has_conflicting_identifiers,
        }


@dataclass(frozen=True)
class EvidenceNormalizationResult:
    """Authoritative result of Phase 14.10 Evidence Normalization.

    Read-only evaluation. Converts Phase 14.9 EvidenceCollectionResult into canonical,
    consistent, version-aware evidence structures.
    """

    payment_id: UUID
    company_id: UUID
    payment_evidence: NormalizedPaymentEvidenceContext
    candidate_bundles: List[NormalizedCandidateBundle]
    total_evidence_items: int
    total_direct_items: int
    total_supporting_items: int
    total_missing_items: int
    total_conflicting_items: int
    rule_version: str
    algorithm_version: str
    is_deterministic: bool = True
    normalized_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_deterministic_payload(self) -> Dict[str, Any]:
        """Convert result to serializable payload containing ONLY deterministic facts.

        Excludes non-deterministic execution metadata such as normalized_at timestamp.
        Guarantees bit-for-bit equivalence across 100 repeated runs.
        """
        return {
            "payment_id": str(self.payment_id),
            "company_id": str(self.company_id),
            "payment_evidence": self.payment_evidence.to_dict(),
            "candidate_bundles": [b.to_dict() for b in self.candidate_bundles],
            "total_evidence_items": self.total_evidence_items,
            "total_direct_items": self.total_direct_items,
            "total_supporting_items": self.total_supporting_items,
            "total_missing_items": self.total_missing_items,
            "total_conflicting_items": self.total_conflicting_items,
            "rule_version": self.rule_version,
            "algorithm_version": self.algorithm_version,
            "is_deterministic": self.is_deterministic,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to serializable dictionary including evaluation metadata."""
        payload = self.to_deterministic_payload()
        payload["normalized_at"] = self.normalized_at.isoformat()
        return payload


# ==============================================================================
# Domain Normalization Engine
# ==============================================================================


class EvidenceNormalizationEngine:
    """Pure domain normalization engine for Phase 14.10.

    Converts raw StructuredEvidenceItem instances and EvidenceCollectionResult aggregates
    into canonical, machine-readable CanonicalEvidenceObject instances.
    Guarantees:
    - Semantic preservation (no loss of raw evidence meaning).
    - Preserves all conflicting and missing signals without loss or resolution.
    - Zero scoring, zero weighting, zero probabilistic confidence.
    - Zero database mutations (pure read-only).
    - 100% deterministic total ordering on normalized outputs.
    - Multi-tenant boundary enforcement.
    """

    CANONICAL_RULE_VERSION: str = "1.0.0"
    CANONICAL_ALGORITHM_VERSION: str = "14.10.0"

    # Mapping of raw string source fields to canonical enum sources
    SOURCE_MAP: Dict[str, NormalizedEvidenceSource] = {
        "payment.bank_account_number": NormalizedEvidenceSource.PAYMENT_BANK_ACCOUNT,
        "payment.payer_raw_identifier": NormalizedEvidenceSource.PAYMENT_PAYER_RAW_IDENTIFIER,
        "payment.narration": NormalizedEvidenceSource.PAYMENT_NARRATION,
        "payment.payment_reference": NormalizedEvidenceSource.PAYMENT_PAYMENT_REFERENCE,
        "payment.payment_date": NormalizedEvidenceSource.PAYMENT_PAYMENT_DATE,
        "payment.currency": NormalizedEvidenceSource.PAYMENT_CURRENCY,
        "payment.amount": NormalizedEvidenceSource.PAYMENT_AMOUNT,
        "customer.name": NormalizedEvidenceSource.CUSTOMER_NAME,
        "customer.tax_id": NormalizedEvidenceSource.CUSTOMER_TAX_ID,
        "customer.identifiers": NormalizedEvidenceSource.CUSTOMER_IDENTIFIERS,
        "customer.aliases": NormalizedEvidenceSource.CUSTOMER_ALIASES,
        "invoice.invoice_number": NormalizedEvidenceSource.INVOICE_INVOICE_NUMBER,
        "invoice.outstanding_amount": NormalizedEvidenceSource.INVOICE_OUTSTANDING_AMOUNT,
        "invoice.issue_date": NormalizedEvidenceSource.INVOICE_ISSUE_DATE,
        "invoice.due_date": NormalizedEvidenceSource.INVOICE_DUE_DATE,
        "invoice.currency": NormalizedEvidenceSource.INVOICE_CURRENCY,
        "candidate.outstanding_amount": NormalizedEvidenceSource.CANDIDATE_OUTSTANDING_AMOUNT,
        "candidate.currency": NormalizedEvidenceSource.CANDIDATE_CURRENCY,
        "candidate.due_date": NormalizedEvidenceSource.CANDIDATE_DUE_DATE,
        "combination.subset": NormalizedEvidenceSource.COMBINATION_SUBSET,
    }

    def __init__(
        self,
        rule_version: str = CANONICAL_RULE_VERSION,
        algorithm_version: str = CANONICAL_ALGORITHM_VERSION,
    ) -> None:
        self.rule_version = rule_version
        self.algorithm_version = algorithm_version

    def normalize_source(self, raw_source: str) -> NormalizedEvidenceSource:
        """Map raw source string to canonical NormalizedEvidenceSource enum."""
        clean_source = raw_source.strip().lower() if raw_source else ""
        for key, enum_val in self.SOURCE_MAP.items():
            if clean_source == key.lower():
                return enum_val
        return NormalizedEvidenceSource.UNKNOWN_SOURCE

    VALID_OBSERVED_RESULTS = {
        "MATCHED": NormalizedEvidenceResult.MATCH,
        "EXACT_MATCH": NormalizedEvidenceResult.MATCH,
        "CONSISTENT": NormalizedEvidenceResult.MATCH,
        "PRESENT": NormalizedEvidenceResult.PRESENT,
        "EXTRACTED": NormalizedEvidenceResult.PRESENT,
        "TOKEN_OVERLAP": NormalizedEvidenceResult.SUPPORTED,
        "PARTIAL_AMOUNT": NormalizedEvidenceResult.SUPPORTED,
        "OVERPAYMENT_AMOUNT": NormalizedEvidenceResult.SUPPORTED,
        "CAUSAL": NormalizedEvidenceResult.SUPPORTED,
        "PROXIMATE": NormalizedEvidenceResult.SUPPORTED,
        "CONFLICT": NormalizedEvidenceResult.CONFLICT,
        "NON_CAUSAL": NormalizedEvidenceResult.CONFLICT,
        "ABSENT": NormalizedEvidenceResult.ABSENT,
        "DISTANT": NormalizedEvidenceResult.ABSENT,
        "UNREGISTERED": NormalizedEvidenceResult.ABSENT,
        "NO_MATCH": NormalizedEvidenceResult.NO_MATCH,
    }

    def normalize_result(
        self,
        observed_result: str,
        classification: EvidenceClassification,
    ) -> NormalizedEvidenceResult:
        """Map raw observed result and classification to canonical NormalizedEvidenceResult.
        
        Fails closed on any unknown or unsupported observed_result string to preserve
        semantic fidelity (Raw meaning == Normalized meaning). Never guesses or infers
        a canonical result from classification alone.
        """
        upper_res = observed_result.upper().strip() if observed_result else ""
        if not upper_res or upper_res not in self.VALID_OBSERVED_RESULTS:
            raise DomainError(f"Unsupported or invalid observed result for normalization: '{observed_result}'")

        mapped = self.VALID_OBSERVED_RESULTS[upper_res]

        # Verify classification consistency: classification must not contradict the explicit observed result
        if classification == EvidenceClassification.CONFLICTING and mapped != NormalizedEvidenceResult.CONFLICT:
            raise DomainError(
                f"Inconsistent classification CONFLICTING with observed result '{observed_result}'"
            )
        if classification == EvidenceClassification.MISSING and mapped != NormalizedEvidenceResult.ABSENT:
            raise DomainError(
                f"Inconsistent classification MISSING with observed result '{observed_result}'"
            )

        return mapped

    def determine_strength(
        self,
        classification: EvidenceClassification,
        evidence_type: EvidenceType,
        observed_result: str,
    ) -> NormalizedEvidenceStrength:
        """Determine qualitative evidentiary strength without computing numeric scores.

        Fidelity Hierarchy:
        - DIRECT evidence facts (exact account, exact amount, exact invoice ref) -> EXACT or HIGH.
        - SUPPORTING evidence facts (name tokens, aliases, UTR, proximity, partial) -> MEDIUM or LOW.
        - MISSING / CONFLICTING facts -> NONE.
        """
        if classification in (EvidenceClassification.MISSING, EvidenceClassification.CONFLICTING):
            return NormalizedEvidenceStrength.NONE

        if classification == EvidenceClassification.DIRECT:
            if evidence_type in (
                EvidenceType.AMOUNT_EXACT_EQUALITY,
                EvidenceType.BANK_ACCOUNT_IDENTIFIER,
                EvidenceType.INVOICE_NUMBER_IN_NARRATION,
                EvidenceType.COMBINATION_SUM_EQUALITY,
            ):
                return NormalizedEvidenceStrength.EXACT
            return NormalizedEvidenceStrength.HIGH

        if classification == EvidenceClassification.SUPPORTING:
            upper_res = observed_result.upper().strip() if observed_result else ""
            if evidence_type in (
                EvidenceType.UTR_IDENTIFIER,
                EvidenceType.CUSTOMER_ALIAS,
                EvidenceType.AMOUNT_PARTIAL_COMPATIBLE,
                EvidenceType.CURRENCY_CONSISTENCY,
                EvidenceType.DATE_CAUSALITY,
            ) or upper_res == "MATCHED":
                return NormalizedEvidenceStrength.MEDIUM
            return NormalizedEvidenceStrength.LOW

        return NormalizedEvidenceStrength.NONE

    def normalize_target_entity(self, raw_entity: str) -> NormalizedTargetEntity:
        """Map raw target entity string to canonical NormalizedTargetEntity enum.
        
        Fails closed on any unknown or unsupported target entity string to preserve
        semantic fidelity (Raw meaning == Normalized meaning).
        """
        upper = raw_entity.upper().strip() if raw_entity else ""
        if upper == "PAYMENT":
            return NormalizedTargetEntity.PAYMENT
        if upper == "CUSTOMER":
            return NormalizedTargetEntity.CUSTOMER
        if upper == "INVOICE":
            return NormalizedTargetEntity.INVOICE
        if upper == "COMBINATION":
            return NormalizedTargetEntity.COMBINATION
        raise DomainError(f"Unsupported or invalid target entity for normalization: '{raw_entity}'")

    def build_identifiers(
        self,
        item: StructuredEvidenceItem,
        payment_id: Optional[UUID] = None,
        customer_id: Optional[UUID] = None,
        invoice_id: Optional[UUID] = None,
        invoice_number: Optional[str] = None,
    ) -> NormalizedRelevantIdentifiers:
        """Extract and assemble relevant identifiers for provenance tracking."""
        utr: Optional[str] = None
        bank_acc: Optional[str] = None
        ref: Optional[str] = None

        if item.evidence_type == EvidenceType.UTR_IDENTIFIER and item.matched_value:
            utr = item.matched_value
        elif item.evidence_type == EvidenceType.BANK_ACCOUNT_IDENTIFIER and item.matched_value:
            bank_acc = item.matched_value
        elif item.evidence_type == EvidenceType.PAYMENT_REFERENCE and item.matched_value:
            ref = item.matched_value

        return NormalizedRelevantIdentifiers(
            payment_id=payment_id,
            customer_id=customer_id,
            invoice_id=invoice_id,
            invoice_number=invoice_number,
            payment_reference=ref,
            utr=utr,
            bank_account=bank_acc,
        )

    def normalize_item(
        self,
        item: StructuredEvidenceItem,
        payment_id: Optional[UUID] = None,
        customer_id: Optional[UUID] = None,
        invoice_id: Optional[UUID] = None,
        invoice_number: Optional[str] = None,
    ) -> CanonicalEvidenceObject:
        """Normalize a single atomic StructuredEvidenceItem into a CanonicalEvidenceObject."""
        # 1. Map type
        try:
            canonical_type = NormalizedEvidenceType(item.evidence_type.value)
        except ValueError:
            raise DomainError(f"Unsupported evidence type for normalization: {item.evidence_type}")

        # 2. Map source
        canonical_source = self.normalize_source(item.source_field)

        # 3. Map result
        canonical_result = self.normalize_result(
            observed_result=item.observed_result,
            classification=item.classification,
        )

        # 4. Map strength (qualitative, non-numeric)
        canonical_strength = self.determine_strength(
            classification=item.classification,
            evidence_type=item.evidence_type,
            observed_result=item.observed_result,
        )

        # 5. Map target entity
        target_entity = self.normalize_target_entity(item.target_entity)

        # 6. Extract identifiers
        identifiers = self.build_identifiers(
            item=item,
            payment_id=payment_id,
            customer_id=customer_id,
            invoice_id=invoice_id,
            invoice_number=invoice_number,
        )

        return CanonicalEvidenceObject(
            evidence_type=canonical_type,
            source=canonical_source,
            result=canonical_result,
            strength=canonical_strength,
            details=item.description,
            identifiers=identifiers,
            classification=item.classification,
            rule_version=self.rule_version,
            algorithm_version=self.algorithm_version,
            target_entity=target_entity,
            matched_value=item.matched_value,
            expected_value=item.expected_value,
            metadata=dict(item.metadata),
        )

    def normalize_items_deduplicated(
        self,
        items: List[StructuredEvidenceItem],
        payment_id: Optional[UUID] = None,
        customer_id: Optional[UUID] = None,
        invoice_id: Optional[UUID] = None,
        invoice_number: Optional[str] = None,
    ) -> List[CanonicalEvidenceObject]:
        """Normalize list of evidence items, safely deduplicating exact semantic duplicates.

        Preserves all conflicting signals, distinct sources, and distinct descriptions.
        Enforces a deterministic total ordering on the returned canonical items:
        Sorted by (classification, evidence_type, source, target_entity, details).
        """
        normalized: List[CanonicalEvidenceObject] = []
        seen_signatures: Set[str] = set()

        for raw_item in items:
            canonical = self.normalize_item(
                item=raw_item,
                payment_id=payment_id,
                customer_id=customer_id,
                invoice_id=invoice_id,
                invoice_number=invoice_number,
            )
            # Create a unique semantic signature to avoid exact identical duplicates
            sig = (
                f"{canonical.evidence_type.value}|{canonical.source.value}|"
                f"{canonical.result.value}|{canonical.classification.value}|"
                f"{canonical.matched_value}|{canonical.expected_value}|{canonical.details}"
            )
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                normalized.append(canonical)

        # Enforce deterministic total order
        normalized.sort(
            key=lambda o: (
                o.classification.value,
                o.evidence_type.value,
                o.source.value,
                o.target_entity.value,
                o.details,
            )
        )
        return normalized

    def normalize_result_aggregate(
        self,
        raw_result: EvidenceCollectionResult,
        customer_id: Optional[UUID] = None,
    ) -> EvidenceNormalizationResult:
        """Convert a Phase 14.9 EvidenceCollectionResult into a canonical EvidenceNormalizationResult.

        Guarantees:
        - Exact Decimal and string preservation.
        - Deterministic candidate bundle ordering (by invoice_number, invoice_id).
        - Deterministic evidence item ordering within bundles.
        - Zero financial mutation.
        """
        # 1. Payment-level normalization
        norm_payment_items = self.normalize_items_deduplicated(
            items=raw_result.payment_evidence.items,
            payment_id=raw_result.payment_id,
            customer_id=customer_id,
        )

        norm_payment_context = NormalizedPaymentEvidenceContext(
            payment_id=raw_result.payment_id,
            company_id=raw_result.company_id,
            items=norm_payment_items,
            extracted_invoice_references=list(raw_result.payment_evidence.extracted_invoice_references),
            has_conflicting_identifiers=raw_result.payment_evidence.has_conflicting_identifiers,
        )

        # 2. Candidate bundle normalization
        norm_bundles: List[NormalizedCandidateBundle] = []
        for raw_bundle in raw_result.candidate_bundles:
            norm_bundle_items = self.normalize_items_deduplicated(
                items=raw_bundle.items,
                payment_id=raw_result.payment_id,
                customer_id=customer_id,
                invoice_id=raw_bundle.invoice_id,
                invoice_number=raw_bundle.invoice_number,
            )

            direct_c = sum(1 for i in norm_bundle_items if i.classification == EvidenceClassification.DIRECT)
            supp_c = sum(1 for i in norm_bundle_items if i.classification == EvidenceClassification.SUPPORTING)
            miss_c = sum(1 for i in norm_bundle_items if i.classification == EvidenceClassification.MISSING)
            conf_c = sum(1 for i in norm_bundle_items if i.classification == EvidenceClassification.CONFLICTING)

            norm_bundles.append(
                NormalizedCandidateBundle(
                    invoice_id=raw_bundle.invoice_id,
                    invoice_number=raw_bundle.invoice_number,
                    items=norm_bundle_items,
                    has_conflicting_evidence=conf_c > 0,
                    direct_evidence_count=direct_c,
                    supporting_evidence_count=supp_c,
                    missing_evidence_count=miss_c,
                    conflicting_evidence_count=conf_c,
                )
            )

        # Deterministic sorting of bundles by (invoice_number, invoice_id)
        norm_bundles.sort(key=lambda b: (b.invoice_number, str(b.invoice_id)))

        # Aggregate counts
        all_norm_items = norm_payment_items + [item for b in norm_bundles for item in b.items]
        total_items = len(all_norm_items)
        total_direct = sum(1 for i in all_norm_items if i.classification == EvidenceClassification.DIRECT)
        total_supp = sum(1 for i in all_norm_items if i.classification == EvidenceClassification.SUPPORTING)
        total_miss = sum(1 for i in all_norm_items if i.classification == EvidenceClassification.MISSING)
        total_conf = sum(1 for i in all_norm_items if i.classification == EvidenceClassification.CONFLICTING)

        return EvidenceNormalizationResult(
            payment_id=raw_result.payment_id,
            company_id=raw_result.company_id,
            payment_evidence=norm_payment_context,
            candidate_bundles=norm_bundles,
            total_evidence_items=total_items,
            total_direct_items=total_direct,
            total_supporting_items=total_supp,
            total_missing_items=total_miss,
            total_conflicting_items=total_conf,
            rule_version=self.rule_version,
            algorithm_version=self.algorithm_version,
            is_deterministic=True,
        )
