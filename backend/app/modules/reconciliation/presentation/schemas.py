"""Pydantic presentation schemas and data masking for counterparty identification."""

from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field



def mask_bank_account(account: Optional[str]) -> Optional[str]:
    """Mask bank account coordinate preserving only the last 4 digits."""
    if not account or not isinstance(account, str):
        return None
    clean = account.strip()
    if len(clean) <= 4:
        return "****"
    return f"{'*' * (len(clean) - 4)}{clean[-4:]}"


def mask_upi_vpa(vpa: Optional[str]) -> Optional[str]:
    """Mask UPI VPA user prefix preserving the first 2 characters and handle domain."""
    if not vpa or not isinstance(vpa, str):
        return None
    clean = vpa.strip()
    if "@" not in clean:
        return mask_bank_account(clean)
    user, handle = clean.split("@", 1)
    if len(user) <= 2:
        masked_user = "**"
    else:
        masked_user = f"{user[:2]}{'*' * (len(user) - 2)}"
    return f"{masked_user}@{handle}"


def mask_evidence_matched_value(matched_val: str, ev_type: str) -> str:
    """Mask sensitive banking coordinates in evidence matched value string."""
    if ev_type == "EXACT_BANK_ACCOUNT":
        return mask_bank_account(matched_val) or matched_val
    if ev_type in ("EXACT_UPI_VPA", "EXACT_VIRTUAL_ACCOUNT"):
        return mask_upi_vpa(matched_val) or matched_val
    return matched_val


class EvidenceSignalResponse(BaseModel):
    """Presentation representation of an individual evidence signal."""

    model_config = ConfigDict(from_attributes=True)

    evidence_type: str
    signal_strength: str
    matched_value: str
    source_field: str
    weight: float
    confidence_delta: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CustomerCandidateResponse(BaseModel):
    """Presentation representation of an evaluated customer candidate."""

    model_config = ConfigDict(from_attributes=True)

    customer_id: UUID
    customer_name: str
    composite_score: float
    evidence_signals: List[EvidenceSignalResponse] = Field(default_factory=list)
    rank: int


class CustomerIdentificationResponse(BaseModel):
    """Authoritative counterparty identification response for a payment."""

    model_config = ConfigDict(from_attributes=True)

    payment_id: UUID
    company_id: UUID
    status: str
    primary_candidate: Optional[CustomerCandidateResponse] = None
    candidates: List[CustomerCandidateResponse] = Field(default_factory=list)
    evidence_signals: List[EvidenceSignalResponse] = Field(default_factory=list)
    total_evidence_score: float
    reason_code: str
    reason_description: str
    is_deterministic: bool
    evaluated_at: str


class BatchCustomerIdentificationRequest(BaseModel):
    """Request payload for batch counterparty identification."""

    payment_ids: Optional[List[UUID]] = None
    limit: int = Field(default=50, ge=1, le=100)


class BatchCustomerIdentificationResponse(BaseModel):
    """Response payload for batch counterparty identification."""

    results: List[CustomerIdentificationResponse]
    total_evaluated: int


class CandidateInvoiceEvidenceResponse(BaseModel):
    """Presentation schema for candidate invoice evidence signal."""

    model_config = ConfigDict(from_attributes=True)

    evidence_type: str
    signal_strength: str
    matched_value: str
    source_field: str
    weight: float
    confidence_delta: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CandidateInvoiceResponse(BaseModel):
    """Presentation schema for a ranked candidate invoice."""

    model_config = ConfigDict(from_attributes=True)

    invoice_id: UUID
    invoice_number: str
    total_amount: str
    paid_amount: str
    outstanding_amount: str
    currency: str
    issue_date: str
    due_date: str
    status: str
    retrieval_priority: float
    evidence_signals: List[CandidateInvoiceEvidenceResponse] = Field(default_factory=list)
    is_exact_amount_match: bool
    is_partial_amount_match: bool
    is_reference_match: bool
    rank: int


class CandidateInvoiceUniverseResponse(BaseModel):
    """Presentation schema for bounded candidate invoice universe."""

    model_config = ConfigDict(from_attributes=True)

    payment_id: UUID
    company_id: UUID
    customer_id: Optional[UUID] = None
    candidates: List[CandidateInvoiceResponse] = Field(default_factory=list)
    total_eligible_invoices: int
    truncated: bool
    candidate_limit: int
    truncation_reason: Optional[str] = None
    currency_mismatches_detected: int = 0
    status_code: str
    is_deterministic: bool
    evaluated_at: str


class GenerateCandidateInvoicesRequest(BaseModel):
    """Request payload for generating candidate invoices for a payment."""

    override_customer_id: Optional[UUID] = None
    limit: int = Field(default=30, ge=1, le=100)


class CandidateFilterCriteriaRequest(BaseModel):
    """Request payload for filtering candidate invoices."""

    override_customer_id: Optional[UUID] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    max_lookback_days: Optional[int] = Field(default=365, ge=1, le=3650)
    max_advance_days: int = Field(default=0, ge=0, le=30)
    require_causality: bool = True
    allowed_statuses: Optional[List[str]] = None
    require_reference_match: bool = False
    disallow_overpayment: bool = False
    max_candidates: int = Field(default=30, ge=1, le=100)


class ExcludedCandidateResponse(BaseModel):
    """Presentation schema for an excluded candidate invoice."""

    model_config = ConfigDict(from_attributes=True)

    invoice_id: UUID
    invoice_number: str
    outstanding_amount: str
    currency: str
    issue_date: str
    due_date: str
    exclusion_reasons: List[str]
    diagnostic_details: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FilteredCandidateUniverseResponse(BaseModel):
    """Presentation schema for deterministically filtered candidate invoice universe."""

    model_config = ConfigDict(from_attributes=True)

    payment_id: UUID
    company_id: UUID
    customer_id: Optional[UUID] = None
    retained_candidates: List[CandidateInvoiceResponse] = Field(default_factory=list)
    excluded_candidates: List[ExcludedCandidateResponse] = Field(default_factory=list)
    filter_criteria: Dict[str, Any] = Field(default_factory=dict)
    total_evaluated: int
    total_retained: int
    total_excluded: int
    currency_mismatches_detected: int = 0
    exclusion_breakdown: Dict[str, int] = Field(default_factory=dict)
    status_code: str
    is_deterministic: bool
    evaluated_at: str



class PaymentIntakeResponse(BaseModel):
    """Presentation DTO for payment intake eligibility outcome."""

    model_config = ConfigDict(from_attributes=True)

    payment_id: UUID
    company_id: UUID
    status: str
    is_eligible: bool
    reason_code: str
    reason_description: str
    original_amount: str
    allocated_amount: str
    unallocated_amount: str
    effective_amount: str
    currency: str
    payment_date: str
    bank_account_number: Optional[str] = None
    is_deterministic: bool
    evaluated_at: str


class BatchPaymentIntakeRequest(BaseModel):
    """Request payload for batch payment intake evaluation."""

    payment_ids: Optional[List[UUID]] = None
    limit: int = Field(default=50, ge=1, le=100)


class BatchPaymentIntakeResponse(BaseModel):
    """Presentation response for batch payment intake evaluation."""

    results: List[PaymentIntakeResponse]
    total_evaluated: int
    total_eligible: int


class ExactMatchCriteriaRequest(BaseModel):
    """Optional configuration for exact match evaluation."""

    amount_tolerance: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    require_exact_currency: bool = True
    date_proximity_days: int = Field(default=30, ge=0)


class ExactMatchRequest(BaseModel):
    """Request payload for exact 1:1 match evaluation."""

    override_customer_id: Optional[UUID] = None
    criteria: Optional[ExactMatchCriteriaRequest] = None
    filter_criteria: Optional[CandidateFilterCriteriaRequest] = None


class ExactMatchEvidenceSignalResponse(BaseModel):
    """Presentation DTO for an evidence signal supporting exact match."""

    model_config = ConfigDict(from_attributes=True)

    evidence_type: str
    signal_strength: str
    description: str
    matched_value: str
    source_field: str
    weight: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExactMatchHypothesisResponse(BaseModel):
    """Presentation DTO for a proposed 1:1 match hypothesis."""

    model_config = ConfigDict(from_attributes=True)

    invoice_id: UUID
    invoice_number: str
    matched_amount: str
    invoice_outstanding_before: str
    invoice_outstanding_after: str
    payment_unallocated_before: str
    payment_unallocated_after: str
    currency: str
    match_type: str
    is_reference_match: bool
    date_difference_days: int
    evidence_signals: List[ExactMatchEvidenceSignalResponse] = Field(default_factory=list)


class ExactMatchResponse(BaseModel):
    """Presentation DTO for exact 1:1 matching evaluation outcome."""

    model_config = ConfigDict(from_attributes=True)

    payment_id: UUID
    company_id: UUID
    customer_id: Optional[UUID] = None
    status: str
    matched_candidate: Optional[ExactMatchHypothesisResponse] = None
    competing_candidates: List[ExactMatchHypothesisResponse] = Field(default_factory=list)
    total_exact_candidates_found: int
    reason_code: str
    reason_description: str
    is_universe_truncated: bool
    candidate_count_evaluated: int
    is_deterministic: bool
    evaluated_at: str


class BatchExactMatchRequest(BaseModel):
    """Request payload for batch exact matching evaluation."""

    payment_ids: Optional[List[UUID]] = None
    limit: int = Field(default=50, ge=1, le=100)
    criteria: Optional[ExactMatchCriteriaRequest] = None
    filter_criteria: Optional[CandidateFilterCriteriaRequest] = None


class BatchExactMatchResponse(BaseModel):
    """Presentation response for batch exact matching evaluation."""

    results: List[ExactMatchResponse]
    total_evaluated: int
    exact_matches_found: int
    ambiguous_matches_found: int
    no_matches_found: int


class PartialMatchCriteriaRequest(BaseModel):
    """Optional configuration for partial match evaluation."""

    amount_tolerance: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    require_exact_currency: bool = True
    date_proximity_days: int = Field(default=30, ge=0)


class PartialMatchRequest(BaseModel):
    """Request payload for partial 1:1 match evaluation."""

    override_customer_id: Optional[UUID] = None
    criteria: Optional[PartialMatchCriteriaRequest] = None
    filter_criteria: Optional[CandidateFilterCriteriaRequest] = None


class PartialMatchEvidenceSignalResponse(BaseModel):
    """Presentation DTO for an evidence signal supporting partial match."""

    model_config = ConfigDict(from_attributes=True)

    evidence_type: str
    signal_strength: str
    description: str
    matched_value: str
    source_field: str
    weight: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PartialMatchHypothesisResponse(BaseModel):
    """Presentation DTO for a proposed partial 1:1 match hypothesis."""

    model_config = ConfigDict(from_attributes=True)

    invoice_id: UUID
    invoice_number: str
    matched_amount: str
    invoice_outstanding_before: str
    invoice_outstanding_after: str
    payment_unallocated_before: str
    payment_unallocated_after: str
    currency: str
    match_type: str
    is_reference_match: bool
    date_difference_days: int
    evidence_signals: List[PartialMatchEvidenceSignalResponse] = Field(default_factory=list)


class PartialMatchResponse(BaseModel):
    """Presentation DTO for partial 1:1 matching evaluation outcome."""

    model_config = ConfigDict(from_attributes=True)

    payment_id: UUID
    company_id: UUID
    customer_id: Optional[UUID] = None
    status: str
    matched_candidate: Optional[PartialMatchHypothesisResponse] = None
    competing_candidates: List[PartialMatchHypothesisResponse] = Field(default_factory=list)
    total_partial_candidates_found: int
    reason_code: str
    reason_description: str
    is_universe_truncated: bool
    candidate_count_evaluated: int
    is_deterministic: bool
    evaluated_at: str


class BatchPartialMatchRequest(BaseModel):
    """Request payload for batch partial matching evaluation."""

    payment_ids: Optional[List[UUID]] = None
    limit: int = Field(default=50, ge=1, le=100)
    criteria: Optional[PartialMatchCriteriaRequest] = None
    filter_criteria: Optional[CandidateFilterCriteriaRequest] = None


class BatchPartialMatchResponse(BaseModel):
    """Presentation response for batch partial matching evaluation."""

    results: List[PartialMatchResponse]
    total_evaluated: int
    partial_matches_found: int
    ambiguous_matches_found: int
    no_matches_found: int


class MultiInvoiceMatchCriteriaRequest(BaseModel):
    """Optional configuration for multi-invoice match evaluation."""

    max_combination_size: int = Field(default=4, ge=2, le=4)
    amount_tolerance: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    require_exact_currency: bool = True
    date_proximity_days: int = Field(default=30, ge=0)


class MultiInvoiceMatchRequest(BaseModel):
    """Request payload for multi-invoice match evaluation."""

    override_customer_id: Optional[UUID] = None
    criteria: Optional[MultiInvoiceMatchCriteriaRequest] = None
    filter_criteria: Optional[CandidateFilterCriteriaRequest] = None


class MultiInvoiceMatchEvidenceSignalResponse(BaseModel):
    """Presentation DTO for an evidence signal supporting a multi-invoice match."""

    model_config = ConfigDict(from_attributes=True)

    evidence_type: str
    signal_strength: str
    description: str
    matched_value: str
    source_field: str
    weight: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MultiInvoiceMatchHypothesisResponse(BaseModel):
    """Presentation DTO for a proposed multi-invoice match hypothesis (1:N)."""

    model_config = ConfigDict(from_attributes=True)

    invoice_ids: List[UUID]
    invoice_numbers: List[str]
    matched_amount: str
    invoices_outstanding_before: List[str]
    invoices_outstanding_after: List[str]
    payment_unallocated_before: str
    payment_unallocated_after: str
    currency: str
    match_type: str
    combination_size: int
    has_reference_match: bool
    matched_reference_count: int
    max_date_difference_days: int
    evidence_signals: List[MultiInvoiceMatchEvidenceSignalResponse] = Field(default_factory=list)


class MultiInvoiceMatchResponse(BaseModel):
    """Presentation DTO for multi-invoice matching evaluation outcome."""

    model_config = ConfigDict(from_attributes=True)

    payment_id: UUID
    company_id: UUID
    customer_id: Optional[UUID] = None
    status: str
    matched_combination: Optional[MultiInvoiceMatchHypothesisResponse] = None
    competing_combinations: List[MultiInvoiceMatchHypothesisResponse] = Field(default_factory=list)
    total_combinations_found: int
    reason_code: str
    reason_description: str
    is_universe_truncated: bool
    candidate_count_evaluated: int
    is_deterministic: bool
    evaluated_at: str


class BatchMultiInvoiceMatchRequest(BaseModel):
    """Request payload for batch multi-invoice matching evaluation."""

    payment_ids: Optional[List[UUID]] = None
    limit: int = Field(default=50, ge=1, le=100)
    criteria: Optional[MultiInvoiceMatchCriteriaRequest] = None
    filter_criteria: Optional[CandidateFilterCriteriaRequest] = None


class BatchMultiInvoiceMatchResponse(BaseModel):
    """Presentation response for batch multi-invoice matching evaluation."""

    results: List[MultiInvoiceMatchResponse]
    total_evaluated: int
    multi_invoice_matches_found: int
    ambiguous_matches_found: int
    no_matches_found: int


# ==============================================================================
# Phase 14.8 Combination Matching Presentation Schemas
# ==============================================================================


class CombinationMatchCriteriaRequest(BaseModel):
    """Optional configuration for combination match evaluation."""

    max_combination_size: int = Field(default=4, ge=2, le=4)
    amount_tolerance: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    require_exact_currency: bool = True
    date_proximity_days: int = Field(default=30, ge=0)
    enable_fifo_aging: bool = True


class CombinationMatchRequest(BaseModel):
    """Request payload for combination match evaluation."""

    override_customer_id: Optional[UUID] = None
    criteria: Optional[CombinationMatchCriteriaRequest] = None
    filter_criteria: Optional[CandidateFilterCriteriaRequest] = None


class CombinationMatchEvidenceSignalResponse(BaseModel):
    """Presentation DTO for an evidence signal supporting a combination match."""

    model_config = ConfigDict(from_attributes=True)

    evidence_type: str
    signal_strength: str
    description: str
    matched_value: str
    source_field: str
    weight: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CombinationHypothesisResponse(BaseModel):
    """Presentation DTO for a combination matching hypothesis."""

    model_config = ConfigDict(from_attributes=True)

    invoice_ids: List[UUID]
    invoice_numbers: List[str]
    matched_amount: str
    invoices_outstanding_before: List[str]
    invoices_outstanding_after: List[str]
    payment_unallocated_before: str
    payment_unallocated_after: str
    currency: str
    combination_size: int
    oldest_due_date: str
    newest_due_date: str
    average_days_to_due: float
    has_reference_match: bool
    matched_reference_count: int
    max_date_difference_days: int
    is_fifo_prioritized: bool
    evidence_signals: List[CombinationMatchEvidenceSignalResponse] = Field(default_factory=list)


class CombinationMatchResponse(BaseModel):
    """Presentation DTO for combination matching evaluation outcome."""

    model_config = ConfigDict(from_attributes=True)

    payment_id: UUID
    company_id: UUID
    customer_id: Optional[UUID] = None
    status: str
    prioritized_combination: Optional[CombinationHypothesisResponse] = None
    competing_combinations: List[CombinationHypothesisResponse] = Field(default_factory=list)
    total_combinations_found: int
    applied_heuristic: Optional[str] = None
    requires_review: bool
    reason_code: str
    reason_description: str
    is_universe_truncated: bool
    candidate_count_evaluated: int
    is_deterministic: bool
    evaluated_at: str


class BatchCombinationMatchRequest(BaseModel):
    """Request payload for batch combination matching evaluation."""

    payment_ids: Optional[List[UUID]] = None
    limit: int = Field(default=50, ge=1, le=100)
    criteria: Optional[CombinationMatchCriteriaRequest] = None
    filter_criteria: Optional[CandidateFilterCriteriaRequest] = None


class BatchCombinationMatchResponse(BaseModel):
    """Presentation response for batch combination matching evaluation."""

    results: List[CombinationMatchResponse]
    total_evaluated: int
    unique_matches_found: int
    prioritized_matches_found: int
    ambiguous_matches_found: int
    no_matches_found: int


