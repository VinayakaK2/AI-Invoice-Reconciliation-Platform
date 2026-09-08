"""Deterministic rule engine for candidate invoice evaluation, ranking, and bounding.

Phase 13.2 evaluates eligible open invoices for an identified customer against an incoming
payment, calculating structured evidence signals and deterministic retrieval priority.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import re
from typing import List, Optional
from uuid import UUID

from app.modules.reconciliation.domain.candidate_invoices import (
    CandidateInvoice,
    CandidateInvoiceEvidenceSignal,
    CandidateInvoiceUniverse,
    InvoiceEvidenceType,
)
from app.modules.reconciliation.domain.entities import SignalStrength
from app.modules.reconciliation.domain.rules import PaymentIntakeContext


@dataclass(frozen=True)
class InvoiceCandidateContext:
    """Read-only context representation of an open customer invoice."""

    id: UUID
    company_id: UUID
    customer_id: UUID
    invoice_number: str
    issue_date: date
    due_date: date
    total_amount: Decimal
    paid_amount: Decimal
    outstanding_amount: Decimal
    currency: str
    status: str
    is_archived: bool = False


class CandidateInvoiceRuleEngine:
    """Deterministic rule evaluation engine for candidate invoice generation.

    CRITICAL GUARANTEES:
    1. Zero Financial Mutation: strictly read-only evaluation.
    2. Zero LLM Authority: 100% deterministic rules and Decimal arithmetic.
    3. Retrieval Priority != Match Confidence: ranking heuristic purely for presentation/bounding.
    """

    DEFAULT_CANDIDATE_LIMIT = 30
    MAX_CANDIDATE_LIMIT = 100

    def __init__(self) -> None:
        # Regex to extract potential alphanumeric invoice tokens (>= 3 chars)
        self._token_pattern = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9\-_/]{2,}\b")

    def _normalize_invoice_token(self, token: str) -> str:
        """Normalize invoice token by stripping common noise and separators."""
        cleaned = re.sub(r"^(INV|BILL|INVOICE)[-_/:]?", "", token.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"[-_/:]", "", cleaned)
        return cleaned.strip().upper()

    def _check_reference_match(
        self,
        invoice_number: str,
        payment: PaymentIntakeContext,
    ) -> Optional[CandidateInvoiceEvidenceSignal]:
        """Check if invoice number appears in payment narration or reference."""
        search_texts = []
        if payment.narration:
            search_texts.append(("narration", payment.narration))
        if payment.payment_reference:
            search_texts.append(("payment_reference", payment.payment_reference))

        norm_inv = self._normalize_invoice_token(invoice_number)
        raw_inv = invoice_number.strip().upper()

        for source_field, text in search_texts:
            text_upper = text.upper()

            # Direct raw match
            if raw_inv in text_upper:
                return CandidateInvoiceEvidenceSignal(
                    evidence_type=InvoiceEvidenceType.INVOICE_NUMBER_MATCH,
                    signal_strength=SignalStrength.STRONG,
                    matched_value=raw_inv,
                    source_field=source_field,
                    weight=40.0,
                    confidence_delta=40.0,
                    metadata={"match_type": "EXACT_RAW_SUBSTRING"},
                )

            # Tokenized match
            extracted_tokens = self._token_pattern.findall(text_upper)
            for token in extracted_tokens:
                norm_token = self._normalize_invoice_token(token)
                if norm_inv and norm_token and (norm_inv == norm_token or norm_inv == token):
                    return CandidateInvoiceEvidenceSignal(
                        evidence_type=InvoiceEvidenceType.INVOICE_NUMBER_MATCH,
                        signal_strength=SignalStrength.STRONG,
                        matched_value=token,
                        source_field=source_field,
                        weight=40.0,
                        confidence_delta=40.0,
                        metadata={"match_type": "NORMALIZED_TOKEN_MATCH"},
                    )

        return None

    def _evaluate_amount_signals(
        self,
        invoice: InvoiceCandidateContext,
        payment: PaymentIntakeContext,
    ) -> List[CandidateInvoiceEvidenceSignal]:
        """Evaluate exact and partial amount compatibility signals."""
        signals: List[CandidateInvoiceEvidenceSignal] = []
        eval_amount = getattr(payment, "effective_amount", payment.amount)

        # Exact outstanding balance match
        if eval_amount == invoice.outstanding_amount:
            signals.append(
                CandidateInvoiceEvidenceSignal(
                    evidence_type=InvoiceEvidenceType.EXACT_AMOUNT_MATCH,
                    signal_strength=SignalStrength.STRONG,
                    matched_value=f"{eval_amount} == {invoice.outstanding_amount}",
                    source_field="amount",
                    weight=35.0,
                    confidence_delta=35.0,
                    metadata={"payment_amount": str(eval_amount)},
                )
            )
        # Exact original gross total match on a partially paid invoice
        elif (
            eval_amount == invoice.total_amount
            and invoice.paid_amount > Decimal("0.00")
        ):
            signals.append(
                CandidateInvoiceEvidenceSignal(
                    evidence_type=InvoiceEvidenceType.EXACT_ORIGINAL_AMOUNT_MATCH,
                    signal_strength=SignalStrength.MEDIUM,
                    matched_value=f"{eval_amount} == {invoice.total_amount} (paid: {invoice.paid_amount})",
                    source_field="amount",
                    weight=25.0,
                    confidence_delta=25.0,
                    metadata={"original_total": str(invoice.total_amount)},
                )
            )
        # Partial payment compatible
        elif eval_amount < invoice.outstanding_amount:
            signals.append(
                CandidateInvoiceEvidenceSignal(
                    evidence_type=InvoiceEvidenceType.PARTIAL_AMOUNT_COMPATIBLE,
                    signal_strength=SignalStrength.MEDIUM,
                    matched_value=f"{eval_amount} < {invoice.outstanding_amount}",
                    source_field="amount",
                    weight=15.0,
                    confidence_delta=15.0,
                    metadata={"remaining_after": str(invoice.outstanding_amount - eval_amount)},
                )
            )

        return signals

    def _evaluate_date_relevance(
        self,
        invoice: InvoiceCandidateContext,
        payment: PaymentIntakeContext,
    ) -> Optional[CandidateInvoiceEvidenceSignal]:
        """Evaluate temporal causality and due date proximity."""
        # Check causality: payment on or after invoice issue date
        is_causal = invoice.issue_date <= payment.payment_date
        causality_weight = 10.0 if is_causal else 0.0

        # Due date proximity
        days_to_due = (payment.payment_date - invoice.due_date).days
        abs_days = abs(days_to_due)

        if abs_days <= 7:
            proximity_weight = 10.0
            strength = SignalStrength.STRONG
        elif abs_days <= 30:
            proximity_weight = 5.0
            strength = SignalStrength.MEDIUM
        else:
            proximity_weight = 2.0
            strength = SignalStrength.WEAK

        total_weight = min(20.0, causality_weight + proximity_weight)

        return CandidateInvoiceEvidenceSignal(
            evidence_type=InvoiceEvidenceType.DATE_RELEVANCE,
            signal_strength=strength,
            matched_value=f"days_to_due: {days_to_due}, causal: {is_causal}",
            source_field="payment_date",
            weight=total_weight,
            confidence_delta=total_weight,
            metadata={
                "issue_date": invoice.issue_date.isoformat(),
                "due_date": invoice.due_date.isoformat(),
                "payment_date": payment.payment_date.isoformat(),
                "days_to_due": days_to_due,
                "is_causal": is_causal,
            },
        )

    def evaluate_candidate(
        self,
        invoice: InvoiceCandidateContext,
        payment: PaymentIntakeContext,
    ) -> CandidateInvoice:
        """Evaluate an individual invoice against payment context to produce CandidateInvoice."""
        signals: List[CandidateInvoiceEvidenceSignal] = []

        # 1. Reference / Invoice Number Match
        ref_signal = self._check_reference_match(invoice.invoice_number, payment)
        if ref_signal:
            signals.append(ref_signal)
        is_reference_match = ref_signal is not None

        # 2. Amount Signals
        amt_signals = self._evaluate_amount_signals(invoice, payment)
        signals.extend(amt_signals)
        is_exact = any(s.evidence_type == InvoiceEvidenceType.EXACT_AMOUNT_MATCH for s in amt_signals)
        is_partial = any(s.evidence_type == InvoiceEvidenceType.PARTIAL_AMOUNT_COMPATIBLE for s in amt_signals)

        # 3. Date Relevance
        date_signal = self._evaluate_date_relevance(invoice, payment)
        if date_signal:
            signals.append(date_signal)

        # 4. Compute retrieval priority with micro-aging tie-breaker
        base_priority = sum(s.weight for s in signals)
        days_overdue = max(0, (payment.payment_date - invoice.due_date).days)
        micro_aging = min(0.99, days_overdue / 1000.0)

        retrieval_priority = min(100.0, base_priority + micro_aging)

        return CandidateInvoice(
            invoice_id=invoice.id,
            invoice_number=invoice.invoice_number,
            total_amount=invoice.total_amount,
            paid_amount=invoice.paid_amount,
            outstanding_amount=invoice.outstanding_amount,
            currency=invoice.currency,
            issue_date=invoice.issue_date,
            due_date=invoice.due_date,
            status=invoice.status,
            retrieval_priority=retrieval_priority,
            evidence_signals=signals,
            is_exact_amount_match=is_exact,
            is_partial_amount_match=is_partial,
            is_reference_match=is_reference_match,
        )

    def generate_universe(
        self,
        payment: PaymentIntakeContext,
        invoices: List[InvoiceCandidateContext],
        customer_id: Optional[UUID],
        limit: int = DEFAULT_CANDIDATE_LIMIT,
        currency_mismatches_detected: int = 0,
    ) -> CandidateInvoiceUniverse:
        """Evaluate, rank, sort, and bound candidate invoices into a CandidateInvoiceUniverse."""
        clamped_limit = max(1, min(limit, self.MAX_CANDIDATE_LIMIT))

        # Filter strictly eligible invoices:
        # PENDING or PARTIALLY_PAID, unarchived, outstanding > 0, currency == payment.currency
        eligible_invoices = [
            inv for inv in invoices
            if inv.status in ("PENDING", "PARTIALLY_PAID")
            and not inv.is_archived
            and inv.outstanding_amount > Decimal("0.00")
            and inv.currency == payment.currency
        ]

        # Evaluate all eligible invoices
        evaluated_candidates = [
            self.evaluate_candidate(inv, payment)
            for inv in eligible_invoices
        ]

        # 5-Key Deterministic Sorting:
        # 1. retrieval_priority DESC
        # 2. is_exact_amount_match DESC
        # 3. is_reference_match DESC
        # 4. due_date ASC (FIFO preference)
        # 5. invoice_id ASC (Canonical tie-breaker)
        evaluated_candidates.sort(
            key=lambda c: (
                c.retrieval_priority,
                c.is_exact_amount_match,
                c.is_reference_match,
                -c.due_date.toordinal(),
                -c.invoice_id.int,
            ),
            reverse=True,
        )

        total_eligible = len(evaluated_candidates)
        truncated = total_eligible > clamped_limit
        truncation_reason: Optional[str] = None

        if truncated:
            truncation_reason = (
                f"Customer has {total_eligible} eligible open invoices. "
                f"Truncated to top {clamped_limit} by deterministic retrieval priority."
            )
            final_candidates = evaluated_candidates[:clamped_limit]
        else:
            final_candidates = evaluated_candidates

        # Re-assign sequential rank on bounded candidates
        ranked_candidates = [
            CandidateInvoice(
                invoice_id=c.invoice_id,
                invoice_number=c.invoice_number,
                total_amount=c.total_amount,
                paid_amount=c.paid_amount,
                outstanding_amount=c.outstanding_amount,
                currency=c.currency,
                issue_date=c.issue_date,
                due_date=c.due_date,
                status=c.status,
                retrieval_priority=c.retrieval_priority,
                evidence_signals=c.evidence_signals,
                is_exact_amount_match=c.is_exact_amount_match,
                is_partial_amount_match=c.is_partial_amount_match,
                is_reference_match=c.is_reference_match,
                rank=idx + 1,
            )
            for idx, c in enumerate(final_candidates)
        ]

        status_code = "SUCCESS"
        if not ranked_candidates:
            if currency_mismatches_detected > 0:
                status_code = "CURRENCY_MISMATCH"
                truncation_reason = (
                    f"Customer has {currency_mismatches_detected} open invoices, but none match "
                    f"payment currency '{payment.currency}'."
                )
            else:
                status_code = "NO_ELIGIBLE_INVOICES"

        return CandidateInvoiceUniverse(
            payment_id=payment.payment_id,
            company_id=payment.company_id,
            customer_id=customer_id,
            candidates=ranked_candidates,
            total_eligible_invoices=total_eligible,
            truncated=truncated,
            candidate_limit=clamped_limit,
            truncation_reason=truncation_reason,
            currency_mismatches_detected=currency_mismatches_detected,
            status_code=status_code,
            is_deterministic=True,
        )
