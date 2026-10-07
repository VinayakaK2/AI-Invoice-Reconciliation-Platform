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
from app.modules.reconciliation.domain.exact_matching import (
    ExactMatchCriteria,
    ExactMatchReasonCode,
    ExactMatchResult,
    ExactMatchRuleEngine,
    ExactMatchStatus,
)
from app.modules.reconciliation.domain.partial_matching import (
    PartialMatchCriteria,
    PartialMatchReasonCode,
    PartialMatchResult,
    PartialMatchRuleEngine,
    PartialMatchStatus,
)
from app.modules.reconciliation.domain.multi_invoice_matching import (
    MultiInvoiceMatchCriteria,
    MultiInvoiceMatchReasonCode,
    MultiInvoiceMatchResult,
    MultiInvoiceMatchRuleEngine,
    MultiInvoiceMatchStatus,
)
from app.modules.reconciliation.domain.combination_matching import (
    CombinationMatchCriteria,
    CombinationMatchReasonCode,
    CombinationMatchResult,
    CombinationMatchRuleEngine,
    CombinationMatchStatus,
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


class ExactMatchUseCase:
    """Evaluate deterministic 1:1 exact matching between a payment and eligible candidate invoices.

    Guarantees:
    - Zero financial accounting state mutation (Rule 4).
    - Strict fail-closed IDOR security (returns 404 for cross-tenant payment or customer access).
    - Consumes bounded, 11-gate-filtered candidate universe from Phase 14.4.
    - Preserves distinction between unique, no match, and ambiguous exact match.
    - Preserves candidate universe completeness / truncation metadata.
    """

    def __init__(
        self,
        payment_lookup_port: PaymentLookupPort,
        customer_lookup_port: CustomerLookupPort,
        invoice_lookup_port: InvoiceLookupPort,
        filter_candidates_use_case: Optional[FilterCandidateInvoicesUseCase] = None,
        exact_match_rule_engine: Optional[ExactMatchRuleEngine] = None,
    ) -> None:
        self.payment_lookup_port = payment_lookup_port
        self.customer_lookup_port = customer_lookup_port
        self.invoice_lookup_port = invoice_lookup_port
        self.filter_candidates_use_case = (
            filter_candidates_use_case
            or FilterCandidateInvoicesUseCase(
                payment_lookup_port=payment_lookup_port,
                customer_lookup_port=customer_lookup_port,
                invoice_lookup_port=invoice_lookup_port,
            )
        )
        self.exact_match_rule_engine = exact_match_rule_engine or ExactMatchRuleEngine()

    def execute(
        self,
        payment_id: UUID,
        company_id: UUID,
        criteria: Optional[ExactMatchCriteria] = None,
        filter_criteria: Optional[CandidateFilterCriteria] = None,
        override_customer_id: Optional[UUID] = None,
        filtered_universe: Optional[FilteredCandidateUniverse] = None,
    ) -> ExactMatchResult:
        """Fetch payment, obtain filtered candidate universe, and evaluate exact 1:1 match."""
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
            return ExactMatchResult(
                payment_id=payment_id,
                company_id=company_id,
                customer_id=override_customer_id,
                status=ExactMatchStatus.NO_EXACT_MATCH,
                matched_candidate=None,
                competing_candidates=[],
                total_exact_candidates_found=0,
                reason_code=ExactMatchReasonCode.PAYMENT_INELIGIBLE,
                reason_description=f"Payment is not eligible for reconciliation intake: {intake_result.reason_description}",
                is_universe_truncated=False,
                candidate_count_evaluated=0,
            )

        # 3. If pre-computed filtered universe is not provided, evaluate it via Phase 14.4
        if filtered_universe is None:
            filtered_universe = self.filter_candidates_use_case.execute(
                payment_id=payment_id,
                company_id=company_id,
                criteria=filter_criteria,
                override_customer_id=override_customer_id,
            )

        # 4. Evaluate exact matching fact via pure domain engine
        return self.exact_match_rule_engine.evaluate(
            payment=payment_context,
            universe=filtered_universe,
            criteria=criteria,
        )


class BatchExactMatchUseCase:
    """Batch evaluate exact 1:1 matching across multiple unreconciled payments.

    Guarantees:
    - Bounded to MAX_BATCH_SIZE = 100 payments.
    - Zero financial accounting state mutation across all evaluated payments.
    - Strict tenant isolation: all evaluated payments belong to authenticated company_id.
    """

    MAX_BATCH_SIZE = 100

    def __init__(
        self,
        payment_lookup_port: PaymentLookupPort,
        exact_match_use_case: ExactMatchUseCase,
    ) -> None:
        self.payment_lookup_port = payment_lookup_port
        self.exact_match_use_case = exact_match_use_case

    def execute(
        self,
        company_id: UUID,
        payment_ids: Optional[List[UUID]] = None,
        limit: int = 50,
        criteria: Optional[ExactMatchCriteria] = None,
        filter_criteria: Optional[CandidateFilterCriteria] = None,
    ) -> List[ExactMatchResult]:
        """Execute exact match evaluation across payments within tenant boundary."""
        if limit < 1 or limit > self.MAX_BATCH_SIZE:
            raise ValidationError(
                f"Batch limit must be between 1 and {self.MAX_BATCH_SIZE}."
            )

        results: List[ExactMatchResult] = []

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
                    res = self.exact_match_use_case.execute(
                        payment_id=p_id,
                        company_id=company_id,
                        criteria=criteria,
                        filter_criteria=filter_criteria,
                    )
                    results.append(res)
        else:
            payment_contexts = self.payment_lookup_port.list_unreconciled_payment_contexts(
                company_id=company_id,
                limit=limit,
            )
            for payment_context in payment_contexts:
                res = self.exact_match_use_case.execute(
                    payment_id=payment_context.payment_id,
                    company_id=company_id,
                    criteria=criteria,
                    filter_criteria=filter_criteria,
                )
                results.append(res)

        return results


class PartialMatchUseCase:
    """Evaluate deterministic 1:1 partial matching between a payment and eligible candidate invoices.

    Guarantees:
    - Zero financial accounting state mutation (Rule 4).
    - Strict fail-closed IDOR security (returns 404 for cross-tenant payment or customer access).
    - Consumes bounded, 11-gate-filtered candidate universe from Phase 14.4.
    - Preserves distinction between unique, no match, and ambiguous partial match.
    - Preserves candidate universe completeness / truncation metadata.
    - Single invoice scope: does not perform multi-invoice combination matching (deferred to Phase 14.7).
    """

    def __init__(
        self,
        payment_lookup_port: PaymentLookupPort,
        customer_lookup_port: CustomerLookupPort,
        invoice_lookup_port: InvoiceLookupPort,
        filter_candidates_use_case: Optional[FilterCandidateInvoicesUseCase] = None,
        partial_match_rule_engine: Optional[PartialMatchRuleEngine] = None,
    ) -> None:
        self.payment_lookup_port = payment_lookup_port
        self.customer_lookup_port = customer_lookup_port
        self.invoice_lookup_port = invoice_lookup_port
        self.filter_candidates_use_case = (
            filter_candidates_use_case
            or FilterCandidateInvoicesUseCase(
                payment_lookup_port=payment_lookup_port,
                customer_lookup_port=customer_lookup_port,
                invoice_lookup_port=invoice_lookup_port,
            )
        )
        self.partial_match_rule_engine = (
            partial_match_rule_engine or PartialMatchRuleEngine()
        )

    def execute(
        self,
        payment_id: UUID,
        company_id: UUID,
        criteria: Optional[PartialMatchCriteria] = None,
        filter_criteria: Optional[CandidateFilterCriteria] = None,
        override_customer_id: Optional[UUID] = None,
        filtered_universe: Optional[FilteredCandidateUniverse] = None,
    ) -> PartialMatchResult:
        """Fetch payment, obtain filtered candidate universe, and evaluate partial 1:1 match."""
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
            return PartialMatchResult(
                payment_id=payment_id,
                company_id=company_id,
                customer_id=override_customer_id,
                status=PartialMatchStatus.NO_PARTIAL_MATCH,
                matched_candidate=None,
                competing_candidates=[],
                total_partial_candidates_found=0,
                reason_code=PartialMatchReasonCode.PAYMENT_INELIGIBLE,
                reason_description=f"Payment is not eligible for reconciliation intake: {intake_result.reason_description}",
                is_universe_truncated=False,
                candidate_count_evaluated=0,
            )

        # 3. If pre-computed filtered universe is not provided, evaluate it via Phase 14.4
        if filtered_universe is None:
            filtered_universe = self.filter_candidates_use_case.execute(
                payment_id=payment_id,
                company_id=company_id,
                criteria=filter_criteria,
                override_customer_id=override_customer_id,
            )

        # 4. Evaluate partial matching fact via pure domain engine
        return self.partial_match_rule_engine.evaluate(
            payment=payment_context,
            universe=filtered_universe,
            criteria=criteria,
        )


class BatchPartialMatchUseCase:
    """Batch evaluate partial 1:1 matching across multiple unreconciled payments.

    Guarantees:
    - Bounded to MAX_BATCH_SIZE = 100 payments.
    - Zero financial accounting state mutation across all evaluated payments.
    - Strict tenant isolation: all evaluated payments belong to authenticated company_id.
    """

    MAX_BATCH_SIZE = 100

    def __init__(
        self,
        payment_lookup_port: PaymentLookupPort,
        partial_match_use_case: PartialMatchUseCase,
    ) -> None:
        self.payment_lookup_port = payment_lookup_port
        self.partial_match_use_case = partial_match_use_case

    def execute(
        self,
        company_id: UUID,
        payment_ids: Optional[List[UUID]] = None,
        limit: int = 50,
        criteria: Optional[PartialMatchCriteria] = None,
        filter_criteria: Optional[CandidateFilterCriteria] = None,
    ) -> List[PartialMatchResult]:
        """Execute partial match evaluation across payments within tenant boundary."""
        if limit < 1 or limit > self.MAX_BATCH_SIZE:
            raise ValidationError(
                f"Batch limit must be between 1 and {self.MAX_BATCH_SIZE}."
            )

        results: List[PartialMatchResult] = []

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
                    res = self.partial_match_use_case.execute(
                        payment_id=p_id,
                        company_id=company_id,
                        criteria=criteria,
                        filter_criteria=filter_criteria,
                    )
                    results.append(res)
        else:
            payment_contexts = self.payment_lookup_port.list_unreconciled_payment_contexts(
                company_id=company_id,
                limit=limit,
            )
            for payment_context in payment_contexts:
                res = self.partial_match_use_case.execute(
                    payment_id=payment_context.payment_id,
                    company_id=company_id,
                    criteria=criteria,
                    filter_criteria=filter_criteria,
                )
                results.append(res)

        return results


class MultiInvoiceMatchUseCase:
    """Evaluate deterministic multi-invoice matching (1:N) between a payment and candidate invoices.

    Guarantees:
    - Zero financial accounting state mutation (Rule 4).
    - Strict fail-closed IDOR security (returns 404 for cross-tenant payment or customer access).
    - Consumes bounded, 11-gate-filtered candidate universe from Phase 14.4.
    - Preserves distinction between unique, no match, and ambiguous multi-invoice matches.
    - Preserves candidate universe completeness / truncation metadata.
    - Multi-invoice scope: evaluates subsets of 2 <= k <= max_combination_size (default 4).
    """

    def __init__(
        self,
        payment_lookup_port: PaymentLookupPort,
        customer_lookup_port: CustomerLookupPort,
        invoice_lookup_port: InvoiceLookupPort,
        filter_candidates_use_case: Optional[FilterCandidateInvoicesUseCase] = None,
        multi_invoice_match_rule_engine: Optional[MultiInvoiceMatchRuleEngine] = None,
    ) -> None:
        self.payment_lookup_port = payment_lookup_port
        self.customer_lookup_port = customer_lookup_port
        self.invoice_lookup_port = invoice_lookup_port
        self.filter_candidates_use_case = (
            filter_candidates_use_case
            or FilterCandidateInvoicesUseCase(
                payment_lookup_port=payment_lookup_port,
                customer_lookup_port=customer_lookup_port,
                invoice_lookup_port=invoice_lookup_port,
            )
        )
        self.multi_invoice_match_rule_engine = (
            multi_invoice_match_rule_engine or MultiInvoiceMatchRuleEngine()
        )

    def execute(
        self,
        payment_id: UUID,
        company_id: UUID,
        criteria: Optional[MultiInvoiceMatchCriteria] = None,
        filter_criteria: Optional[CandidateFilterCriteria] = None,
        override_customer_id: Optional[UUID] = None,
        filtered_universe: Optional[FilteredCandidateUniverse] = None,
    ) -> MultiInvoiceMatchResult:
        """Fetch payment, obtain filtered candidate universe, and evaluate multi-invoice match."""
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
            return MultiInvoiceMatchResult(
                payment_id=payment_id,
                company_id=company_id,
                customer_id=override_customer_id,
                status=MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH,
                matched_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                reason_code=MultiInvoiceMatchReasonCode.PAYMENT_INELIGIBLE,
                reason_description=f"Payment is not eligible for reconciliation intake: {intake_result.reason_description}",
                is_universe_truncated=False,
                candidate_count_evaluated=0,
            )

        # 3. If pre-computed filtered universe is not provided, evaluate it via Phase 14.4
        if filtered_universe is None:
            filtered_universe = self.filter_candidates_use_case.execute(
                payment_id=payment_id,
                company_id=company_id,
                criteria=filter_criteria,
                override_customer_id=override_customer_id,
            )

        # 4. Evaluate multi-invoice matching fact via pure domain engine
        return self.multi_invoice_match_rule_engine.evaluate(
            payment=payment_context,
            universe=filtered_universe,
            criteria=criteria,
        )


class BatchMultiInvoiceMatchUseCase:
    """Batch evaluate multi-invoice matching (1:N) across multiple unreconciled payments.

    Guarantees:
    - Bounded to MAX_BATCH_SIZE = 100 payments.
    - Zero financial accounting state mutation across all evaluated payments.
    - Strict tenant isolation: all evaluated payments belong to authenticated company_id.
    """

    MAX_BATCH_SIZE = 100

    def __init__(
        self,
        payment_lookup_port: PaymentLookupPort,
        multi_invoice_match_use_case: MultiInvoiceMatchUseCase,
    ) -> None:
        self.payment_lookup_port = payment_lookup_port
        self.multi_invoice_match_use_case = multi_invoice_match_use_case

    def execute(
        self,
        company_id: UUID,
        payment_ids: Optional[List[UUID]] = None,
        limit: int = 50,
        criteria: Optional[MultiInvoiceMatchCriteria] = None,
        filter_criteria: Optional[CandidateFilterCriteria] = None,
    ) -> List[MultiInvoiceMatchResult]:
        """Execute multi-invoice match evaluation across payments within tenant boundary."""
        if limit < 1 or limit > self.MAX_BATCH_SIZE:
            raise ValidationError(
                f"Batch limit must be between 1 and {self.MAX_BATCH_SIZE}."
            )

        results: List[MultiInvoiceMatchResult] = []

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
                    res = self.multi_invoice_match_use_case.execute(
                        payment_id=p_id,
                        company_id=company_id,
                        criteria=criteria,
                        filter_criteria=filter_criteria,
                    )
                    results.append(res)
        else:
            payment_contexts = self.payment_lookup_port.list_unreconciled_payment_contexts(
                company_id=company_id,
                limit=limit,
            )
            for payment_context in payment_contexts:
                res = self.multi_invoice_match_use_case.execute(
                    payment_id=payment_context.payment_id,
                    company_id=company_id,
                    criteria=criteria,
                    filter_criteria=filter_criteria,
                )
                results.append(res)

        return results


class CombinationMatchUseCase:
    """Execute Phase 14.8 Combination Matching for a payment against candidate invoices.

    Guarantees:
    - Pure in-memory fact generation; zero financial accounting state mutation.
    - Strict fail-closed IDOR security (returns 404 for cross-tenant payment or customer access).
    - Consumes bounded, 11-gate-filtered candidate universe from Phase 14.4.
    - Evaluates competing candidate combinations, applying reference prioritization and Rule M-3
      FIFO aging heuristics (prioritizing the oldest outstanding invoices by due_date).
    - Strictly flags REVIEW_REQUIRED whenever competing combinations are resolved via heuristics.
    - Preserves unresolvable ambiguity when combinations have identical aging and evidence.
    """

    def __init__(
        self,
        payment_lookup_port: PaymentLookupPort,
        customer_lookup_port: CustomerLookupPort,
        invoice_lookup_port: InvoiceLookupPort,
        filter_candidates_use_case: Optional[FilterCandidateInvoicesUseCase] = None,
        combination_match_rule_engine: Optional[CombinationMatchRuleEngine] = None,
    ) -> None:
        self.payment_lookup_port = payment_lookup_port
        self.customer_lookup_port = customer_lookup_port
        self.invoice_lookup_port = invoice_lookup_port
        self.filter_candidates_use_case = (
            filter_candidates_use_case
            or FilterCandidateInvoicesUseCase(
                payment_lookup_port=payment_lookup_port,
                customer_lookup_port=customer_lookup_port,
                invoice_lookup_port=invoice_lookup_port,
            )
        )
        self.combination_match_rule_engine = (
            combination_match_rule_engine or CombinationMatchRuleEngine()
        )

    def execute(
        self,
        payment_id: UUID,
        company_id: UUID,
        criteria: Optional[CombinationMatchCriteria] = None,
        filter_criteria: Optional[CandidateFilterCriteria] = None,
        override_customer_id: Optional[UUID] = None,
        filtered_universe: Optional[FilteredCandidateUniverse] = None,
    ) -> CombinationMatchResult:
        """Fetch payment, obtain filtered candidate universe, and evaluate combination match."""
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
            return CombinationMatchResult(
                payment_id=payment_id,
                company_id=company_id,
                customer_id=override_customer_id,
                status=CombinationMatchStatus.NO_COMBINATION_MATCH,
                prioritized_combination=None,
                competing_combinations=[],
                total_combinations_found=0,
                applied_heuristic=None,
                requires_review=False,
                reason_code=CombinationMatchReasonCode.PAYMENT_INELIGIBLE,
                reason_description=f"Payment is not eligible for reconciliation intake: {intake_result.reason_description}",
                is_universe_truncated=False,
                candidate_count_evaluated=0,
            )

        # 3. If pre-computed filtered universe is not provided, evaluate it via Phase 14.4
        if filtered_universe is None:
            filtered_universe = self.filter_candidates_use_case.execute(
                payment_id=payment_id,
                company_id=company_id,
                criteria=filter_criteria,
                override_customer_id=override_customer_id,
            )

        # 4. Evaluate combination matching fact via pure domain engine
        return self.combination_match_rule_engine.evaluate(
            payment=payment_context,
            universe=filtered_universe,
            criteria=criteria,
        )


class BatchCombinationMatchUseCase:
    """Batch evaluate combination matching across multiple unreconciled payments.

    Guarantees:
    - Bounded to MAX_BATCH_SIZE = 100 payments.
    - Zero financial accounting state mutation across all evaluated payments.
    - Strict tenant isolation: all evaluated payments belong to authenticated company_id.
    """

    MAX_BATCH_SIZE = 100

    def __init__(
        self,
        payment_lookup_port: PaymentLookupPort,
        combination_match_use_case: CombinationMatchUseCase,
    ) -> None:
        self.payment_lookup_port = payment_lookup_port
        self.combination_match_use_case = combination_match_use_case

    def execute(
        self,
        company_id: UUID,
        payment_ids: Optional[List[UUID]] = None,
        limit: int = 50,
        criteria: Optional[CombinationMatchCriteria] = None,
        filter_criteria: Optional[CandidateFilterCriteria] = None,
    ) -> List[CombinationMatchResult]:
        """Execute combination match evaluation across payments within tenant boundary."""
        if limit < 1 or limit > self.MAX_BATCH_SIZE:
            raise ValidationError(
                f"Batch limit must be between 1 and {self.MAX_BATCH_SIZE}."
            )

        results: List[CombinationMatchResult] = []

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
                    res = self.combination_match_use_case.execute(
                        payment_id=p_id,
                        company_id=company_id,
                        criteria=criteria,
                        filter_criteria=filter_criteria,
                    )
                    results.append(res)
        else:
            payment_contexts = self.payment_lookup_port.list_unreconciled_payment_contexts(
                company_id=company_id,
                limit=limit,
            )
            for payment_context in payment_contexts:
                res = self.combination_match_use_case.execute(
                    payment_id=payment_context.payment_id,
                    company_id=company_id,
                    criteria=criteria,
                    filter_criteria=filter_criteria,
                )
                results.append(res)

        return results


