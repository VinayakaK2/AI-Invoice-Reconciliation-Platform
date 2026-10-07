"""Reconciliation engine module presentation router."""

from typing import Any, Dict, List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.auth.domain.entities import User
from app.modules.auth.presentation.dependencies import get_current_user
from app.modules.reconciliation.application.use_cases import (
    BatchExactMatchUseCase,
    BatchIdentifyPaymentCustomersUseCase,
    BatchIntakePaymentUseCase,
    BatchMultiInvoiceMatchUseCase,
    BatchPartialMatchUseCase,
    ExactMatchUseCase,
    FilterCandidateInvoicesUseCase,
    GenerateCandidateInvoicesUseCase,
    IdentifyPaymentCustomerUseCase,
    IntakePaymentUseCase,
    MultiInvoiceMatchUseCase,
    PartialMatchUseCase,
)
from app.modules.reconciliation.domain.candidate_filters import (
    CandidateFilterCriteria,
    ExcludedCandidateInvoice,
    FilteredCandidateUniverse,
)
from app.modules.reconciliation.domain.candidate_invoices import (
    CandidateInvoice,
    CandidateInvoiceUniverse,
)
from app.modules.reconciliation.domain.entities import (
    CustomerIdentificationResult,
    CustomerMatchCandidate,
    EvidenceSignal,
)
from app.modules.reconciliation.domain.exact_matching import (
    ExactMatchCriteria,
    ExactMatchHypothesis,
    ExactMatchResult,
    ExactMatchStatus,
)
from app.modules.reconciliation.domain.partial_matching import (
    PartialMatchCriteria,
    PartialMatchHypothesis,
    PartialMatchResult,
    PartialMatchStatus,
)
from app.modules.reconciliation.domain.multi_invoice_matching import (
    MultiInvoiceMatchCriteria,
    MultiInvoiceMatchHypothesis,
    MultiInvoiceMatchResult,
    MultiInvoiceMatchStatus,
)
from app.modules.reconciliation.domain.invoice_rules import CandidateInvoiceRuleEngine
from app.modules.reconciliation.domain.payment_intake import PaymentIntakeResult
from app.modules.reconciliation.infrastructure.adapters import (
    SQLAlchemyCustomerLookupAdapter,
    SQLAlchemyInvoiceLookupAdapter,
    SQLAlchemyPaymentLookupAdapter,
)
from app.modules.reconciliation.presentation.schemas import (
    BatchCustomerIdentificationRequest,
    BatchCustomerIdentificationResponse,
    BatchExactMatchRequest,
    BatchExactMatchResponse,
    BatchMultiInvoiceMatchRequest,
    BatchMultiInvoiceMatchResponse,
    BatchPartialMatchRequest,
    BatchPartialMatchResponse,
    BatchPaymentIntakeRequest,
    BatchPaymentIntakeResponse,
    CandidateFilterCriteriaRequest,
    CandidateInvoiceEvidenceResponse,
    CandidateInvoiceResponse,
    CandidateInvoiceUniverseResponse,
    CustomerCandidateResponse,
    CustomerIdentificationResponse,
    EvidenceSignalResponse,
    ExactMatchCriteriaRequest,
    ExactMatchEvidenceSignalResponse,
    ExactMatchHypothesisResponse,
    ExactMatchRequest,
    ExactMatchResponse,
    ExcludedCandidateResponse,
    FilteredCandidateUniverseResponse,
    GenerateCandidateInvoicesRequest,
    MultiInvoiceMatchCriteriaRequest,
    MultiInvoiceMatchEvidenceSignalResponse,
    MultiInvoiceMatchHypothesisResponse,
    MultiInvoiceMatchRequest,
    MultiInvoiceMatchResponse,
    PartialMatchCriteriaRequest,
    PartialMatchEvidenceSignalResponse,
    PartialMatchHypothesisResponse,
    PartialMatchRequest,
    PartialMatchResponse,
    PaymentIntakeResponse,
    mask_bank_account,
    mask_evidence_matched_value,
)

router = APIRouter(prefix="/reconciliation", tags=["Reconciliation"])


def _map_intake_result_to_response(result: PaymentIntakeResult) -> PaymentIntakeResponse:
    """Map domain PaymentIntakeResult to presentation DTO with masked banking coordinates."""
    return PaymentIntakeResponse(
        payment_id=result.payment_id,
        company_id=result.company_id,
        status=result.status.value,
        is_eligible=result.is_eligible,
        reason_code=result.reason_code,
        reason_description=result.reason_description,
        original_amount=str(result.original_amount),
        allocated_amount=str(result.allocated_amount),
        unallocated_amount=str(result.unallocated_amount),
        effective_amount=str(result.effective_amount),
        currency=result.currency,
        payment_date=result.payment_date.isoformat(),
        bank_account_number=mask_bank_account(result.bank_account_number),
        is_deterministic=result.is_deterministic,
        evaluated_at=result.evaluated_at.isoformat(),
    )


def _map_candidate_to_response(candidate: CustomerMatchCandidate) -> CustomerCandidateResponse:
    """Map domain candidate to presentation DTO with masked evidence signals."""
    return CustomerCandidateResponse(
        customer_id=candidate.customer_id,
        customer_name=candidate.customer_name,
        composite_score=round(candidate.composite_score, 2),
        evidence_signals=[
            EvidenceSignalResponse(
                evidence_type=sig.evidence_type.value,
                signal_strength=sig.signal_strength.value,
                matched_value=mask_evidence_matched_value(
                    sig.matched_value, sig.evidence_type.value
                ),
                source_field=sig.source_field,
                weight=sig.weight,
                confidence_delta=sig.confidence_delta,
                metadata=sig.metadata,
            )
            for sig in candidate.evidence_signals
        ],
        rank=candidate.rank,
    )


def _map_result_to_response(
    result: CustomerIdentificationResult,
) -> CustomerIdentificationResponse:
    """Map domain identification result to presentation DTO with masked banking coordinates."""
    masked_signals = [
        EvidenceSignalResponse(
            evidence_type=sig.evidence_type.value,
            signal_strength=sig.signal_strength.value,
            matched_value=mask_evidence_matched_value(
                sig.matched_value, sig.evidence_type.value
            ),
            source_field=sig.source_field,
            weight=sig.weight,
            confidence_delta=sig.confidence_delta,
            metadata=sig.metadata,
        )
        for sig in result.evidence_signals
    ]

    primary_dto = (
        _map_candidate_to_response(result.primary_candidate)
        if result.primary_candidate
        else None
    )

    candidates_dto = [_map_candidate_to_response(c) for c in result.candidates]

    return CustomerIdentificationResponse(
        payment_id=result.payment_id,
        company_id=result.company_id,
        status=result.status.value,
        primary_candidate=primary_dto,
        candidates=candidates_dto,
        evidence_signals=masked_signals,
        total_evidence_score=round(result.total_evidence_score, 2),
        reason_code=result.reason_code,
        reason_description=result.reason_description,
        is_deterministic=result.is_deterministic,
        evaluated_at=result.evaluated_at.isoformat(),
    )


@router.get("/status")
def reconciliation_module_status() -> dict:
    """Return reconciliation module readiness status."""
    return {"module": "reconciliation", "status": "initialized"}


@router.post(
    "/intake/{payment_id}",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def evaluate_payment_intake(
    payment_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Deterministically evaluate reconciliation intake eligibility for a single payment.

    Guarantees:
    - Zero financial accounting state mutation (Rule 4).
    - Fail-closed IDOR security (404 Not Found for cross-tenant access).
    - Returns 4-tier status (ELIGIBLE, ALREADY_PROCESSED, INELIGIBLE, INVALID) and effective amount.
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    use_case = IntakePaymentUseCase(payment_lookup_port=payment_adapter)

    result = use_case.execute(
        payment_id=payment_id,
        company_id=current_user.company_id,
    )

    return {
        "success": True,
        "data": _map_intake_result_to_response(result),
    }


@router.post(
    "/intake-batch",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def batch_evaluate_payment_intake(
    payload: BatchPaymentIntakeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Batch evaluate reconciliation intake eligibility across payments for tenant.

    Bounded to max 100 payments per batch. Evaluates without mutating financial state.
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    use_case = BatchIntakePaymentUseCase(payment_lookup_port=payment_adapter)

    results = use_case.execute(
        company_id=current_user.company_id,
        payment_ids=payload.payment_ids,
        limit=payload.limit,
    )

    dto_results = [_map_intake_result_to_response(r) for r in results]
    eligible_count = sum(1 for r in results if r.is_eligible)

    return {
        "success": True,
        "data": BatchPaymentIntakeResponse(
            results=dto_results,
            total_evaluated=len(dto_results),
            total_eligible=eligible_count,
        ),
    }


@router.post(
    "/identify/{payment_id}",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def identify_payment_customer(
    payment_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Deterministically identify counterparty customer for a specific payment."""
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)

    use_case = IdentifyPaymentCustomerUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
    )

    result = use_case.execute(
        payment_id=payment_id,
        company_id=current_user.company_id,
    )

    return {
        "success": True,
        "data": _map_result_to_response(result),
    }


@router.post(
    "/identify-batch",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def batch_identify_payment_customers(
    payload: BatchCustomerIdentificationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Batch identify counterparty customers across multiple unreconciled payments."""
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)

    use_case = BatchIdentifyPaymentCustomersUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
    )

    results = use_case.execute(
        company_id=current_user.company_id,
        payment_ids=payload.payment_ids,
        limit=payload.limit,
    )

    dto_results = [_map_result_to_response(r) for r in results]

    return {
        "success": True,
        "data": BatchCustomerIdentificationResponse(
            results=dto_results,
            total_evaluated=len(dto_results),
        ),
    }


def _map_candidate_invoice_to_response(
    candidate: CandidateInvoice,
) -> CandidateInvoiceResponse:
    """Map CandidateInvoice domain entity to presentation response schema."""
    return CandidateInvoiceResponse(
        invoice_id=candidate.invoice_id,
        invoice_number=candidate.invoice_number,
        total_amount=str(candidate.total_amount),
        paid_amount=str(candidate.paid_amount),
        outstanding_amount=str(candidate.outstanding_amount),
        currency=candidate.currency,
        issue_date=candidate.issue_date.isoformat(),
        due_date=candidate.due_date.isoformat(),
        status=candidate.status,
        retrieval_priority=round(candidate.retrieval_priority, 2),
        evidence_signals=[
            CandidateInvoiceEvidenceResponse(
                evidence_type=sig.evidence_type.value,
                signal_strength=sig.signal_strength.value,
                matched_value=sig.matched_value,
                source_field=sig.source_field,
                weight=sig.weight,
                confidence_delta=sig.confidence_delta,
                metadata=sig.metadata,
            )
            for sig in candidate.evidence_signals
        ],
        is_exact_amount_match=candidate.is_exact_amount_match,
        is_partial_amount_match=candidate.is_partial_amount_match,
        is_reference_match=candidate.is_reference_match,
        rank=candidate.rank,
    )


def _map_universe_to_response(
    universe: CandidateInvoiceUniverse,
) -> CandidateInvoiceUniverseResponse:
    """Map CandidateInvoiceUniverse domain aggregate to presentation response schema."""
    return CandidateInvoiceUniverseResponse(
        payment_id=universe.payment_id,
        company_id=universe.company_id,
        customer_id=universe.customer_id,
        candidates=[_map_candidate_invoice_to_response(c) for c in universe.candidates],
        total_eligible_invoices=universe.total_eligible_invoices,
        truncated=universe.truncated,
        candidate_limit=universe.candidate_limit,
        truncation_reason=universe.truncation_reason,
        currency_mismatches_detected=universe.currency_mismatches_detected,
        status_code=universe.status_code,
        is_deterministic=universe.is_deterministic,
        evaluated_at=universe.evaluated_at.isoformat(),
    )


@router.post(
    "/candidates/{payment_id}",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def generate_candidate_invoices(
    payment_id: UUID,
    payload: Optional[GenerateCandidateInvoicesRequest] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Generate bounded, deterministically ranked candidate invoices for a payment candidate."""
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db)

    use_case = GenerateCandidateInvoicesUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )

    limit = payload.limit if payload else CandidateInvoiceRuleEngine.DEFAULT_CANDIDATE_LIMIT
    override_customer_id = payload.override_customer_id if payload else None

    universe = use_case.execute(
        payment_id=payment_id,
        company_id=current_user.company_id,
        limit=limit,
        override_customer_id=override_customer_id,
    )

    return {
        "success": True,
        "data": _map_universe_to_response(universe),
    }


def _map_excluded_candidate_to_response(
    excluded: ExcludedCandidateInvoice,
) -> ExcludedCandidateResponse:
    """Map ExcludedCandidateInvoice domain entity to presentation response schema."""
    return ExcludedCandidateResponse(
        invoice_id=excluded.invoice_id,
        invoice_number=excluded.invoice_number,
        outstanding_amount=str(excluded.outstanding_amount),
        currency=excluded.currency,
        issue_date=excluded.issue_date.isoformat(),
        due_date=excluded.due_date.isoformat(),
        exclusion_reasons=[r.value for r in excluded.exclusion_reasons],
        diagnostic_details=excluded.diagnostic_details,
        metadata=excluded.metadata,
    )


def _map_filtered_universe_to_response(
    universe: FilteredCandidateUniverse,
) -> FilteredCandidateUniverseResponse:
    """Map FilteredCandidateUniverse domain aggregate to presentation response schema."""
    criteria_dict = {
        "min_amount": str(universe.filter_criteria.min_amount)
        if universe.filter_criteria.min_amount is not None
        else None,
        "max_amount": str(universe.filter_criteria.max_amount)
        if universe.filter_criteria.max_amount is not None
        else None,
        "max_lookback_days": universe.filter_criteria.max_lookback_days,
        "max_advance_days": universe.filter_criteria.max_advance_days,
        "require_causality": universe.filter_criteria.require_causality,
        "allowed_statuses": sorted(list(universe.filter_criteria.allowed_statuses)),
        "require_reference_match": universe.filter_criteria.require_reference_match,
        "disallow_overpayment": universe.filter_criteria.disallow_overpayment,
        "max_candidates": universe.filter_criteria.max_candidates,
    }

    return FilteredCandidateUniverseResponse(
        payment_id=universe.payment_id,
        company_id=universe.company_id,
        customer_id=universe.customer_id,
        retained_candidates=[
            _map_candidate_invoice_to_response(c) for c in universe.retained_candidates
        ],
        excluded_candidates=[
            _map_excluded_candidate_to_response(e) for e in universe.excluded_candidates
        ],
        filter_criteria=criteria_dict,
        total_evaluated=universe.total_evaluated,
        total_retained=universe.total_retained,
        total_excluded=universe.total_excluded,
        currency_mismatches_detected=universe.currency_mismatches_detected,
        exclusion_breakdown=universe.exclusion_breakdown,
        status_code=universe.status_code,
        is_deterministic=universe.is_deterministic,
        evaluated_at=universe.evaluated_at.isoformat(),
    )


@router.post(
    "/candidates/{payment_id}/filtered",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def filter_candidate_invoices(
    payment_id: UUID,
    payload: Optional[CandidateFilterCriteriaRequest] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Deterministically filter candidate invoices for a payment candidate.

    Guarantees:
    - Zero financial accounting state mutation (Rule 4).
    - Fail-closed IDOR security (404 Not Found for cross-tenant access).
    - Returns structured retained candidates and machine-readable excluded candidates.
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db)

    use_case = FilterCandidateInvoicesUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )

    criteria = None
    override_customer_id = None
    if payload:
        override_customer_id = payload.override_customer_id
        statuses = (
            set(payload.allowed_statuses)
            if payload.allowed_statuses is not None
            else {"PENDING", "PARTIALLY_PAID"}
        )
        criteria = CandidateFilterCriteria(
            min_amount=payload.min_amount,
            max_amount=payload.max_amount,
            max_lookback_days=payload.max_lookback_days,
            max_advance_days=payload.max_advance_days,
            require_causality=payload.require_causality,
            allowed_statuses=statuses,
            require_reference_match=payload.require_reference_match,
            disallow_overpayment=payload.disallow_overpayment,
            max_candidates=payload.max_candidates,
        )

    universe = use_case.execute(
        payment_id=payment_id,
        company_id=current_user.company_id,
        criteria=criteria,
        override_customer_id=override_customer_id,
    )

    return {
        "success": True,
        "data": _map_filtered_universe_to_response(universe),
    }


def _map_exact_match_hypothesis_to_response(
    hypothesis: ExactMatchHypothesis,
) -> ExactMatchHypothesisResponse:
    """Map ExactMatchHypothesis domain entity to presentation response schema."""
    return ExactMatchHypothesisResponse(
        invoice_id=hypothesis.invoice_id,
        invoice_number=hypothesis.invoice_number,
        matched_amount=str(hypothesis.matched_amount),
        invoice_outstanding_before=str(hypothesis.invoice_outstanding_before),
        invoice_outstanding_after=str(hypothesis.invoice_outstanding_after),
        payment_unallocated_before=str(hypothesis.payment_unallocated_before),
        payment_unallocated_after=str(hypothesis.payment_unallocated_after),
        currency=hypothesis.currency,
        match_type=hypothesis.match_type,
        is_reference_match=hypothesis.is_reference_match,
        date_difference_days=hypothesis.date_difference_days,
        evidence_signals=[
            ExactMatchEvidenceSignalResponse(
                evidence_type=sig.evidence_type.value,
                signal_strength=sig.signal_strength.value,
                description=sig.description,
                matched_value=sig.matched_value,
                source_field=sig.source_field,
                weight=sig.weight,
                metadata=sig.metadata,
            )
            for sig in hypothesis.evidence_signals
        ],
    )


def _map_exact_match_result_to_response(
    result: ExactMatchResult,
) -> ExactMatchResponse:
    """Map ExactMatchResult domain aggregate to presentation response schema."""
    matched_dto = (
        _map_exact_match_hypothesis_to_response(result.matched_candidate)
        if result.matched_candidate
        else None
    )
    competing_dtos = [
        _map_exact_match_hypothesis_to_response(c) for c in result.competing_candidates
    ]

    return ExactMatchResponse(
        payment_id=result.payment_id,
        company_id=result.company_id,
        customer_id=result.customer_id,
        status=result.status.value,
        matched_candidate=matched_dto,
        competing_candidates=competing_dtos,
        total_exact_candidates_found=result.total_exact_candidates_found,
        reason_code=result.reason_code.value,
        reason_description=result.reason_description,
        is_universe_truncated=result.is_universe_truncated,
        candidate_count_evaluated=result.candidate_count_evaluated,
        is_deterministic=result.is_deterministic,
        evaluated_at=result.evaluated_at.isoformat(),
    )


@router.post(
    "/exact-match/{payment_id}",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def evaluate_exact_match(
    payment_id: UUID,
    payload: Optional[ExactMatchRequest] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Deterministically evaluate 1:1 exact matching for a single payment.

    Guarantees:
    - Zero financial accounting state mutation (Rule 4).
    - Fail-closed IDOR security (404 Not Found for cross-tenant payment or customer access).
    - Returns structured match hypothesis and diagnostic reason code.
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db)

    use_case = ExactMatchUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )

    criteria = None
    filter_criteria = None
    override_customer_id = None

    if payload:
        override_customer_id = payload.override_customer_id
        if payload.criteria:
            criteria = ExactMatchCriteria(
                amount_tolerance=payload.criteria.amount_tolerance,
                require_exact_currency=payload.criteria.require_exact_currency,
                date_proximity_days=payload.criteria.date_proximity_days,
            )
        if payload.filter_criteria:
            fc = payload.filter_criteria
            statuses = (
                set(fc.allowed_statuses)
                if fc.allowed_statuses is not None
                else {"PENDING", "PARTIALLY_PAID"}
            )
            filter_criteria = CandidateFilterCriteria(
                min_amount=fc.min_amount,
                max_amount=fc.max_amount,
                max_lookback_days=fc.max_lookback_days,
                max_advance_days=fc.max_advance_days,
                require_causality=fc.require_causality,
                allowed_statuses=statuses,
                require_reference_match=fc.require_reference_match,
                disallow_overpayment=fc.disallow_overpayment,
                max_candidates=fc.max_candidates,
            )

    result = use_case.execute(
        payment_id=payment_id,
        company_id=current_user.company_id,
        criteria=criteria,
        filter_criteria=filter_criteria,
        override_customer_id=override_customer_id,
    )

    return {
        "success": True,
        "data": _map_exact_match_result_to_response(result),
    }


@router.post(
    "/exact-match-batch",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def batch_evaluate_exact_matches(
    payload: BatchExactMatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Batch evaluate 1:1 exact matching across unreconciled payments for tenant.

    Bounded to max 100 payments per batch. Strictly zero financial mutation.
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db)

    exact_match_use_case = ExactMatchUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )
    batch_use_case = BatchExactMatchUseCase(
        payment_lookup_port=payment_adapter,
        exact_match_use_case=exact_match_use_case,
    )

    criteria = None
    filter_criteria = None

    if payload.criteria:
        criteria = ExactMatchCriteria(
            amount_tolerance=payload.criteria.amount_tolerance,
            require_exact_currency=payload.criteria.require_exact_currency,
            date_proximity_days=payload.criteria.date_proximity_days,
        )
    if payload.filter_criteria:
        fc = payload.filter_criteria
        statuses = (
            set(fc.allowed_statuses)
            if fc.allowed_statuses is not None
            else {"PENDING", "PARTIALLY_PAID"}
        )
        filter_criteria = CandidateFilterCriteria(
            min_amount=fc.min_amount,
            max_amount=fc.max_amount,
            max_lookback_days=fc.max_lookback_days,
            max_advance_days=fc.max_advance_days,
            require_causality=fc.require_causality,
            allowed_statuses=statuses,
            require_reference_match=fc.require_reference_match,
            disallow_overpayment=fc.disallow_overpayment,
            max_candidates=fc.max_candidates,
        )

    results = batch_use_case.execute(
        company_id=current_user.company_id,
        payment_ids=payload.payment_ids,
        limit=payload.limit,
        criteria=criteria,
        filter_criteria=filter_criteria,
    )

    dto_results = [_map_exact_match_result_to_response(r) for r in results]
    exact_count = sum(1 for r in results if r.status == ExactMatchStatus.EXACT_MATCH)
    ambiguous_count = sum(
        1 for r in results if r.status == ExactMatchStatus.AMBIGUOUS_EXACT_MATCH
    )
    no_match_count = sum(1 for r in results if r.status == ExactMatchStatus.NO_EXACT_MATCH)

    return {
        "success": True,
        "data": BatchExactMatchResponse(
            results=dto_results,
            total_evaluated=len(dto_results),
            exact_matches_found=exact_count,
            ambiguous_matches_found=ambiguous_count,
            no_matches_found=no_match_count,
        ),
    }


def _map_partial_match_hypothesis_to_response(
    hypothesis: PartialMatchHypothesis,
) -> PartialMatchHypothesisResponse:
    """Map PartialMatchHypothesis domain entity to presentation response schema."""
    return PartialMatchHypothesisResponse(
        invoice_id=hypothesis.invoice_id,
        invoice_number=hypothesis.invoice_number,
        matched_amount=str(hypothesis.matched_amount),
        invoice_outstanding_before=str(hypothesis.invoice_outstanding_before),
        invoice_outstanding_after=str(hypothesis.invoice_outstanding_after),
        payment_unallocated_before=str(hypothesis.payment_unallocated_before),
        payment_unallocated_after=str(hypothesis.payment_unallocated_after),
        currency=hypothesis.currency,
        match_type=hypothesis.match_type,
        is_reference_match=hypothesis.is_reference_match,
        date_difference_days=hypothesis.date_difference_days,
        evidence_signals=[
            PartialMatchEvidenceSignalResponse(
                evidence_type=sig.evidence_type.value,
                signal_strength=sig.signal_strength.value,
                description=sig.description,
                matched_value=sig.matched_value,
                source_field=sig.source_field,
                weight=sig.weight,
                metadata=sig.metadata,
            )
            for sig in hypothesis.evidence_signals
        ],
    )


def _map_partial_match_result_to_response(
    result: PartialMatchResult,
) -> PartialMatchResponse:
    """Map PartialMatchResult domain aggregate to presentation response schema."""
    matched_dto = (
        _map_partial_match_hypothesis_to_response(result.matched_candidate)
        if result.matched_candidate
        else None
    )
    competing_dtos = [
        _map_partial_match_hypothesis_to_response(c) for c in result.competing_candidates
    ]

    return PartialMatchResponse(
        payment_id=result.payment_id,
        company_id=result.company_id,
        customer_id=result.customer_id,
        status=result.status.value,
        matched_candidate=matched_dto,
        competing_candidates=competing_dtos,
        total_partial_candidates_found=result.total_partial_candidates_found,
        reason_code=result.reason_code.value,
        reason_description=result.reason_description,
        is_universe_truncated=result.is_universe_truncated,
        candidate_count_evaluated=result.candidate_count_evaluated,
        is_deterministic=result.is_deterministic,
        evaluated_at=result.evaluated_at.isoformat(),
    )


@router.post(
    "/partial-match/{payment_id}",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def evaluate_partial_match(
    payment_id: UUID,
    payload: Optional[PartialMatchRequest] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Deterministically evaluate 1:1 partial matching for a single payment.

    Guarantees:
    - Zero financial accounting state mutation (Rule 4).
    - Fail-closed IDOR security (404 Not Found for cross-tenant payment or customer access).
    - Returns structured match hypothesis and diagnostic reason code.
    - Single invoice scope: does not perform multi-invoice combination matching (Phase 14.7).
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db)

    use_case = PartialMatchUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )

    criteria = None
    filter_criteria = None
    override_customer_id = None

    if payload:
        override_customer_id = payload.override_customer_id
        if payload.criteria:
            criteria = PartialMatchCriteria(
                amount_tolerance=payload.criteria.amount_tolerance,
                require_exact_currency=payload.criteria.require_exact_currency,
                date_proximity_days=payload.criteria.date_proximity_days,
            )
        if payload.filter_criteria:
            fc = payload.filter_criteria
            statuses = (
                set(fc.allowed_statuses)
                if fc.allowed_statuses is not None
                else {"PENDING", "PARTIALLY_PAID"}
            )
            filter_criteria = CandidateFilterCriteria(
                min_amount=fc.min_amount,
                max_amount=fc.max_amount,
                max_lookback_days=fc.max_lookback_days,
                max_advance_days=fc.max_advance_days,
                require_causality=fc.require_causality,
                allowed_statuses=statuses,
                require_reference_match=fc.require_reference_match,
                disallow_overpayment=fc.disallow_overpayment,
                max_candidates=fc.max_candidates,
            )

    result = use_case.execute(
        payment_id=payment_id,
        company_id=current_user.company_id,
        criteria=criteria,
        filter_criteria=filter_criteria,
        override_customer_id=override_customer_id,
    )

    return {
        "success": True,
        "data": _map_partial_match_result_to_response(result),
    }


@router.post(
    "/partial-match-batch",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def batch_evaluate_partial_matches(
    payload: BatchPartialMatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Batch evaluate 1:1 partial matching across unreconciled payments for tenant.

    Bounded to max 100 payments per batch. Strictly zero financial mutation.
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db)

    partial_match_use_case = PartialMatchUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )
    batch_use_case = BatchPartialMatchUseCase(
        payment_lookup_port=payment_adapter,
        partial_match_use_case=partial_match_use_case,
    )

    criteria = None
    filter_criteria = None

    if payload.criteria:
        criteria = PartialMatchCriteria(
            amount_tolerance=payload.criteria.amount_tolerance,
            require_exact_currency=payload.criteria.require_exact_currency,
            date_proximity_days=payload.criteria.date_proximity_days,
        )
    if payload.filter_criteria:
        fc = payload.filter_criteria
        statuses = (
            set(fc.allowed_statuses)
            if fc.allowed_statuses is not None
            else {"PENDING", "PARTIALLY_PAID"}
        )
        filter_criteria = CandidateFilterCriteria(
            min_amount=fc.min_amount,
            max_amount=fc.max_amount,
            max_lookback_days=fc.max_lookback_days,
            max_advance_days=fc.max_advance_days,
            require_causality=fc.require_causality,
            allowed_statuses=statuses,
            require_reference_match=fc.require_reference_match,
            disallow_overpayment=fc.disallow_overpayment,
            max_candidates=fc.max_candidates,
        )

    results = batch_use_case.execute(
        company_id=current_user.company_id,
        payment_ids=payload.payment_ids,
        limit=payload.limit,
        criteria=criteria,
        filter_criteria=filter_criteria,
    )

    dto_results = [_map_partial_match_result_to_response(r) for r in results]
    partial_count = sum(1 for r in results if r.status == PartialMatchStatus.PARTIAL_MATCH)
    ambiguous_count = sum(
        1 for r in results if r.status == PartialMatchStatus.AMBIGUOUS_PARTIAL_MATCH
    )
    no_match_count = sum(1 for r in results if r.status == PartialMatchStatus.NO_PARTIAL_MATCH)

    return {
        "success": True,
        "data": BatchPartialMatchResponse(
            results=dto_results,
            total_evaluated=len(dto_results),
            partial_matches_found=partial_count,
            ambiguous_matches_found=ambiguous_count,
            no_matches_found=no_match_count,
        ),
    }


def _map_multi_invoice_hypothesis_to_response(
    hypothesis: MultiInvoiceMatchHypothesis,
) -> MultiInvoiceMatchHypothesisResponse:
    """Map MultiInvoiceMatchHypothesis domain entity to presentation response schema."""
    return MultiInvoiceMatchHypothesisResponse(
        invoice_ids=hypothesis.invoice_ids,
        invoice_numbers=hypothesis.invoice_numbers,
        matched_amount=str(hypothesis.matched_amount),
        invoices_outstanding_before=[str(amt) for amt in hypothesis.invoices_outstanding_before],
        invoices_outstanding_after=[str(amt) for amt in hypothesis.invoices_outstanding_after],
        payment_unallocated_before=str(hypothesis.payment_unallocated_before),
        payment_unallocated_after=str(hypothesis.payment_unallocated_after),
        currency=hypothesis.currency,
        match_type=hypothesis.match_type,
        combination_size=hypothesis.combination_size,
        has_reference_match=hypothesis.has_reference_match,
        matched_reference_count=hypothesis.matched_reference_count,
        max_date_difference_days=hypothesis.max_date_difference_days,
        evidence_signals=[
            MultiInvoiceMatchEvidenceSignalResponse(
                evidence_type=sig.evidence_type.value,
                signal_strength=sig.signal_strength.value,
                description=sig.description,
                matched_value=sig.matched_value,
                source_field=sig.source_field,
                weight=sig.weight,
                metadata=sig.metadata,
            )
            for sig in hypothesis.evidence_signals
        ],
    )


def _map_multi_invoice_match_result_to_response(
    result: MultiInvoiceMatchResult,
) -> MultiInvoiceMatchResponse:
    """Map MultiInvoiceMatchResult domain aggregate to presentation response schema."""
    matched_dto = (
        _map_multi_invoice_hypothesis_to_response(result.matched_combination)
        if result.matched_combination
        else None
    )
    competing_dtos = [
        _map_multi_invoice_hypothesis_to_response(c) for c in result.competing_combinations
    ]

    return MultiInvoiceMatchResponse(
        payment_id=result.payment_id,
        company_id=result.company_id,
        customer_id=result.customer_id,
        status=result.status.value,
        matched_combination=matched_dto,
        competing_combinations=competing_dtos,
        total_combinations_found=result.total_combinations_found,
        reason_code=result.reason_code.value,
        reason_description=result.reason_description,
        is_universe_truncated=result.is_universe_truncated,
        candidate_count_evaluated=result.candidate_count_evaluated,
        is_deterministic=result.is_deterministic,
        evaluated_at=result.evaluated_at.isoformat(),
    )


@router.post(
    "/multi-invoice-match/{payment_id}",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def evaluate_multi_invoice_match(
    payment_id: UUID,
    payload: Optional[MultiInvoiceMatchRequest] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Deterministically evaluate multi-invoice matching (1:N) for a single payment.

    Guarantees:
    - Zero financial accounting state mutation (Rule 4).
    - Fail-closed IDOR security (404 Not Found for cross-tenant payment or customer access).
    - Returns structured match hypothesis and diagnostic reason code.
    - Preserves ambiguity when multiple distinct combinations sum to payment amount.
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db)

    use_case = MultiInvoiceMatchUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )

    criteria = None
    filter_criteria = None
    override_customer_id = None

    if payload:
        override_customer_id = payload.override_customer_id
        if payload.criteria:
            criteria = MultiInvoiceMatchCriteria(
                max_combination_size=payload.criteria.max_combination_size,
                amount_tolerance=payload.criteria.amount_tolerance,
                require_exact_currency=payload.criteria.require_exact_currency,
                date_proximity_days=payload.criteria.date_proximity_days,
            )
        if payload.filter_criteria:
            fc = payload.filter_criteria
            statuses = (
                set(fc.allowed_statuses)
                if fc.allowed_statuses is not None
                else {"PENDING", "PARTIALLY_PAID"}
            )
            filter_criteria = CandidateFilterCriteria(
                min_amount=fc.min_amount,
                max_amount=fc.max_amount,
                max_lookback_days=fc.max_lookback_days,
                max_advance_days=fc.max_advance_days,
                require_causality=fc.require_causality,
                allowed_statuses=statuses,
                require_reference_match=fc.require_reference_match,
                disallow_overpayment=fc.disallow_overpayment,
                max_candidates=fc.max_candidates,
            )

    result = use_case.execute(
        payment_id=payment_id,
        company_id=current_user.company_id,
        criteria=criteria,
        filter_criteria=filter_criteria,
        override_customer_id=override_customer_id,
    )

    return {
        "success": True,
        "data": _map_multi_invoice_match_result_to_response(result),
    }


@router.post(
    "/multi-invoice-match-batch",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def batch_evaluate_multi_invoice_matches(
    payload: BatchMultiInvoiceMatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Batch evaluate multi-invoice matching (1:N) across unreconciled payments for tenant.

    Bounded to max 100 payments per batch. Strictly zero financial mutation.
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db)

    multi_invoice_match_use_case = MultiInvoiceMatchUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )
    batch_use_case = BatchMultiInvoiceMatchUseCase(
        payment_lookup_port=payment_adapter,
        multi_invoice_match_use_case=multi_invoice_match_use_case,
    )

    criteria = None
    filter_criteria = None

    if payload.criteria:
        criteria = MultiInvoiceMatchCriteria(
            max_combination_size=payload.criteria.max_combination_size,
            amount_tolerance=payload.criteria.amount_tolerance,
            require_exact_currency=payload.criteria.require_exact_currency,
            date_proximity_days=payload.criteria.date_proximity_days,
        )
    if payload.filter_criteria:
        fc = payload.filter_criteria
        statuses = (
            set(fc.allowed_statuses)
            if fc.allowed_statuses is not None
            else {"PENDING", "PARTIALLY_PAID"}
        )
        filter_criteria = CandidateFilterCriteria(
            min_amount=fc.min_amount,
            max_amount=fc.max_amount,
            max_lookback_days=fc.max_lookback_days,
            max_advance_days=fc.max_advance_days,
            require_causality=fc.require_causality,
            allowed_statuses=statuses,
            require_reference_match=fc.require_reference_match,
            disallow_overpayment=fc.disallow_overpayment,
            max_candidates=fc.max_candidates,
        )

    results = batch_use_case.execute(
        company_id=current_user.company_id,
        payment_ids=payload.payment_ids,
        limit=payload.limit,
        criteria=criteria,
        filter_criteria=filter_criteria,
    )

    dto_results = [_map_multi_invoice_match_result_to_response(r) for r in results]
    multi_count = sum(1 for r in results if r.status == MultiInvoiceMatchStatus.MULTI_INVOICE_MATCH)
    ambiguous_count = sum(
        1 for r in results if r.status == MultiInvoiceMatchStatus.AMBIGUOUS_MULTI_INVOICE_MATCH
    )
    no_match_count = sum(1 for r in results if r.status == MultiInvoiceMatchStatus.NO_MULTI_INVOICE_MATCH)

    return {
        "success": True,
        "data": BatchMultiInvoiceMatchResponse(
            results=dto_results,
            total_evaluated=len(dto_results),
            multi_invoice_matches_found=multi_count,
            ambiguous_matches_found=ambiguous_count,
            no_matches_found=no_match_count,
        ),
    }


