"""Reconciliation application use cases for counterparty identification."""

from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from app.modules.reconciliation.application.ports import (
    CustomerLookupPort,
    InvoiceLookupPort,
    PaymentLookupPort,
)
from app.modules.reconciliation.domain.candidate_filters import (
    CandidateFilterCriteria,
    CandidateFilterRuleEngine,
    FilteredCandidateUniverse,
)
from app.modules.reconciliation.domain.candidate_invoices import (
    CandidateInvoiceUniverse,
)
from app.modules.reconciliation.domain.entities import (
    CustomerIdentificationResult,
    IdentificationStatus,
)
from app.modules.reconciliation.domain.invoice_rules import (
    CandidateInvoiceRuleEngine,
)
from app.modules.reconciliation.domain.payment_intake import (
    PaymentIntakeReasonCode,
    PaymentIntakeResult,
    PaymentIntakeRuleEngine,
    PaymentIntakeStatus,
)
from app.modules.reconciliation.domain.rules import PayerIdentificationRuleEngine
from app.shared.exceptions import NotFoundError, ValidationError


class IdentifyPaymentCustomerUseCase:
    """Evaluate counterparty payer identification for a single payment candidate."""

    def __init__(
        self,
        payment_lookup_port: PaymentLookupPort,
        customer_lookup_port: CustomerLookupPort,
        rule_engine: Optional[PayerIdentificationRuleEngine] = None,
    ) -> None:
        self.payment_lookup_port = payment_lookup_port
        self.customer_lookup_port = customer_lookup_port
        self.rule_engine = rule_engine or PayerIdentificationRuleEngine()

    def execute(self, payment_id: UUID, company_id: UUID) -> CustomerIdentificationResult:
        """Fetch payment, verify tenant boundary, evaluate customers, and return result."""
        payment_context = self.payment_lookup_port.get_payment_intake_context(
            payment_id=payment_id,
            company_id=company_id,
        )
        if not payment_context:
            # Fail-closed IDOR protection: return 404
            raise NotFoundError(entity_name="Payment", entity_id=payment_id)

        active_customers = self.customer_lookup_port.get_active_customer_contexts(
            company_id=company_id
        )

        return self.rule_engine.evaluate(
            payment=payment_context,
            customers=active_customers,
        )


class BatchIdentifyPaymentCustomersUseCase:
    """Evaluate counterparty payer identification across multiple payment candidates."""

    MAX_BATCH_SIZE = 100

    def __init__(
        self,
        payment_lookup_port: PaymentLookupPort,
        customer_lookup_port: CustomerLookupPort,
        rule_engine: Optional[PayerIdentificationRuleEngine] = None,
    ) -> None:
        self.payment_lookup_port = payment_lookup_port
        self.customer_lookup_port = customer_lookup_port
        self.rule_engine = rule_engine or PayerIdentificationRuleEngine()

    def execute(
        self,
        company_id: UUID,
        payment_ids: Optional[List[UUID]] = None,
        limit: int = 50,
    ) -> List[CustomerIdentificationResult]:
        """Execute counterparty identification across unreconciled payments within tenant boundary."""
        if limit > self.MAX_BATCH_SIZE:
            raise ValidationError(
                f"Batch size {limit} exceeds maximum allowed limit of {self.MAX_BATCH_SIZE}."
            )

        # Pre-fetch active customers once for the tenant
        active_customers = self.customer_lookup_port.get_active_customer_contexts(
            company_id=company_id
        )

        results: List[CustomerIdentificationResult] = []

        if payment_ids:
            if len(payment_ids) > self.MAX_BATCH_SIZE:
                raise ValidationError(
                    f"Requested {len(payment_ids)} payments exceeds maximum batch limit of {self.MAX_BATCH_SIZE}."
                )
            for p_id in payment_ids:
                payment_context = self.payment_lookup_port.get_payment_intake_context(
                    payment_id=p_id,
                    company_id=company_id,
                )
                if payment_context:
                    res = self.rule_engine.evaluate(payment_context, active_customers)
                    results.append(res)
        else:
            payment_contexts = self.payment_lookup_port.list_unreconciled_payment_contexts(
                company_id=company_id,
                limit=limit,
            )
            for payment_context in payment_contexts:
                res = self.rule_engine.evaluate(payment_context, active_customers)
                results.append(res)

        return results


class IntakePaymentUseCase:
    """Evaluate reconciliation intake eligibility for a single payment candidate.

    Guarantees:
    - Zero financial accounting state mutation (Rule 4).
    - Strict fail-closed IDOR security (returns 404 for cross-tenant access).
    - Deterministic 4-tier classification with diagnostic reason codes.
    """

    def __init__(
        self,
        payment_lookup_port: PaymentLookupPort,
        rule_engine: Optional[PaymentIntakeRuleEngine] = None,
    ) -> None:
        self.payment_lookup_port = payment_lookup_port
        self.rule_engine = rule_engine or PaymentIntakeRuleEngine()

    def execute(self, payment_id: UUID, company_id: UUID) -> PaymentIntakeResult:
        """Fetch payment, verify tenant boundary, evaluate intake, and return result."""
        payment_context = self.payment_lookup_port.get_payment_intake_context(
            payment_id=payment_id,
            company_id=company_id,
        )
        if not payment_context:
            raise NotFoundError(entity_name="Payment", entity_id=payment_id)

        return self.rule_engine.evaluate(payment_context)


class BatchIntakePaymentUseCase:
    """Evaluate reconciliation intake eligibility across multiple payment candidates.

    Guarantees:
    - Batch evaluation bounded to MAX_BATCH_SIZE = 100 payments.
    - Zero financial state mutation across all evaluated payments.
    - Deterministic classification and structured diagnostic output.
    """

    MAX_BATCH_SIZE = 100

    def __init__(
        self,
        payment_lookup_port: PaymentLookupPort,
        rule_engine: Optional[PaymentIntakeRuleEngine] = None,
    ) -> None:
        self.payment_lookup_port = payment_lookup_port
        self.rule_engine = rule_engine or PaymentIntakeRuleEngine()

    def execute(
        self,
        company_id: UUID,
        payment_ids: Optional[List[UUID]] = None,
        limit: int = 50,
    ) -> List[PaymentIntakeResult]:
        """Batch evaluate payment intake contexts for tenant."""
        if limit < 1 or limit > self.MAX_BATCH_SIZE:
            raise ValidationError(
                f"Batch limit must be between 1 and {self.MAX_BATCH_SIZE}."
            )

        results: List[PaymentIntakeResult] = []

        if payment_ids:
            if len(payment_ids) > self.MAX_BATCH_SIZE:
                raise ValidationError(
                    f"Requested {len(payment_ids)} payments exceeds maximum batch limit of {self.MAX_BATCH_SIZE}."
                )
            for p_id in payment_ids:
                payment_context = self.payment_lookup_port.get_payment_intake_context(
                    payment_id=p_id,
                    company_id=company_id,
                )
                if payment_context:
                    res = self.rule_engine.evaluate(payment_context)
                    results.append(res)
        else:
            payment_contexts = self.payment_lookup_port.list_unreconciled_payment_contexts(
                company_id=company_id,
                limit=limit,
            )
            for payment_context in payment_contexts:
                res = self.rule_engine.evaluate(payment_context)
                results.append(res)

        return results


class GenerateCandidateInvoicesUseCase:
    """Evaluate and generate bounded, deterministically ranked candidate invoices for a payment."""

    def __init__(
        self,
        payment_lookup_port: PaymentLookupPort,
        customer_lookup_port: CustomerLookupPort,
        invoice_lookup_port: InvoiceLookupPort,
        customer_rule_engine: Optional[PayerIdentificationRuleEngine] = None,
        invoice_rule_engine: Optional[CandidateInvoiceRuleEngine] = None,
    ) -> None:
        self.payment_lookup_port = payment_lookup_port
        self.customer_lookup_port = customer_lookup_port
        self.invoice_lookup_port = invoice_lookup_port
        self.customer_rule_engine = customer_rule_engine or PayerIdentificationRuleEngine()
        self.invoice_rule_engine = invoice_rule_engine or CandidateInvoiceRuleEngine()

    def execute(
        self,
        payment_id: UUID,
        company_id: UUID,
        limit: int = CandidateInvoiceRuleEngine.DEFAULT_CANDIDATE_LIMIT,
        override_customer_id: Optional[UUID] = None,
    ) -> CandidateInvoiceUniverse:
        """Fetch payment, resolve customer, query open invoices, and generate ranked candidate universe."""
        if limit < 1 or limit > CandidateInvoiceRuleEngine.MAX_CANDIDATE_LIMIT:
            raise ValidationError(
                f"Candidate limit must be between 1 and {CandidateInvoiceRuleEngine.MAX_CANDIDATE_LIMIT}."
            )

        # 1. Fetch payment intake context (Fail-closed 404 IDOR protection)
        payment_context = self.payment_lookup_port.get_payment_intake_context(
            payment_id=payment_id,
            company_id=company_id,
        )
        if not payment_context:
            raise NotFoundError(entity_name="Payment", entity_id=payment_id)

        # 2. Check payment intake eligibility via Phase 14.1 Payment Intake Gateway
        intake_result = PaymentIntakeRuleEngine.evaluate(payment_context)
        if not intake_result.is_eligible:
            return CandidateInvoiceUniverse(
                payment_id=payment_id,
                company_id=company_id,
                customer_id=override_customer_id,
                candidates=[],
                total_eligible_invoices=0,
                truncated=False,
                candidate_limit=limit,
                truncation_reason=intake_result.reason_description,
                status_code="NOT_ELIGIBLE",
            )

        # 3. Resolve Customer Context
        resolved_customer_id: Optional[UUID] = None

        if override_customer_id:
            # Verify explicit customer belongs to authenticated tenant
            cust = self.customer_lookup_port.get_customer_by_id(
                customer_id=override_customer_id,
                company_id=company_id,
            )
            if not cust:
                raise NotFoundError(entity_name="Customer", entity_id=override_customer_id)
            resolved_customer_id = override_customer_id
        else:
            # Deterministic customer identification via Phase 13.1
            active_customers = self.customer_lookup_port.get_active_customer_contexts(
                company_id=company_id
            )
            cust_result = self.customer_rule_engine.evaluate(
                payment=payment_context,
                customers=active_customers,
            )

            if (
                cust_result.status == IdentificationStatus.IDENTIFIED
                and cust_result.primary_candidate
            ):
                resolved_customer_id = cust_result.primary_candidate.customer_id
            else:
                # Customer context cannot be uniquely resolved
                return CandidateInvoiceUniverse(
                    payment_id=payment_id,
                    company_id=company_id,
                    customer_id=None,
                    candidates=[],
                    total_eligible_invoices=0,
                    truncated=False,
                    candidate_limit=limit,
                    truncation_reason=(
                        f"Cannot generate invoice candidates: customer identification returned {cust_result.status.value}. "
                        f"{cust_result.reason_description}"
                    ),
                    status_code="CUSTOMER_UNRESOLVED",
                )

        # 4. Fetch candidate open invoices for customer and currency
        candidate_invoices = self.invoice_lookup_port.get_customer_candidate_invoices(
            company_id=company_id,
            customer_id=resolved_customer_id,
            currency=payment_context.currency,
        )

        # 5. Check for currency mismatch if no invoices match payment currency
        currency_mismatches = 0
        if not candidate_invoices:
            currency_mismatches = (
                self.invoice_lookup_port.count_customer_invoices_in_other_currencies(
                    company_id=company_id,
                    customer_id=resolved_customer_id,
                    payment_currency=payment_context.currency,
                )
            )

        # 6. Generate deterministic candidate universe
        return self.invoice_rule_engine.generate_universe(
            payment=payment_context,
            invoices=candidate_invoices,
            customer_id=resolved_customer_id,
            limit=limit,
            currency_mismatches_detected=currency_mismatches,
        )


class FilterCandidateInvoicesUseCase:
    """Evaluate and produce a bounded, deterministically filtered universe of candidate invoices.

    Guarantees:
    - Candidate Completeness: Never truncates the candidate pool before mandatory filtering gates.
    - Bounded Post-Filter Universe: Slices retained candidates to max_candidates (K <= 30) after filtering.
    - Zero financial accounting state mutation (Rule 4).
    - Strict fail-closed IDOR security (returns 404 for cross-tenant payment or customer access).
    - Machine-readable, deterministically ordered audit trail of excluded candidate invoices.
    """

    def __init__(
        self,
        payment_lookup_port: PaymentLookupPort,
        customer_lookup_port: CustomerLookupPort,
        invoice_lookup_port: InvoiceLookupPort,
        generate_candidates_use_case: Optional[GenerateCandidateInvoicesUseCase] = None,
        filter_rule_engine: Optional[CandidateFilterRuleEngine] = None,
        invoice_rule_engine: Optional[CandidateInvoiceRuleEngine] = None,
    ) -> None:
        self.payment_lookup_port = payment_lookup_port
        self.customer_lookup_port = customer_lookup_port
        self.invoice_lookup_port = invoice_lookup_port
        self.generate_candidates_use_case = (
            generate_candidates_use_case
            or GenerateCandidateInvoicesUseCase(
                payment_lookup_port=payment_lookup_port,
                customer_lookup_port=customer_lookup_port,
                invoice_lookup_port=invoice_lookup_port,
            )
        )
        self.filter_rule_engine = filter_rule_engine or CandidateFilterRuleEngine()
        self.invoice_rule_engine = invoice_rule_engine or CandidateInvoiceRuleEngine()

    def execute(
        self,
        payment_id: UUID,
        company_id: UUID,
        criteria: Optional[CandidateFilterCriteria] = None,
        override_customer_id: Optional[UUID] = None,
        raw_universe: Optional[CandidateInvoiceUniverse] = None,
    ) -> FilteredCandidateUniverse:
        """Fetch payment, evaluate open invoices without premature truncation, and filter deterministically."""
        criteria = criteria or CandidateFilterCriteria()

        # 1. Fetch payment intake context (Fail-closed 404 IDOR protection)
        payment_context = self.payment_lookup_port.get_payment_intake_context(
            payment_id=payment_id,
            company_id=company_id,
        )
        if not payment_context:
            raise NotFoundError(entity_name="Payment", entity_id=payment_id)

        # 2. Check payment intake eligibility via Phase 14.1 Payment Intake Gateway
        intake_result = PaymentIntakeRuleEngine.evaluate(payment_context)
        if not intake_result.is_eligible:
            return FilteredCandidateUniverse(
                payment_id=payment_id,
                company_id=company_id,
                customer_id=override_customer_id,
                retained_candidates=[],
                excluded_candidates=[],
                filter_criteria=criteria,
                total_evaluated=0,
                total_retained=0,
                total_excluded=0,
                currency_mismatches_detected=0,
                exclusion_breakdown={},
                status_code="NOT_ELIGIBLE",
            )

        # 3. Resolve Customer Context
        resolved_customer_id: Optional[UUID] = None
        customer_is_archived = False

        if override_customer_id:
            # Verify explicit customer belongs to authenticated tenant
            cust = self.customer_lookup_port.get_customer_by_id(
                customer_id=override_customer_id,
                company_id=company_id,
            )
            if not cust:
                raise NotFoundError(entity_name="Customer", entity_id=override_customer_id)
            resolved_customer_id = override_customer_id
            customer_is_archived = getattr(cust, "is_archived", False)
        else:
            # Deterministic customer identification via Phase 14.2
            active_customers = self.customer_lookup_port.get_active_customer_contexts(
                company_id=company_id
            )
            cust_result = self.generate_candidates_use_case.customer_rule_engine.evaluate(
                payment=payment_context,
                customers=active_customers,
            )

            if (
                cust_result.status == IdentificationStatus.IDENTIFIED
                and cust_result.primary_candidate
            ):
                resolved_customer_id = cust_result.primary_candidate.customer_id
            else:
                # Customer context cannot be uniquely resolved
                return FilteredCandidateUniverse(
                    payment_id=payment_id,
                    company_id=company_id,
                    customer_id=None,
                    retained_candidates=[],
                    excluded_candidates=[],
                    filter_criteria=criteria,
                    total_evaluated=0,
                    total_retained=0,
                    total_excluded=0,
                    currency_mismatches_detected=0,
                    exclusion_breakdown={},
                    status_code="CUSTOMER_UNRESOLVED",
                )

        effective_amount = getattr(
            payment_context, "effective_amount", payment_context.amount
        )

        # 4. If an in-memory raw_universe was explicitly provided, filter it directly
        if raw_universe is not None:
            return self.filter_rule_engine.filter_universe(
                universe=raw_universe,
                payment_currency=payment_context.currency,
                payment_date=payment_context.payment_date,
                payment_effective_amount=effective_amount,
                criteria=criteria,
                customer_is_archived=customer_is_archived,
            )

        # 5. Authoritative Candidate Retrieval & Evaluation:
        # Fetch all open candidate invoices for customer and currency without pre-filter truncation
        candidate_invoices = self.invoice_lookup_port.get_customer_candidate_invoices(
            company_id=company_id,
            customer_id=resolved_customer_id,
            currency=payment_context.currency,
        )

        currency_mismatches = 0
        if not candidate_invoices:
            currency_mismatches = (
                self.invoice_lookup_port.count_customer_invoices_in_other_currencies(
                    company_id=company_id,
                    customer_id=resolved_customer_id,
                    payment_currency=payment_context.currency,
                )
            )

        # Evaluate candidate evidence signals for every open candidate invoice
        evaluated_candidates = [
            self.invoice_rule_engine.evaluate_candidate(inv, payment_context)
            for inv in candidate_invoices
        ]

        raw_universe = CandidateInvoiceUniverse(
            payment_id=payment_id,
            company_id=company_id,
            customer_id=resolved_customer_id,
            candidates=evaluated_candidates,
            total_eligible_invoices=len(evaluated_candidates),
            truncated=False,
            candidate_limit=len(evaluated_candidates) or 1,
            truncation_reason=None,
            currency_mismatches_detected=currency_mismatches,
            status_code="CURRENCY_MISMATCH" if currency_mismatches > 0 and not evaluated_candidates else (
                "NO_ELIGIBLE_INVOICES" if not evaluated_candidates else "SUCCESS"
            ),
        )

        # 6. Filter universe deterministically (bounding to criteria.max_candidates happens here)
        return self.filter_rule_engine.filter_universe(
            universe=raw_universe,
            payment_currency=payment_context.currency,
            payment_date=payment_context.payment_date,
            payment_effective_amount=effective_amount,
            criteria=criteria,
            customer_is_archived=customer_is_archived,
        )


