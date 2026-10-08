"""Domain entities, value objects, taxonomy, and engine for Phase 14.9 Evidence Collection.

Phase 14.9 establishes a bounded, deterministic, read-only evidence collection layer
that collects available reconciliation facts/signals across payment intake, customer context,
and candidate invoices, classifying them into structured evidence items:
- DIRECT: Strong explicit identifiers or deterministic proofs (bank account, UTR, explicit invoice reference, etc.)
- SUPPORTING: Contextual signals that support a candidate without uniquely proving it (name tokens, aliases, date proximity, amount relations, historical patterns)
- MISSING: Expected reconciliation evidence that is absent (no reference, no UTR, no usable customer identifier)
- CONFLICTING: Two or more authoritative signals materially disagree (e.g. payment reference points to Customer A, bank account belongs to Customer B; or explicit invoice reference points to Invoice A while candidate is Invoice B)

CRITICAL INVARIANTS:
1. Evidence Collection != Matching != Scoring != Confidence != Decision != Approval != Financial Mutation.
2. Evidence layer describes WHAT exists and WHAT it says; it NEVER computes scores, weights, or probabilities.
3. Observed Financial Mutation: NONE (Strictly read-only evaluation).
4. Determinism: 100% deterministic, zero LLM authority, zero float math.
5. Incomplete / Unavailable Data is classified as MISSING, never fabricated.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from app.modules.reconciliation.domain.candidate_invoices import CandidateInvoice
from app.modules.reconciliation.domain.normalizer import PayerStringNormalizer
from app.modules.reconciliation.domain.rules import CustomerLookupContext, PaymentIntakeContext
from app.shared.exceptions import DomainError


class EvidenceClassification(str, Enum):
    """Authoritative Phase 14.9 four-state evidence classification taxonomy."""

    DIRECT = "DIRECT"
    SUPPORTING = "SUPPORTING"
    MISSING = "MISSING"
    CONFLICTING = "CONFLICTING"


class EvidenceType(str, Enum):
    """Authoritative categorization of evidence across reconciliation dimensions."""

    # Customer & Account Identifiers
    BANK_ACCOUNT_IDENTIFIER = "BANK_ACCOUNT_IDENTIFIER"
    UPI_VPA_IDENTIFIER = "UPI_VPA_IDENTIFIER"
    CUSTOMER_NAME_TOKEN = "CUSTOMER_NAME_TOKEN"
    CUSTOMER_ALIAS = "CUSTOMER_ALIAS"
    CUSTOMER_TAX_ID = "CUSTOMER_TAX_ID"

    # Payment & Transfer Identifiers
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

    # Historical Pattern
    HISTORICAL_CUSTOMER_PATTERN = "HISTORICAL_CUSTOMER_PATTERN"

    # General / Extension Identifiers
    PURCHASE_ORDER_REFERENCE = "PURCHASE_ORDER_REFERENCE"


@dataclass(frozen=True)
class StructuredEvidenceItem:
    """Immutable atomic structured evidence item produced by Phase 14.9 Evidence Collection.

    Contains zero score, zero weight, and zero probabilistic calibration.
    """

    evidence_type: EvidenceType
    classification: EvidenceClassification
    source_field: str
    observed_result: str
    description: str
    target_entity: str  # "CUSTOMER", "INVOICE", "PAYMENT", "COMBINATION"
    matched_value: Optional[str] = None
    expected_value: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert evidence item to serializable dictionary."""
        return {
            "evidence_type": self.evidence_type.value,
            "classification": self.classification.value,
            "source_field": self.source_field,
            "observed_result": self.observed_result,
            "description": self.description,
            "target_entity": self.target_entity,
            "matched_value": self.matched_value,
            "expected_value": self.expected_value,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class CandidateEvidenceBundle:
    """Structured collection of evidence items evaluated for a specific candidate invoice."""

    invoice_id: UUID
    invoice_number: str
    items: List[StructuredEvidenceItem]
    has_conflicting_evidence: bool
    direct_evidence_count: int
    supporting_evidence_count: int
    missing_evidence_count: int
    conflicting_evidence_count: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert candidate evidence bundle to serializable dictionary."""
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
class PaymentEvidenceContext:
    """Payment-level structured evidence facts before candidate pairing."""

    payment_id: UUID
    company_id: UUID
    items: List[StructuredEvidenceItem]
    extracted_invoice_references: List[str]
    has_conflicting_identifiers: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert payment evidence context to serializable dictionary."""
        return {
            "payment_id": str(self.payment_id),
            "company_id": str(self.company_id),
            "items": [item.to_dict() for item in self.items],
            "extracted_invoice_references": self.extracted_invoice_references,
            "has_conflicting_identifiers": self.has_conflicting_identifiers,
        }


@dataclass(frozen=True)
class EvidenceCollectionResult:
    """Authoritative result of Phase 14.9 Evidence Collection.

    Strictly read-only evaluation. Describes all available facts, classifies them into
    DIRECT, SUPPORTING, MISSING, CONFLICTING, and exposes full provenance without scoring.
    """

    payment_id: UUID
    company_id: UUID
    payment_evidence: PaymentEvidenceContext
    candidate_bundles: List[CandidateEvidenceBundle]
    total_evidence_items: int
    total_direct_items: int
    total_supporting_items: int
    total_missing_items: int
    total_conflicting_items: int
    is_deterministic: bool = True
    collected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_deterministic_payload(self) -> Dict[str, Any]:
        """Convert result to serializable payload containing ONLY deterministic evidence facts.

        Excludes non-deterministic execution metadata such as collected_at evaluation timestamp.
        Two evaluations on the same input are guaranteed to produce bit-for-bit identical
        deterministic payloads.
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
            "is_deterministic": self.is_deterministic,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to serializable dictionary including evaluation metadata."""
        payload = self.to_deterministic_payload()
        payload["collected_at"] = self.collected_at.isoformat()
        return payload


class EvidenceCollectionEngine:
    """Pure domain rule engine for Phase 14.9 Evidence Collection.

    Extracts and classifies evidence items across payment, customer context, and candidate invoices.
    Guarantees:
    - Zero floating-point arithmetic (exact Decimal).
    - Zero database mutations (pure read-only).
    - Zero scoring / weighting (strictly deferred to Phase 14.11).
    - Preserves all conflicting and missing signals without loss.
    """

    UTR_REGEX = re.compile(r"\bUTR(?:\s*(?:NO|NUMBER|REF(?:ERENCE)?))?\s*[:#-]\s*([A-Z0-9]{8,22})\b", re.IGNORECASE)
    INVOICE_PATTERN = re.compile(r"\b(?:INV|BILL|REC)[-_A-Z0-9]{3,30}\b", re.IGNORECASE)

    def __init__(self, normalizer: Optional[PayerStringNormalizer] = None) -> None:
        self.normalizer = normalizer or PayerStringNormalizer()

    def collect_payment_evidence(
        self,
        payment: PaymentIntakeContext,
        customer: Optional[CustomerLookupContext] = None,
        all_customers: Optional[List[CustomerLookupContext]] = None,
    ) -> PaymentEvidenceContext:
        """Extract and classify payment-level evidence facts."""
        items: List[StructuredEvidenceItem] = []
        extracted_invoice_refs: List[str] = []

        # 1. Payment Reference
        if payment.payment_reference and payment.payment_reference.strip():
            ref_clean = payment.payment_reference.strip()
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.PAYMENT_REFERENCE,
                    classification=EvidenceClassification.SUPPORTING,
                    source_field="payment.payment_reference",
                    observed_result="PRESENT",
                    description=f"Payment reference is provided: {ref_clean}",
                    target_entity="PAYMENT",
                    matched_value=ref_clean,
                )
            )
            # Canonicalize invoice-like references before downstream comparison.
            if self.INVOICE_PATTERN.match(ref_clean):
                extracted_invoice_refs.append(ref_clean.upper().strip())
        else:
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.PAYMENT_REFERENCE,
                    classification=EvidenceClassification.MISSING,
                    source_field="payment.payment_reference",
                    observed_result="ABSENT",
                    description="Payment reference is missing or empty",
                    target_entity="PAYMENT",
                )
            )

        # 2. Narration & UTR
        narration_raw = payment.narration or ""
        if narration_raw.strip():
            narration_cleaned = self.normalizer.clean_text(narration_raw)
            # Extract possible invoice numbers from narration
            found_inv_tokens = self.INVOICE_PATTERN.findall(narration_raw)
            for tok in found_inv_tokens:
                clean_tok = tok.upper().strip()
                if clean_tok not in extracted_invoice_refs:
                    extracted_invoice_refs.append(clean_tok)

            if found_inv_tokens:
                items.append(
                    StructuredEvidenceItem(
                        evidence_type=EvidenceType.INVOICE_NUMBER_IN_NARRATION,
                        classification=EvidenceClassification.DIRECT,
                        source_field="payment.narration",
                        observed_result="MATCHED",
                        description=f"Narration explicitly mentions invoice token(s): {', '.join(found_inv_tokens)}",
                        target_entity="PAYMENT",
                        matched_value=", ".join(found_inv_tokens),
                    )
                )

            # UTR detection
            utr_matches = self.UTR_REGEX.findall(narration_raw)
            if utr_matches:
                utr_val = utr_matches[0]
                items.append(
                    StructuredEvidenceItem(
                        evidence_type=EvidenceType.UTR_IDENTIFIER,
                        classification=EvidenceClassification.SUPPORTING,
                        source_field="payment.narration",
                        observed_result="EXTRACTED",
                        description=f"Extracted bank UTR reference: {utr_val}",
                        target_entity="PAYMENT",
                        matched_value=utr_val,
                    )
                )
            else:
                items.append(
                    StructuredEvidenceItem(
                        evidence_type=EvidenceType.UTR_IDENTIFIER,
                        classification=EvidenceClassification.MISSING,
                        source_field="payment.narration",
                        observed_result="ABSENT",
                        description="No standard UTR pattern detected in payment narration",
                        target_entity="PAYMENT",
                    )
                )
        else:
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.INVOICE_NUMBER_IN_NARRATION,
                    classification=EvidenceClassification.MISSING,
                    source_field="payment.narration",
                    observed_result="ABSENT",
                    description="Payment narration is missing or empty",
                    target_entity="PAYMENT",
                )
            )
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.UTR_IDENTIFIER,
                    classification=EvidenceClassification.MISSING,
                    source_field="payment.narration",
                    observed_result="ABSENT",
                    description="Payment narration is empty; UTR is missing",
                    target_entity="PAYMENT",
                )
            )

        # 3. Payment Coordinate Identifier & Cross-Customer Conflict Check
        has_conflicting_identifiers = False
        bank_account = (payment.bank_account_number or "").strip()
        payer_identifier = (payment.payer_raw_identifier or "").strip()

        if bank_account:
            clean_acc = bank_account
            if customer:
                cust_accounts = [val for (itype, val) in customer.identifiers if itype in ("BANK_ACCOUNT", "VIRTUAL_ACCOUNT")]
                if clean_acc in cust_accounts or any(clean_acc.endswith(ca) or ca.endswith(clean_acc) for ca in cust_accounts):
                    items.append(StructuredEvidenceItem(
                        evidence_type=EvidenceType.BANK_ACCOUNT_IDENTIFIER,
                        classification=EvidenceClassification.DIRECT,
                        source_field="payment.bank_account_number",
                        observed_result="MATCHED",
                        description=f"Payment account matches registered customer bank account: {clean_acc}",
                        target_entity="CUSTOMER",
                        matched_value=clean_acc,
                    ))
                else:
                    items.append(StructuredEvidenceItem(
                        evidence_type=EvidenceType.BANK_ACCOUNT_IDENTIFIER,
                        classification=EvidenceClassification.MISSING,
                        source_field="payment.bank_account_number",
                        observed_result="UNREGISTERED",
                        description=f"Payment account {clean_acc} is not in resolved customer's registered identifiers",
                        target_entity="CUSTOMER",
                        matched_value=clean_acc,
                    ))
            if all_customers and customer:
                for other_c in all_customers:
                    if other_c.customer_id != customer.customer_id and not other_c.is_archived:
                        other_accs = [val for (itype, val) in other_c.identifiers if itype in ("BANK_ACCOUNT", "VIRTUAL_ACCOUNT")]
                        if clean_acc in other_accs:
                            has_conflicting_identifiers = True
                            items.append(StructuredEvidenceItem(
                                evidence_type=EvidenceType.BANK_ACCOUNT_IDENTIFIER,
                                classification=EvidenceClassification.CONFLICTING,
                                source_field="payment.bank_account_number",
                                observed_result="CONFLICT",
                                description=f"Payment account {clean_acc} belongs to Customer '{other_c.name}'",
                                target_entity="CUSTOMER",
                                matched_value=clean_acc,
                            ))
        elif payer_identifier and "@" in payer_identifier:
            clean_vpa = payer_identifier
            matched = False
            if customer:
                customer_vpas = [val for (itype, val) in customer.identifiers if itype == "UPI_VPA"]
                if clean_vpa in customer_vpas:
                    matched = True
                    items.append(StructuredEvidenceItem(
                        evidence_type=EvidenceType.UPI_VPA_IDENTIFIER,
                        classification=EvidenceClassification.DIRECT,
                        source_field="payment.payer_raw_identifier",
                        observed_result="MATCHED",
                        description=f"Payment UPI VPA matches registered customer identifier: {clean_vpa}",
                        target_entity="CUSTOMER",
                        matched_value=clean_vpa,
                    ))
            if all_customers and customer:
                for other_c in all_customers:
                    if other_c.customer_id != customer.customer_id and not other_c.is_archived:
                        other_vpas = [val for (itype, val) in other_c.identifiers if itype == "UPI_VPA"]
                        if clean_vpa in other_vpas:
                            has_conflicting_identifiers = True
                            matched = True
                            items.append(StructuredEvidenceItem(
                                evidence_type=EvidenceType.UPI_VPA_IDENTIFIER,
                                classification=EvidenceClassification.CONFLICTING,
                                source_field="payment.payer_raw_identifier",
                                observed_result="CONFLICT",
                                description=f"Payment UPI VPA {clean_vpa} belongs to a different registered customer",
                                target_entity="CUSTOMER",
                                matched_value=clean_vpa,
                            ))
            if not matched:
                items.append(StructuredEvidenceItem(
                    evidence_type=EvidenceType.UPI_VPA_IDENTIFIER,
                    classification=EvidenceClassification.MISSING,
                    source_field="payment.payer_raw_identifier",
                    observed_result="UNREGISTERED",
                    description=f"Payment UPI VPA {clean_vpa} is not in registered customer identifiers",
                    target_entity="CUSTOMER",
                    matched_value=clean_vpa,
                ))
        else:
            items.append(StructuredEvidenceItem(
                evidence_type=EvidenceType.BANK_ACCOUNT_IDENTIFIER,
                classification=EvidenceClassification.MISSING,
                source_field="payment.bank_account_number",
                observed_result="ABSENT",
                description="Payment does not contain a bank account or UPI payer identifier",
                target_entity="CUSTOMER",
            ))

        # 4. Customer Name / Alias Evidence
        if customer:
            clean_cust_name = self.normalizer.clean_text(customer.name)
            narration_cleaned = self.normalizer.clean_text(payment.narration or "")
            payer_name_cleaned = self.normalizer.clean_text(payment.payer_raw_name or "")

            # Match on name tokens
            name_tokens = set(clean_cust_name.split())
            narration_tokens = set(narration_cleaned.split()) | set(payer_name_cleaned.split())
            shared_tokens = name_tokens & narration_tokens

            if len(shared_tokens) > 0 and clean_cust_name in (narration_cleaned or payer_name_cleaned):
                items.append(
                    StructuredEvidenceItem(
                        evidence_type=EvidenceType.CUSTOMER_NAME_TOKEN,
                        classification=EvidenceClassification.SUPPORTING,
                        source_field="payment.narration",
                        observed_result="MATCHED",
                        description=f"Full customer name '{customer.name}' found in payment text",
                        target_entity="CUSTOMER",
                        matched_value=customer.name,
                    )
                )
            elif shared_tokens:
                items.append(
                    StructuredEvidenceItem(
                        evidence_type=EvidenceType.CUSTOMER_NAME_TOKEN,
                        classification=EvidenceClassification.SUPPORTING,
                        source_field="payment.narration",
                        observed_result="TOKEN_OVERLAP",
                        description=f"Customer name tokens overlap with payment text: {', '.join(sorted(shared_tokens))}",
                        target_entity="CUSTOMER",
                        matched_value=", ".join(sorted(shared_tokens)),
                    )
                )
            else:
                items.append(
                    StructuredEvidenceItem(
                        evidence_type=EvidenceType.CUSTOMER_NAME_TOKEN,
                        classification=EvidenceClassification.MISSING,
                        source_field="payment.narration",
                        observed_result="ABSENT",
                        description=f"No customer name tokens for '{customer.name}' found in payment text",
                        target_entity="CUSTOMER",
                    )
                )

            # Check aliases
            alias_matched = False
            for alias in customer.aliases:
                clean_alias = self.normalizer.clean_text(alias)
                if clean_alias and (clean_alias in narration_cleaned or clean_alias in payer_name_cleaned):
                    alias_matched = True
                    items.append(
                        StructuredEvidenceItem(
                            evidence_type=EvidenceType.CUSTOMER_ALIAS,
                            classification=EvidenceClassification.SUPPORTING,
                            source_field="payment.narration",
                            observed_result="MATCHED",
                            description=f"Known customer alias '{alias}' matched in payment text",
                            target_entity="CUSTOMER",
                            matched_value=alias,
                        )
                    )
                    break
            if not alias_matched and customer.aliases:
                items.append(
                    StructuredEvidenceItem(
                        evidence_type=EvidenceType.CUSTOMER_ALIAS,
                        classification=EvidenceClassification.MISSING,
                        source_field="payment.narration",
                        observed_result="ABSENT",
                        description="Customer has registered aliases, but none were detected in payment text",
                        target_entity="CUSTOMER",
                    )
                )

        return PaymentEvidenceContext(
            payment_id=payment.payment_id,
            company_id=payment.company_id,
            items=items,
            extracted_invoice_references=extracted_invoice_refs,
            has_conflicting_identifiers=has_conflicting_identifiers,
        )

    def collect_candidate_evidence(
        self,
        payment: PaymentIntakeContext,
        candidate: CandidateInvoice,
        payment_evidence: PaymentEvidenceContext,
        customer: Optional[CustomerLookupContext] = None,
    ) -> CandidateEvidenceBundle:
        """Extract and classify evidence items relating payment to a specific CandidateInvoice."""
        items: List[StructuredEvidenceItem] = []
        effective_amount = payment.effective_amount

        # 1. Invoice Number Direct Match / Conflict Check
        inv_clean = candidate.invoice_number.upper().strip()
        matched_in_refs = any(
            inv_clean == ref
            for ref in payment_evidence.extracted_invoice_references
        )

        if matched_in_refs:
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.INVOICE_NUMBER_IN_NARRATION,
                    classification=EvidenceClassification.DIRECT,
                    source_field="payment.narration",
                    observed_result="MATCHED",
                    description=f"Candidate invoice number {candidate.invoice_number} is explicitly referenced in payment",
                    target_entity="INVOICE",
                    matched_value=candidate.invoice_number,
                )
            )
        elif payment_evidence.extracted_invoice_references:
            # Payment explicitly mentioned a DIFFERENT invoice number!
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.INVOICE_NUMBER_IN_NARRATION,
                    classification=EvidenceClassification.CONFLICTING,
                    source_field="payment.narration",
                    observed_result="CONFLICT",
                    description=(
                        f"Payment explicitly references invoice(s) '{', '.join(payment_evidence.extracted_invoice_references)}', "
                        f"conflicting with candidate {candidate.invoice_number}"
                    ),
                    target_entity="INVOICE",
                    matched_value=", ".join(payment_evidence.extracted_invoice_references),
                    expected_value=candidate.invoice_number,
                )
            )
        else:
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.INVOICE_NUMBER_IN_NARRATION,
                    classification=EvidenceClassification.MISSING,
                    source_field="payment.narration",
                    observed_result="ABSENT",
                    description=f"Payment text does not reference invoice number {candidate.invoice_number}",
                    target_entity="INVOICE",
                )
            )

        # 2. Currency Consistency
        if payment.currency.upper() == candidate.currency.upper():
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.CURRENCY_CONSISTENCY,
                    classification=EvidenceClassification.SUPPORTING,
                    source_field="candidate.currency",
                    observed_result="CONSISTENT",
                    description=f"Currency matches: {payment.currency}",
                    target_entity="INVOICE",
                    matched_value=payment.currency,
                )
            )
        else:
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.CURRENCY_CONSISTENCY,
                    classification=EvidenceClassification.CONFLICTING,
                    source_field="candidate.currency",
                    observed_result="CONFLICT",
                    description=f"Currency mismatch: payment is {payment.currency}, candidate invoice is {candidate.currency}",
                    target_entity="INVOICE",
                    matched_value=payment.currency,
                    expected_value=candidate.currency,
                )
            )

        # 3. Amount Relationship (Exact Decimal, No Floats)
        if effective_amount == candidate.outstanding_amount:
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.AMOUNT_EXACT_EQUALITY,
                    classification=EvidenceClassification.DIRECT,
                    source_field="candidate.outstanding_amount",
                    observed_result="EXACT_MATCH",
                    description=f"Payment effective amount ({effective_amount}) exactly equals invoice outstanding amount ({candidate.outstanding_amount})",
                    target_entity="INVOICE",
                    matched_value=str(effective_amount),
                    expected_value=str(candidate.outstanding_amount),
                )
            )
        elif effective_amount < candidate.outstanding_amount:
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.AMOUNT_PARTIAL_COMPATIBLE,
                    classification=EvidenceClassification.SUPPORTING,
                    source_field="candidate.outstanding_amount",
                    observed_result="PARTIAL_AMOUNT",
                    description=f"Payment effective amount ({effective_amount}) is less than invoice outstanding ({candidate.outstanding_amount})",
                    target_entity="INVOICE",
                    matched_value=str(effective_amount),
                    expected_value=str(candidate.outstanding_amount),
                )
            )
        else:
            # effective_amount > candidate.outstanding_amount (Overpayment relationship)
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.AMOUNT_OVERPAYMENT,
                    classification=EvidenceClassification.SUPPORTING,
                    source_field="candidate.outstanding_amount",
                    observed_result="OVERPAYMENT_AMOUNT",
                    description=f"Payment effective amount ({effective_amount}) exceeds candidate invoice outstanding ({candidate.outstanding_amount})",
                    target_entity="INVOICE",
                    matched_value=str(effective_amount),
                    expected_value=str(candidate.outstanding_amount),
                )
            )

        # 4. Temporal Relationship (Causality and Date Proximity)
        # Causality: payment date must be on or after issue date
        if payment.payment_date >= candidate.issue_date:
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.DATE_CAUSALITY,
                    classification=EvidenceClassification.SUPPORTING,
                    source_field="payment.payment_date",
                    observed_result="CAUSAL",
                    description=f"Payment date ({payment.payment_date.isoformat()}) is on or after invoice issue date ({candidate.issue_date.isoformat()})",
                    target_entity="INVOICE",
                    matched_value=payment.payment_date.isoformat(),
                )
            )
        else:
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.DATE_CAUSALITY,
                    classification=EvidenceClassification.CONFLICTING,
                    source_field="payment.payment_date",
                    observed_result="NON_CAUSAL",
                    description=f"Payment date ({payment.payment_date.isoformat()}) precedes invoice issue date ({candidate.issue_date.isoformat()})",
                    target_entity="INVOICE",
                    matched_value=payment.payment_date.isoformat(),
                    expected_value=candidate.issue_date.isoformat(),
                )
            )

        # Date Proximity to Due Date
        days_from_due = (payment.payment_date - candidate.due_date).days
        abs_days = abs(days_from_due)
        if abs_days <= 30:
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.DATE_PROXIMITY,
                    classification=EvidenceClassification.SUPPORTING,
                    source_field="candidate.due_date",
                    observed_result="PROXIMATE",
                    description=f"Payment date is within {abs_days} days of invoice due date ({candidate.due_date.isoformat()})",
                    target_entity="INVOICE",
                    matched_value=str(abs_days),
                    metadata={"days_difference": days_from_due},
                )
            )
        else:
            items.append(
                StructuredEvidenceItem(
                    evidence_type=EvidenceType.DATE_PROXIMITY,
                    classification=EvidenceClassification.MISSING,
                    source_field="candidate.due_date",
                    observed_result="DISTANT",
                    description=f"Payment date is {abs_days} days away from invoice due date (> 30 days)",
                    target_entity="INVOICE",
                    matched_value=str(abs_days),
                    metadata={"days_difference": days_from_due},
                )
            )

        # Count classifications
        direct_c = sum(1 for i in items if i.classification == EvidenceClassification.DIRECT)
        supp_c = sum(1 for i in items if i.classification == EvidenceClassification.SUPPORTING)
        miss_c = sum(1 for i in items if i.classification == EvidenceClassification.MISSING)
        conf_c = sum(1 for i in items if i.classification == EvidenceClassification.CONFLICTING)

        return CandidateEvidenceBundle(
            invoice_id=candidate.invoice_id,
            invoice_number=candidate.invoice_number,
            items=items,
            has_conflicting_evidence=conf_c > 0,
            direct_evidence_count=direct_c,
            supporting_evidence_count=supp_c,
            missing_evidence_count=miss_c,
            conflicting_evidence_count=conf_c,
        )

    def evaluate(
        self,
        payment: PaymentIntakeContext,
        candidates: List[CandidateInvoice],
        customer: Optional[CustomerLookupContext] = None,
        all_customers: Optional[List[CustomerLookupContext]] = None,
    ) -> EvidenceCollectionResult:
        """Execute complete Phase 14.9 Evidence Collection evaluation.

        Collects payment evidence and candidate-specific evidence bundles.
        Guarantees:
        - 100% deterministic (re-running on same input produces identical output).
        - Zero financial mutation.
        - Zero scoring/weighting.
        """
        if payment.company_id is None:
            raise DomainError("PaymentIntakeContext must have a valid company_id for tenant safety")

        # 1. Collect payment-level evidence
        payment_evidence = self.collect_payment_evidence(
            payment=payment,
            customer=customer,
            all_customers=all_customers,
        )

        # 2. Collect candidate-specific evidence
        bundles: List[CandidateEvidenceBundle] = []
        for cand in candidates:
            # Enforce authoritative tenant boundary invariant:
            # CandidateInvoice company_id (if present) MUST match payment.company_id.
            cand_company_id = getattr(cand, "company_id", None)
            if cand_company_id is not None and cand_company_id != payment.company_id:
                raise DomainError(
                    f"Cross-tenant candidate invoice detected! Candidate invoice {cand.invoice_number} "
                    f"belongs to company {cand_company_id}, violating tenant boundary for payment company {payment.company_id}."
                )

            bundle = self.collect_candidate_evidence(
                payment=payment,
                candidate=cand,
                payment_evidence=payment_evidence,
                customer=customer,
            )
            bundles.append(bundle)

        # Enforce deterministic total order on candidate bundles:
        # Bundles must be sorted by (invoice_number, invoice_id) so the collection result
        # is completely permutation-invariant regardless of input candidate ordering.
        bundles.sort(key=lambda b: (b.invoice_number, str(b.invoice_id)))

        # Aggregation
        all_items = payment_evidence.items + [item for b in bundles for item in b.items]
        total_items = len(all_items)
        total_direct = sum(1 for i in all_items if i.classification == EvidenceClassification.DIRECT)
        total_supp = sum(1 for i in all_items if i.classification == EvidenceClassification.SUPPORTING)
        total_miss = sum(1 for i in all_items if i.classification == EvidenceClassification.MISSING)
        total_conf = sum(1 for i in all_items if i.classification == EvidenceClassification.CONFLICTING)

        return EvidenceCollectionResult(
            payment_id=payment.payment_id,
            company_id=payment.company_id,
            payment_evidence=payment_evidence,
            candidate_bundles=bundles,
            total_evidence_items=total_items,
            total_direct_items=total_direct,
            total_supporting_items=total_supp,
            total_missing_items=total_miss,
            total_conflicting_items=total_conf,
            is_deterministic=True,
        )
