"""Reconciliation engine module presentation router."""

from typing import Any, Dict, List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.auth.domain.entities import User
from app.modules.auth.presentation.dependencies import get_current_user
from app.modules.reconciliation.application.use_cases import (
    BatchCombinationMatchUseCase,
    BatchExactMatchUseCase,
    BatchIdentifyPaymentCustomersUseCase,
    BatchIntakePaymentUseCase,
    BatchMultiInvoiceMatchUseCase,
    BatchPartialMatchUseCase,
    CombinationMatchUseCase,
    ExactMatchUseCase,
    FilterCandidateInvoicesUseCase,
    GenerateCandidateInvoicesUseCase,
    IdentifyPaymentCustomerUseCase,
    IntakePaymentUseCase,
    MultiInvoiceMatchUseCase,
    PartialMatchUseCase,
    EvidenceCollectionUseCase,
    BatchEvidenceCollectionUseCase,
    EvidenceNormalizationUseCase,
    BatchEvidenceNormalizationUseCase,
    MatchingScoringUseCase,
    BatchMatchingScoringUseCase,
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
from app.modules.reconciliation.domain.combination_matching import (
    CombinationHypothesis,
    CombinationMatchCriteria,
    CombinationMatchResult,
    CombinationMatchStatus,
)
from app.modules.reconciliation.domain.invoice_rules import CandidateInvoiceRuleEngine
from app.modules.reconciliation.domain.payment_intake import PaymentIntakeResult
from app.modules.reconciliation.infrastructure.adapters import (
    SQLAlchemyCustomerLookupAdapter,
    SQLAlchemyInvoiceLookupAdapter,
    SQLAlchemyPaymentLookupAdapter,
)
from app.modules.reconciliation.presentation.schemas import (
    BatchCombinationMatchRequest,
    BatchCombinationMatchResponse,
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
    CombinationHypothesisResponse,
    CombinationMatchCriteriaRequest,
    CombinationMatchEvidenceSignalResponse,
    CombinationMatchRequest,
    CombinationMatchResponse,
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
    StructuredEvidenceItemResponse,
    CandidateEvidenceBundleResponse,
    PaymentEvidenceContextResponse,
    EvidenceCollectionRequest,
    EvidenceCollectionResponse,
    RelevantIdentifiersResponse,
    CanonicalEvidenceObjectResponse,
    NormalizedCandidateBundleResponse,
    NormalizedPaymentEvidenceContextResponse,
    EvidenceNormalizationRequest,
    EvidenceNormalizationResponse,
    ScoreContributionResponse,
    CandidateScoreResponse,
    MatchingScoringRequest,
    MatchingScoringResponse,
    BatchMatchingScoringRequest,
    BatchMatchingScoringResponse,
    mask_bank_account,
    mask_evidence_matched_value,
)
from app.modules.reconciliation.domain.matching_scoring import (
    CandidateScoreResult,
    MatchingScoringResult,
    ScoreContribution,
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


# ==============================================================================
# Phase 14.8 Combination Matching Presentation Endpoints & Mappers
# ==============================================================================


def _map_combination_hypothesis_to_response(
    hypothesis: Optional[CombinationHypothesis],
) -> Optional[CombinationHypothesisResponse]:
    """Map domain CombinationHypothesis to presentation response DTO."""
    if not hypothesis:
        return None
    return CombinationHypothesisResponse(
        invoice_ids=hypothesis.invoice_ids,
        invoice_numbers=hypothesis.invoice_numbers,
        matched_amount=str(hypothesis.matched_amount),
        invoices_outstanding_before=[str(amt) for amt in hypothesis.invoices_outstanding_before],
        invoices_outstanding_after=[str(amt) for amt in hypothesis.invoices_outstanding_after],
        payment_unallocated_before=str(hypothesis.payment_unallocated_before),
        payment_unallocated_after=str(hypothesis.payment_unallocated_after),
        currency=hypothesis.currency,
        combination_size=hypothesis.combination_size,
        oldest_due_date=hypothesis.oldest_due_date.isoformat(),
        newest_due_date=hypothesis.newest_due_date.isoformat(),
        average_days_to_due=hypothesis.average_days_to_due,
        has_reference_match=hypothesis.has_reference_match,
        matched_reference_count=hypothesis.matched_reference_count,
        max_date_difference_days=hypothesis.max_date_difference_days,
        is_fifo_prioritized=hypothesis.is_fifo_prioritized,
        evidence_signals=[
            CombinationMatchEvidenceSignalResponse(
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


def _map_combination_match_result_to_response(
    result: CombinationMatchResult,
) -> CombinationMatchResponse:
    """Map CombinationMatchResult domain aggregate to presentation response schema."""
    prioritized_dto = (
        _map_combination_hypothesis_to_response(result.prioritized_combination)
        if result.prioritized_combination
        else None
    )
    competing_dtos = [
        _map_combination_hypothesis_to_response(c) for c in result.competing_combinations
    ]

    return CombinationMatchResponse(
        payment_id=result.payment_id,
        company_id=result.company_id,
        customer_id=result.customer_id,
        status=result.status.value,
        prioritized_combination=prioritized_dto,
        competing_combinations=[c for c in competing_dtos if c is not None],
        total_combinations_found=result.total_combinations_found,
        applied_heuristic=result.applied_heuristic,
        requires_review=result.requires_review,
        reason_code=result.reason_code.value,
        reason_description=result.reason_description,
        is_universe_truncated=result.is_universe_truncated,
        candidate_count_evaluated=result.candidate_count_evaluated,
        is_deterministic=result.is_deterministic,
        evaluated_at=result.evaluated_at.isoformat(),
    )


@router.post(
    "/combination-match/{payment_id}",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def evaluate_combination_match(
    payment_id: UUID,
    payload: Optional[CombinationMatchRequest] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Deterministically evaluate combination matching (1:N) for a single payment.

    Guarantees:
    - Zero financial accounting state mutation (Rule 4).
    - Fail-closed IDOR security (404 Not Found for cross-tenant payment or customer access).
    - Evaluates competing candidate combinations and applies Rule M-3 FIFO aging heuristics.
    - Strictly flags REVIEW_REQUIRED whenever competing combinations are resolved via heuristics.
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db)

    use_case = CombinationMatchUseCase(
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
            criteria = CombinationMatchCriteria(
                max_combination_size=payload.criteria.max_combination_size,
                amount_tolerance=payload.criteria.amount_tolerance,
                require_exact_currency=payload.criteria.require_exact_currency,
                date_proximity_days=payload.criteria.date_proximity_days,
                enable_fifo_aging=payload.criteria.enable_fifo_aging,
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
        "data": _map_combination_match_result_to_response(result),
    }


@router.post(
    "/combination-match-batch",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def batch_evaluate_combination_matches(
    payload: BatchCombinationMatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Batch evaluate combination matching (1:N) across unreconciled payments for tenant.

    Bounded to max 100 payments per batch. Evaluates without mutating financial state.
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db)

    comb_use_case = CombinationMatchUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )
    batch_use_case = BatchCombinationMatchUseCase(
        payment_lookup_port=payment_adapter,
        combination_match_use_case=comb_use_case,
    )

    criteria = None
    filter_criteria = None

    if payload.criteria:
        criteria = CombinationMatchCriteria(
            max_combination_size=payload.criteria.max_combination_size,
            amount_tolerance=payload.criteria.amount_tolerance,
            require_exact_currency=payload.criteria.require_exact_currency,
            date_proximity_days=payload.criteria.date_proximity_days,
            enable_fifo_aging=payload.criteria.enable_fifo_aging,
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

    dto_results = [_map_combination_match_result_to_response(r) for r in results]
    unique_count = sum(1 for r in results if r.status == CombinationMatchStatus.UNIQUE_COMBINATION_MATCH)
    prioritized_count = sum(
        1 for r in results if r.status == CombinationMatchStatus.PRIORITIZED_COMBINATION_MATCH
    )
    ambiguous_count = sum(
        1 for r in results if r.status == CombinationMatchStatus.AMBIGUOUS_COMBINATION_MATCH
    )
    no_match_count = sum(1 for r in results if r.status == CombinationMatchStatus.NO_COMBINATION_MATCH)

    return {
        "success": True,
        "data": BatchCombinationMatchResponse(
            results=dto_results,
            total_evaluated=len(dto_results),
            unique_matches_found=unique_count,
            prioritized_matches_found=prioritized_count,
            ambiguous_matches_found=ambiguous_count,
            no_matches_found=no_match_count,
        ),
    }


# ==============================================================================
# Phase 14.9 Evidence Collection Router Endpoints
# ==============================================================================


def _map_evidence_item_to_response(item: Any) -> StructuredEvidenceItemResponse:
    """Map StructuredEvidenceItem domain value object to presentation schema."""
    return StructuredEvidenceItemResponse(
        evidence_type=item.evidence_type.value,
        classification=item.classification.value,
        source_field=item.source_field,
        observed_result=item.observed_result,
        description=item.description,
        target_entity=item.target_entity,
        matched_value=mask_evidence_matched_value(item.evidence_type.value, item.matched_value)
        if item.matched_value
        else None,
        expected_value=item.expected_value,
        metadata=item.metadata,
    )


def _map_evidence_collection_result_to_response(
    result: Any,
) -> EvidenceCollectionResponse:
    """Map EvidenceCollectionResult domain aggregate to presentation schema."""
    payment_evidence_dto = PaymentEvidenceContextResponse(
        payment_id=result.payment_evidence.payment_id,
        company_id=result.payment_evidence.company_id,
        items=[_map_evidence_item_to_response(i) for i in result.payment_evidence.items],
        extracted_invoice_references=result.payment_evidence.extracted_invoice_references,
        has_conflicting_identifiers=result.payment_evidence.has_conflicting_identifiers,
    )

    bundle_dtos = [
        CandidateEvidenceBundleResponse(
            invoice_id=b.invoice_id,
            invoice_number=b.invoice_number,
            items=[_map_evidence_item_to_response(i) for i in b.items],
            has_conflicting_evidence=b.has_conflicting_evidence,
            direct_evidence_count=b.direct_evidence_count,
            supporting_evidence_count=b.supporting_evidence_count,
            missing_evidence_count=b.missing_evidence_count,
            conflicting_evidence_count=b.conflicting_evidence_count,
        )
        for b in result.candidate_bundles
    ]

    return EvidenceCollectionResponse(
        payment_id=result.payment_id,
        company_id=result.company_id,
        payment_evidence=payment_evidence_dto,
        candidate_bundles=bundle_dtos,
        total_evidence_items=result.total_evidence_items,
        total_direct_items=result.total_direct_items,
        total_supporting_items=result.total_supporting_items,
        total_missing_items=result.total_missing_items,
        total_conflicting_items=result.total_conflicting_items,
        is_deterministic=result.is_deterministic,
        collected_at=result.collected_at.isoformat(),
    )


@router.post(
    "/evidence-collection/{payment_id}",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def collect_evidence_for_payment(
    payment_id: UUID,
    payload: Optional[EvidenceCollectionRequest] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Collect and classify structured evidence (DIRECT, SUPPORTING, MISSING, CONFLICTING) for a payment.

    Guarantees:
    - Zero financial accounting state mutation (Rule 4).
    - Fail-closed IDOR security (404 Not Found for cross-tenant payment or customer access).
    - Zero scoring / weighting: Exposes structured factual signals without deciding or approving.
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db)

    use_case = EvidenceCollectionUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )

    filter_criteria = None
    override_customer_id = None

    if payload:
        override_customer_id = payload.override_customer_id
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
        filter_criteria=filter_criteria,
        override_customer_id=override_customer_id,
    )

    return {
        "success": True,
        "data": _map_evidence_collection_result_to_response(result),
    }


# ==============================================================================
# Phase 14.10 Evidence Normalization Router Endpoints
# ==============================================================================


def _map_canonical_evidence_object_to_response(item: Any) -> CanonicalEvidenceObjectResponse:
    """Map CanonicalEvidenceObject domain value object to presentation schema."""
    identifiers_dict = item.identifiers.to_dict()
    identifiers_dto = RelevantIdentifiersResponse(
        payment_id=identifiers_dict.get("payment_id"),
        customer_id=identifiers_dict.get("customer_id"),
        invoice_id=identifiers_dict.get("invoice_id"),
        invoice_number=identifiers_dict.get("invoice_number"),
        payment_reference=identifiers_dict.get("payment_reference"),
        utr=identifiers_dict.get("utr"),
        bank_account=mask_bank_account(identifiers_dict.get("bank_account"))
        if identifiers_dict.get("bank_account")
        else None,
    )

    return CanonicalEvidenceObjectResponse(
        evidence_type=item.evidence_type.value,
        source=item.source.value,
        result=item.result.value,
        strength=item.strength.value,
        details=item.details,
        identifiers=identifiers_dto,
        classification=item.classification.value,
        rule_version=item.rule_version,
        algorithm_version=item.algorithm_version,
        target_entity=item.target_entity.value,
        matched_value=mask_evidence_matched_value(item.evidence_type.value, item.matched_value)
        if item.matched_value
        else None,
        expected_value=item.expected_value,
        metadata=item.metadata,
    )


def _map_evidence_normalization_result_to_response(
    result: Any,
) -> EvidenceNormalizationResponse:
    """Map EvidenceNormalizationResult domain aggregate to presentation schema."""
    payment_evidence_dto = NormalizedPaymentEvidenceContextResponse(
        payment_id=result.payment_evidence.payment_id,
        company_id=result.payment_evidence.company_id,
        items=[_map_canonical_evidence_object_to_response(i) for i in result.payment_evidence.items],
        extracted_invoice_references=result.payment_evidence.extracted_invoice_references,
        has_conflicting_identifiers=result.payment_evidence.has_conflicting_identifiers,
    )

    bundle_dtos = [
        NormalizedCandidateBundleResponse(
            invoice_id=b.invoice_id,
            invoice_number=b.invoice_number,
            items=[_map_canonical_evidence_object_to_response(i) for i in b.items],
            has_conflicting_evidence=b.has_conflicting_evidence,
            direct_evidence_count=b.direct_evidence_count,
            supporting_evidence_count=b.supporting_evidence_count,
            missing_evidence_count=b.missing_evidence_count,
            conflicting_evidence_count=b.conflicting_evidence_count,
        )
        for b in result.candidate_bundles
    ]

    return EvidenceNormalizationResponse(
        payment_id=result.payment_id,
        company_id=result.company_id,
        payment_evidence=payment_evidence_dto,
        candidate_bundles=bundle_dtos,
        total_evidence_items=result.total_evidence_items,
        total_direct_items=result.total_direct_items,
        total_supporting_items=result.total_supporting_items,
        total_missing_items=result.total_missing_items,
        total_conflicting_items=result.total_conflicting_items,
        rule_version=result.rule_version,
        algorithm_version=result.algorithm_version,
        is_deterministic=result.is_deterministic,
        normalized_at=result.normalized_at.isoformat(),
    )


@router.post(
    "/evidence-normalization/{payment_id}",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def normalize_evidence_for_payment(
    payment_id: UUID,
    payload: Optional[EvidenceNormalizationRequest] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Convert raw reconciliation evidence into canonical, version-aware, structured evidence objects.

    Guarantees:
    - Zero financial accounting state mutation (Observed Financial Mutation: NONE).
    - Fail-closed IDOR security (404 Not Found for cross-tenant payment or customer access).
    - Semantic preservation: Retains factual meaning, preserving all conflicts and missing signals.
    - Zero scoring / weighting: Provides canonical representation strictly for Phase 14.11.
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db)

    collection_use_case = EvidenceCollectionUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )

    use_case = EvidenceNormalizationUseCase(
        evidence_collection_use_case=collection_use_case,
    )

    filter_criteria = None
    override_customer_id = None

    if payload:
        override_customer_id = payload.override_customer_id
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
        filter_criteria=filter_criteria,
        override_customer_id=override_customer_id,
    )

    return {
        "success": True,
        "data": _map_evidence_normalization_result_to_response(result),
    }


# ==============================================================================
# Phase 14.11 Matching & Scoring Mappers & Endpoints
# ==============================================================================


def _map_score_contribution_to_response(
    contrib: ScoreContribution,
) -> ScoreContributionResponse:
    """Map domain ScoreContribution to presentation DTO with masked sensitive coordinates."""
    masked_matched = mask_evidence_matched_value(
        contrib.evidence_type.value,
        contrib.matched_value,
    )
    masked_expected = mask_evidence_matched_value(
        contrib.evidence_type.value,
        contrib.expected_value,
    )

    return ScoreContributionResponse(
        signal_type=contrib.signal_type.value,
        weight=str(contrib.weight),
        applied=contrib.applied,
        evidence_type=contrib.evidence_type.value,
        source=contrib.source.value,
        result=contrib.result.value,
        reason=contrib.reason,
        target_entity=contrib.target_entity.value,
        matched_value=masked_matched,
        expected_value=masked_expected,
    )


def _map_candidate_score_to_response(
    score_res: CandidateScoreResult,
) -> CandidateScoreResponse:
    """Map domain CandidateScoreResult to presentation DTO."""
    return CandidateScoreResponse(
        invoice_id=score_res.invoice_id,
        invoice_number=score_res.invoice_number,
        total_score=str(score_res.total_score),
        raw_unclamped_score=str(score_res.raw_unclamped_score),
        contributions=[_map_score_contribution_to_response(c) for c in score_res.contributions],
        has_conflicting_evidence=score_res.has_conflicting_evidence,
        rule_version=score_res.rule_version,
        algorithm_version=score_res.algorithm_version,
        is_empirically_validated=score_res.is_empirically_validated,
    )


def _map_matching_scoring_result_to_response(
    result: MatchingScoringResult,
) -> MatchingScoringResponse:
    """Map domain MatchingScoringResult to presentation DTO."""
    return MatchingScoringResponse(
        payment_id=result.payment_id,
        company_id=result.company_id,
        candidate_scores=[_map_candidate_score_to_response(c) for c in result.candidate_scores],
        total_candidates_scored=result.total_candidates_scored,
        rule_version=result.rule_version,
        algorithm_version=result.algorithm_version,
        is_deterministic=result.is_deterministic,
        is_empirically_validated=result.is_empirically_validated,
        scored_at=result.scored_at.isoformat(),
    )


@router.post(
    "/matching-scoring/batch",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def batch_score_payment_candidates(
    payload: Optional[BatchMatchingScoringRequest] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Batch calculate candidate scores across multiple unreconciled payments.

    Guarantees:
    - Bounded to max limit of 100 payments.
    - Zero financial accounting state mutation.
    - Strict tenant isolation: scoped to authenticated current_user.company_id.
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db)

    collection_use_case = EvidenceCollectionUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )
    normalization_use_case = EvidenceNormalizationUseCase(
        evidence_collection_use_case=collection_use_case,
    )
    scoring_use_case = MatchingScoringUseCase(
        evidence_normalization_use_case=normalization_use_case,
    )
    batch_use_case = BatchMatchingScoringUseCase(
        payment_lookup_port=payment_adapter,
        matching_scoring_use_case=scoring_use_case,
    )

    payment_ids = payload.payment_ids if payload else None
    limit = payload.limit if payload else 50
    filter_criteria = None

    if payload and payload.filter_criteria:
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
        payment_ids=payment_ids,
        limit=limit,
        filter_criteria=filter_criteria,
    )

    response_dtos = [_map_matching_scoring_result_to_response(r) for r in results]

    return {
        "success": True,
        "data": {
            "results": response_dtos,
            "total_evaluated": len(response_dtos),
        },
    }


@router.post(
    "/matching-scoring/{payment_id}",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def score_payment_candidates(
    payment_id: UUID,
    payload: Optional[MatchingScoringRequest] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Calculate deterministic candidate scores (0.00 to 100.00) based on normalized evidence.

    Guarantees:
    - Zero financial accounting state mutation (Observed Financial Mutation: NONE).
    - Fail-closed IDOR security (404 Not Found for cross-tenant payment or customer access).
    - Pure Decimal arithmetic; bounds scores to [0.00, 100.00].
    - Traceable score breakdown: explainable provenance for every applied contribution.
    - Non-Decision: Scores only. Never outputs AUTO_ELIGIBLE, MATCH_SUGGESTED, or allocation.
    """
    payment_adapter = SQLAlchemyPaymentLookupAdapter(db=db)
    customer_adapter = SQLAlchemyCustomerLookupAdapter(db=db)
    invoice_adapter = SQLAlchemyInvoiceLookupAdapter(db=db)

    collection_use_case = EvidenceCollectionUseCase(
        payment_lookup_port=payment_adapter,
        customer_lookup_port=customer_adapter,
        invoice_lookup_port=invoice_adapter,
    )
    normalization_use_case = EvidenceNormalizationUseCase(
        evidence_collection_use_case=collection_use_case,
    )
    scoring_use_case = MatchingScoringUseCase(
        evidence_normalization_use_case=normalization_use_case,
    )

    filter_criteria = None
    override_customer_id = None

    if payload:
        override_customer_id = payload.override_customer_id
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

    result = scoring_use_case.execute(
        payment_id=payment_id,
        company_id=current_user.company_id,
        filter_criteria=filter_criteria,
        override_customer_id=override_customer_id,
    )

    return {
        "success": True,
        "data": _map_matching_scoring_result_to_response(result),
    }

