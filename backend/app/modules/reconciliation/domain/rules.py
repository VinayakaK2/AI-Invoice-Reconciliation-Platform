"""Deterministic rule engine for payment counterparty identification."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID

from app.modules.reconciliation.domain.entities import (
    CustomerIdentificationResult,
    CustomerMatchCandidate,
    EvidenceSignal,
    EvidenceType,
    IdentificationStatus,
    SignalStrength,
)
from app.modules.reconciliation.domain.normalizer import PayerStringNormalizer
from app.modules.reconciliation.domain.payment_intake import (
    PaymentIntakeRuleEngine,
    PaymentIntakeStatus,
)


@dataclass(frozen=True)
class PaymentIntakeContext:
    """Immutable normalized payment context evaluated by the rule engine."""

    payment_id: UUID
    company_id: UUID
    amount: Decimal
    currency: str
    payment_date: date
    payer_raw_name: Optional[str] = None
    payer_raw_identifier: Optional[str] = None
    payment_reference: Optional[str] = None
    bank_account_number: Optional[str] = None
    narration: Optional[str] = None
    bank_transaction_id: Optional[UUID] = None
    allocated_amount: Decimal = Decimal("0.00")
    unallocated_amount: Optional[Decimal] = None
    status: str = "UNRECONCILED"
    transaction_type: str = "CREDIT"

    @property
    def effective_amount(self) -> Decimal:
        """Return remaining unallocated balance if specified, else gross amount."""
        if self.unallocated_amount is not None:
            return self.unallocated_amount
        return self.amount - self.allocated_amount


@dataclass(frozen=True)
class CustomerLookupContext:
    """Immutable customer data context evaluated by the rule engine."""

    customer_id: UUID
    name: str
    tax_id: Optional[str] = None
    is_archived: bool = False
    aliases: List[str] = field(default_factory=list)
    identifiers: List[Tuple[str, str]] = field(default_factory=list)  # (type, normalized_val)


class PayerIdentificationRuleEngine:
    """Deterministic, explainable rule engine for counterparty payer identification."""

    # Ambiguity score threshold: minimum separation required between top two candidates
    AMBIGUITY_DELTA_THRESHOLD = 15.0

    # Minimum score required for single counterparty identification
    IDENTIFICATION_SCORE_THRESHOLD = 70.0

    # Minimum score to be considered a viable ambiguous candidate (below this is UNKNOWN)
    VIABLE_CANDIDATE_THRESHOLD = 30.0

    def __init__(self, normalizer: Optional[PayerStringNormalizer] = None) -> None:
        self.normalizer = normalizer or PayerStringNormalizer()

    def evaluate(
        self,
        payment: PaymentIntakeContext,
        customers: List[CustomerLookupContext],
    ) -> CustomerIdentificationResult:
        """Deterministically evaluate payment against active customers and return identification result."""
        # Phase 14.1 Payment Intake Eligibility Gateway Evaluation
        intake_result = PaymentIntakeRuleEngine.evaluate(payment)
        if not intake_result.is_eligible:
            return CustomerIdentificationResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                status=IdentificationStatus.NOT_ELIGIBLE,
                primary_candidate=None,
                candidates=[],
                evidence_signals=[],
                total_evidence_score=0.0,
                reason_code=intake_result.reason_code,
                reason_description=intake_result.reason_description,
            )

        # Invariant: Archived customers must NEVER be evaluated as counterparty candidates
        active_customers = [c for c in customers if not c.is_archived]
        if not active_customers:
            return CustomerIdentificationResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                status=IdentificationStatus.UNKNOWN,
                primary_candidate=None,
                candidates=[],
                evidence_signals=[],
                total_evidence_score=0.0,
                reason_code="NO_ACTIVE_CUSTOMERS_CONFIGURED",
                reason_description="No active customers available in tenant workspace for counterparty matching.",
            )

        # Hoist narration coordinate extraction once per payment (O(1) instead of O(C))
        extracted_vpas = [
            self.normalizer.normalize_identifier(v)
            for v in self.normalizer.extract_potential_upi_vpas(payment.narration)
        ]
        extracted_accs = [
            self.normalizer.normalize_identifier(a)
            for a in self.normalizer.extract_potential_bank_accounts(payment.narration)
        ]

        # Collect evidence signals for each candidate customer
        candidate_signals: Dict[UUID, List[EvidenceSignal]] = {}
        for cust in active_customers:
            signals = self._evaluate_customer_signals(
                payment, cust, extracted_vpas=extracted_vpas, extracted_accs=extracted_accs
            )
            if signals:
                candidate_signals[cust.customer_id] = signals

        # If no customer produced any evidence signal
        if not candidate_signals:
            return CustomerIdentificationResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                status=IdentificationStatus.UNKNOWN,
                primary_candidate=None,
                candidates=[],
                evidence_signals=[],
                total_evidence_score=0.0,
                reason_code="NO_RECOGNIZABLE_COUNTERPARTY_EVIDENCE",
                reason_description="No direct identifiers, aliases, or name tokens matched known customers.",
            )

        # Build evaluated candidate objects
        customer_map = {c.customer_id: c for c in active_customers}
        all_candidates: List[CustomerMatchCandidate] = []

        for cust_id, signals in candidate_signals.items():
            cust = customer_map[cust_id]
            comp_score = self._compute_composite_score(signals)
            all_candidates.append(
                CustomerMatchCandidate(
                    customer_id=cust_id,
                    customer_name=cust.name,
                    composite_score=comp_score,
                    evidence_signals=signals,
                )
            )

        # Sort candidates deterministically: composite score DESC, then customer_name ASC, then customer_id ASC
        all_candidates.sort(key=lambda c: (-c.composite_score, c.customer_name, str(c.customer_id)))

        # Build all_signals strictly ordered by candidate rank
        all_signals: List[EvidenceSignal] = [
            sig for c in all_candidates for sig in c.evidence_signals
        ]

        # Re-assign ranks
        ranked_candidates = [
            CustomerMatchCandidate(
                customer_id=c.customer_id,
                customer_name=c.customer_name,
                composite_score=c.composite_score,
                evidence_signals=c.evidence_signals,
                rank=idx + 1,
            )
            for idx, c in enumerate(all_candidates)
        ]

        # Check for conflicting direct evidence across different customers
        conflict_result = self._detect_conflicting_signals(payment, ranked_candidates)
        if conflict_result:
            return conflict_result

        top_candidate = ranked_candidates[0]
        top_score = top_candidate.composite_score

        # Case 1: Top score below minimum viable threshold -> UNKNOWN
        if top_score < self.VIABLE_CANDIDATE_THRESHOLD:
            return CustomerIdentificationResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                status=IdentificationStatus.UNKNOWN,
                primary_candidate=None,
                candidates=ranked_candidates,
                evidence_signals=all_signals,
                total_evidence_score=top_score,
                reason_code="INSUFFICIENT_EVIDENCE_SCORE",
                reason_description=f"Top candidate score {top_score:.2f} is below minimum threshold {self.VIABLE_CANDIDATE_THRESHOLD:.2f}.",
            )

        # Case 2: Top score in low-confidence band [30.0, 70.0) -> AMBIGUOUS
        if top_score < self.IDENTIFICATION_SCORE_THRESHOLD:
            return CustomerIdentificationResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                status=IdentificationStatus.AMBIGUOUS,
                primary_candidate=None,
                candidates=ranked_candidates,
                evidence_signals=all_signals,
                total_evidence_score=top_score,
                reason_code="LOW_CONFIDENCE_COUNTERPARTY_MATCH",
                reason_description=f"Evidence score {top_score:.2f} indicates potential candidates but lacks certainty.",
            )

        # Case 3: Top score >= 70.0, check ambiguity delta against runner-up
        if len(ranked_candidates) > 1:
            runner_up = ranked_candidates[1]
            score_delta = top_score - runner_up.composite_score
            if score_delta < self.AMBIGUITY_DELTA_THRESHOLD:
                return CustomerIdentificationResult(
                    payment_id=payment.payment_id,
                    company_id=payment.company_id,
                    status=IdentificationStatus.AMBIGUOUS,
                    primary_candidate=None,
                    candidates=ranked_candidates,
                    evidence_signals=all_signals,
                    total_evidence_score=top_score,
                    reason_code="CLOSE_SCORE_COUNTERPARTY_AMBIGUITY",
                    reason_description=(
                        f"Top candidates '{top_candidate.customer_name}' ({top_score:.2f}) and "
                        f"'{runner_up.customer_name}' ({runner_up.composite_score:.2f}) differ by "
                        f"only {score_delta:.2f} points (threshold {self.AMBIGUITY_DELTA_THRESHOLD:.2f})."
                    ),
                )

        # Case 4: Top candidate satisfies all identification criteria -> IDENTIFIED
        return CustomerIdentificationResult(
            payment_id=payment.payment_id,
            company_id=payment.company_id,
            status=IdentificationStatus.IDENTIFIED,
            primary_candidate=top_candidate,
            candidates=ranked_candidates,
            evidence_signals=all_signals,
            total_evidence_score=top_score,
            reason_code="DETERMINISTIC_COUNTERPARTY_IDENTIFIED",
            reason_description=f"Customer '{top_candidate.customer_name}' identified with score {top_score:.2f}.",
        )

    def _evaluate_customer_signals(
        self,
        payment: PaymentIntakeContext,
        cust: CustomerLookupContext,
        extracted_vpas: Optional[List[str]] = None,
        extracted_accs: Optional[List[str]] = None,
    ) -> List[EvidenceSignal]:
        """Evaluate evidence signals for a single customer candidate."""
        signals: List[EvidenceSignal] = []

        # 1. Direct Banking Coordinate / Identifier Matches (Weight = 100.0)
        norm_payer_id = self.normalizer.normalize_identifier(payment.payer_raw_identifier)
        norm_bank_acc = self.normalizer.normalize_identifier(payment.bank_account_number)

        if extracted_vpas is None:
            extracted_vpas = [
                self.normalizer.normalize_identifier(v)
                for v in self.normalizer.extract_potential_upi_vpas(payment.narration)
            ]
        if extracted_accs is None:
            extracted_accs = [
                self.normalizer.normalize_identifier(a)
                for a in self.normalizer.extract_potential_bank_accounts(payment.narration)
            ]

        for id_type, id_val in cust.identifiers:
            norm_cust_id = self.normalizer.normalize_identifier(id_val)
            if not norm_cust_id:
                continue

            # Match against payer_raw_identifier
            if norm_payer_id and norm_payer_id == norm_cust_id:
                if id_type == "VIRTUAL_ACCOUNT":
                    ev_type = EvidenceType.EXACT_VIRTUAL_ACCOUNT
                elif "@" in id_val or id_type == "UPI_VPA":
                    ev_type = EvidenceType.EXACT_UPI_VPA
                else:
                    ev_type = EvidenceType.EXACT_BANK_ACCOUNT

                signals.append(
                    EvidenceSignal(
                        evidence_type=ev_type,
                        signal_strength=SignalStrength.STRONG,
                        matched_value=id_val,
                        source_field="payer_raw_identifier",
                        weight=100.0,
                        confidence_delta=100.0,
                        metadata={"identifier_type": id_type},
                    )
                )

            # Match against bank_account_number
            elif norm_bank_acc and norm_bank_acc == norm_cust_id:
                ev_type = (
                    EvidenceType.EXACT_VIRTUAL_ACCOUNT
                    if id_type == "VIRTUAL_ACCOUNT"
                    else EvidenceType.EXACT_BANK_ACCOUNT
                )
                signals.append(
                    EvidenceSignal(
                        evidence_type=ev_type,
                        signal_strength=SignalStrength.STRONG,
                        matched_value=id_val,
                        source_field="bank_account_number",
                        weight=100.0,
                        confidence_delta=100.0,
                        metadata={"identifier_type": id_type},
                    )
                )

            # Match against extracted VPAs from narration
            elif extracted_vpas and norm_cust_id in extracted_vpas:
                signals.append(
                    EvidenceSignal(
                        evidence_type=EvidenceType.EXACT_UPI_VPA,
                        signal_strength=SignalStrength.STRONG,
                        matched_value=id_val,
                        source_field="narration_extracted_vpa",
                        weight=95.0,
                        confidence_delta=95.0,
                        metadata={"identifier_type": id_type},
                    )
                )

            # Match against extracted accounts from narration
            elif extracted_accs and norm_cust_id in extracted_accs:
                signals.append(
                    EvidenceSignal(
                        evidence_type=EvidenceType.EXACT_BANK_ACCOUNT,
                        signal_strength=SignalStrength.STRONG,
                        matched_value=id_val,
                        source_field="narration_extracted_account",
                        weight=95.0,
                        confidence_delta=95.0,
                        metadata={"identifier_type": id_type},
                    )
                )

        # 2. Exact Customer Alias Match (Weight = 85.0)
        clean_payer_name = self.normalizer.clean_text(payment.payer_raw_name)
        if clean_payer_name:
            for alias in cust.aliases:
                clean_alias = self.normalizer.clean_text(alias)
                if clean_alias and clean_payer_name == clean_alias:
                    signals.append(
                        EvidenceSignal(
                            evidence_type=EvidenceType.EXACT_ALIAS,
                            signal_strength=SignalStrength.STRONG,
                            matched_value=alias,
                            source_field="payer_raw_name",
                            weight=85.0,
                            confidence_delta=85.0,
                        )
                    )
                    break

        # 3. Normalized Legal Name Match (Weight = 75.0)
        norm_legal_payer = self.normalizer.normalize_legal_name(payment.payer_raw_name)
        norm_legal_cust = self.normalizer.normalize_legal_name(cust.name)
        if norm_legal_payer and norm_legal_cust and norm_legal_payer == norm_legal_cust:
            signals.append(
                EvidenceSignal(
                    evidence_type=EvidenceType.NORMALIZED_LEGAL_NAME,
                    signal_strength=SignalStrength.MEDIUM,
                    matched_value=cust.name,
                    source_field="payer_raw_name",
                    weight=75.0,
                    confidence_delta=75.0,
                )
            )

        # 4. Payer Raw Name Clean Match (Weight = 70.0, if not already captured by legal name)
        clean_cust_name = self.normalizer.clean_text(cust.name)
        if (
            clean_payer_name
            and clean_cust_name
            and clean_payer_name == clean_cust_name
            and not any(s.evidence_type == EvidenceType.NORMALIZED_LEGAL_NAME for s in signals)
        ):
            signals.append(
                EvidenceSignal(
                    evidence_type=EvidenceType.PAYER_RAW_NAME_EXACT,
                    signal_strength=SignalStrength.MEDIUM,
                    matched_value=cust.name,
                    source_field="payer_raw_name",
                    weight=70.0,
                    confidence_delta=70.0,
                )
            )

        # 5. Tax ID / Reference Match (Weight = 50.0)
        clean_ref = self.normalizer.normalize_identifier(payment.payment_reference)
        clean_tax = self.normalizer.normalize_identifier(cust.tax_id)
        invalid_tax_placeholders = {"NA", "NONE", "PENDING", "NULL", "N/A", "NIL", "NOTAPPLICABLE"}
        if (
            clean_ref
            and clean_tax
            and len(clean_tax) >= 5
            and clean_tax.upper() not in invalid_tax_placeholders
            and clean_ref == clean_tax
        ):
            signals.append(
                EvidenceSignal(
                    evidence_type=EvidenceType.REFERENCE_MATCH,
                    signal_strength=SignalStrength.MEDIUM,
                    matched_value=cust.tax_id or "",
                    source_field="payment_reference",
                    weight=50.0,
                    confidence_delta=50.0,
                    metadata={"match_category": "TAX_ID_REFERENCE_MATCH"},
                )
            )

        # 6. Narration Token Overlap (Weight up to 45.0)
        # Only evaluate token overlap if no exact coordinate, alias, or name match was already established
        has_exact_or_coord = any(
            s.evidence_type
            in (
                EvidenceType.EXACT_BANK_ACCOUNT,
                EvidenceType.EXACT_UPI_VPA,
                EvidenceType.EXACT_VIRTUAL_ACCOUNT,
                EvidenceType.EXACT_ALIAS,
                EvidenceType.NORMALIZED_LEGAL_NAME,
                EvidenceType.PAYER_RAW_NAME_EXACT,
            )
            for s in signals
        )
        if not has_exact_or_coord:
            token_signal = self._evaluate_token_overlap(payment, cust)
            if token_signal:
                signals.append(token_signal)

        return signals

    def _evaluate_token_overlap(
        self,
        payment: PaymentIntakeContext,
        cust: CustomerLookupContext,
    ) -> Optional[EvidenceSignal]:
        """Compute token overlap between payment narration/payer name and customer name/aliases."""
        payment_text = f"{payment.payer_raw_name or ''} {payment.narration or ''}"
        payment_tokens = self.normalizer.extract_tokens(payment_text, min_len=3)
        if not payment_tokens:
            return None

        cust_text = f"{cust.name} {' '.join(cust.aliases)}"
        cust_tokens = self.normalizer.extract_tokens(cust_text, min_len=3)
        if not cust_tokens:
            return None

        matched_tokens = payment_tokens.intersection(cust_tokens)
        if not matched_tokens:
            return None

        qualifies = len(matched_tokens) >= 2 or any(len(t) >= 5 for t in matched_tokens)
        if not qualifies:
            return None

        overlap_ratio = len(matched_tokens) / len(cust_tokens)
        if overlap_ratio < 0.30:
            return None

        overlap_score = min(45.0, 20.0 + (25.0 * overlap_ratio))
        return EvidenceSignal(
            evidence_type=EvidenceType.NARRATION_TOKEN_OVERLAP,
            signal_strength=SignalStrength.WEAK,
            matched_value=" ".join(sorted(matched_tokens)),
            source_field="narration",
            weight=round(overlap_score, 2),
            confidence_delta=round(overlap_score, 2),
            metadata={
                "matched_tokens": sorted(matched_tokens),
                "overlap_ratio": round(overlap_ratio, 2),
            },
        )

    def _compute_composite_score(self, signals: List[EvidenceSignal]) -> float:
        """Compute candidate composite score using primary signal weight plus supporting decay."""
        if not signals:
            return 0.0

        sorted_weights = sorted([s.weight for s in signals], reverse=True)
        primary = sorted_weights[0]
        support_boost = sum(w * 0.05 for w in sorted_weights[1:])
        return min(100.0, round(primary + support_boost, 2))

    def _detect_conflicting_signals(
        self,
        payment: PaymentIntakeContext,
        candidates: List[CustomerMatchCandidate],
    ) -> Optional[CustomerIdentificationResult]:
        """Detect if strong signals point to two different customer candidates."""
        if len(candidates) < 2:
            return None

        strong_types = {
            EvidenceType.EXACT_BANK_ACCOUNT,
            EvidenceType.EXACT_UPI_VPA,
            EvidenceType.EXACT_VIRTUAL_ACCOUNT,
        }

        c0_direct = [s for s in candidates[0].evidence_signals if s.evidence_type in strong_types]
        c1_direct = [s for s in candidates[1].evidence_signals if s.evidence_type in strong_types]

        c1_strong_name = [
            s
            for s in candidates[1].evidence_signals
            if s.evidence_type
            in (
                EvidenceType.EXACT_ALIAS,
                EvidenceType.NORMALIZED_LEGAL_NAME,
                EvidenceType.PAYER_RAW_NAME_EXACT,
            )
        ]

        # Conflict 1: Candidate 0 has direct coordinate and Candidate 1 has direct coordinate
        # BUT if both candidates matched the EXACT SAME coordinate value (e.g. shared bank account),
        # it is a shared account ambiguity, NOT a conflict!
        if c0_direct and c1_direct:
            c0_vals = {self.normalizer.normalize_identifier(s.matched_value) for s in c0_direct}
            c1_vals = {self.normalizer.normalize_identifier(s.matched_value) for s in c1_direct}
            # Only conflict if they matched DIFFERENT coordinate values
            if not c0_vals.intersection(c1_vals):
                all_signals: List[EvidenceSignal] = []
                for c in candidates:
                    all_signals.extend(c.evidence_signals)
                return CustomerIdentificationResult(
                    payment_id=payment.payment_id,
                    company_id=payment.company_id,
                    status=IdentificationStatus.CONFLICTING,
                    primary_candidate=None,
                    candidates=candidates,
                    evidence_signals=all_signals,
                    total_evidence_score=0.0,
                    reason_code="CONFLICTING_DIRECT_IDENTIFIERS",
                    reason_description=(
                        f"Conflicting direct coordinates: '{candidates[0].customer_name}' and "
                        f"'{candidates[1].customer_name}' both matched different direct coordinates."
                    ),
                )

        # Conflict 2: Candidate 0 has direct coordinate but Candidate 1 has explicit name/alias with top score
        if (
            c0_direct
            and c1_strong_name
            and candidates[1].composite_score >= self.IDENTIFICATION_SCORE_THRESHOLD
        ):
            all_signals = []
            for c in candidates:
                all_signals.extend(c.evidence_signals)
            return CustomerIdentificationResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                status=IdentificationStatus.CONFLICTING,
                primary_candidate=None,
                candidates=candidates,
                evidence_signals=all_signals,
                total_evidence_score=0.0,
                reason_code="CONFLICTING_COORDINATE_AND_NAME",
                reason_description=(
                    f"Coordinate belongs to '{candidates[0].customer_name}', but payer name "
                    f"explicitly identifies '{candidates[1].customer_name}'."
                ),
            )

        return None
