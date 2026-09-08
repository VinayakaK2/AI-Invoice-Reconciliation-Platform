"""Unit tests for Phase 13.2 Candidate Invoice domain entities and value objects."""

from datetime import date
from decimal import Decimal
import uuid
import pytest

from app.modules.reconciliation.domain.candidate_invoices import (
    CandidateInvoice,
    CandidateInvoiceEvidenceSignal,
    CandidateInvoiceUniverse,
    InvoiceEvidenceType,
)
from app.modules.reconciliation.domain.entities import SignalStrength
from app.shared.exceptions import DomainError, FinancialInvariantError


def test_candidate_invoice_valid_construction() -> None:
    """Test valid creation of CandidateInvoice with balance conservation."""
    inv_id = uuid.uuid4()
    candidate = CandidateInvoice(
        invoice_id=inv_id,
        invoice_number="INV-2026-001",
        total_amount=Decimal("1000.00"),
        paid_amount=Decimal("200.00"),
        outstanding_amount=Decimal("800.00"),
        currency="INR",
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 31),
        status="PARTIALLY_PAID",
        retrieval_priority=75.5,
        evidence_signals=[],
        is_exact_amount_match=False,
        is_partial_amount_match=True,
        is_reference_match=False,
        rank=1,
    )
    assert candidate.invoice_id == inv_id
    assert candidate.outstanding_amount == Decimal("800.00")
    assert candidate.rank == 1

    d = candidate.to_dict()
    assert d["invoice_id"] == str(inv_id)
    assert d["total_amount"] == "1000.00"
    assert d["outstanding_amount"] == "800.00"
    assert d["retrieval_priority"] == 75.5


def test_candidate_invoice_negative_or_zero_outstanding_rejected() -> None:
    """Test that candidate invoice rejects zero or negative outstanding balance."""
    with pytest.raises(FinancialInvariantError, match="must have positive outstanding balance"):
        CandidateInvoice(
            invoice_id=uuid.uuid4(),
            invoice_number="INV-2026-002",
            total_amount=Decimal("1000.00"),
            paid_amount=Decimal("1000.00"),
            outstanding_amount=Decimal("0.00"),
            currency="INR",
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            status="PAID",
            retrieval_priority=50.0,
            evidence_signals=[],
            is_exact_amount_match=False,
            is_partial_amount_match=False,
            is_reference_match=False,
        )


def test_candidate_invoice_balance_conservation_violation() -> None:
    """Test that candidate invoice enforces paid + outstanding == total."""
    with pytest.raises(FinancialInvariantError, match="violates balance conservation"):
        CandidateInvoice(
            invoice_id=uuid.uuid4(),
            invoice_number="INV-2026-003",
            total_amount=Decimal("1000.00"),
            paid_amount=Decimal("100.00"),
            outstanding_amount=Decimal("800.00"),  # 100 + 800 != 1000
            currency="INR",
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            status="PARTIALLY_PAID",
            retrieval_priority=50.0,
            evidence_signals=[],
            is_exact_amount_match=False,
            is_partial_amount_match=False,
            is_reference_match=False,
        )


def test_candidate_invoice_status_restriction() -> None:
    """Test that only PENDING or PARTIALLY_PAID invoices can be candidates."""
    with pytest.raises(DomainError, match="Only PENDING or PARTIALLY_PAID"):
        CandidateInvoice(
            invoice_id=uuid.uuid4(),
            invoice_number="INV-2026-004",
            total_amount=Decimal("500.00"),
            paid_amount=Decimal("0.00"),
            outstanding_amount=Decimal("500.00"),
            currency="INR",
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            status="DRAFT",  # Drafts cannot be candidates
            retrieval_priority=50.0,
            evidence_signals=[],
            is_exact_amount_match=False,
            is_partial_amount_match=False,
            is_reference_match=False,
        )


def test_candidate_invoice_priority_range() -> None:
    """Test that retrieval priority must be strictly bounded in [0.0, 100.0]."""
    with pytest.raises(DomainError, match="Retrieval priority must be between 0.0 and 100.0"):
        CandidateInvoice(
            invoice_id=uuid.uuid4(),
            invoice_number="INV-2026-005",
            total_amount=Decimal("500.00"),
            paid_amount=Decimal("0.00"),
            outstanding_amount=Decimal("500.00"),
            currency="INR",
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            status="PENDING",
            retrieval_priority=120.0,  # Invalid
            evidence_signals=[],
            is_exact_amount_match=False,
            is_partial_amount_match=False,
            is_reference_match=False,
        )


def test_candidate_evidence_signal_serialization() -> None:
    """Test CandidateInvoiceEvidenceSignal serialization."""
    sig = CandidateInvoiceEvidenceSignal(
        evidence_type=InvoiceEvidenceType.EXACT_AMOUNT_MATCH,
        signal_strength=SignalStrength.STRONG,
        matched_value="500.00",
        source_field="amount",
        weight=35.0,
        confidence_delta=35.0,
        metadata={"key": "val"},
    )
    d = sig.to_dict()
    assert d["evidence_type"] == "EXACT_AMOUNT_MATCH"
    assert d["signal_strength"] == "STRONG"
    assert d["weight"] == 35.0
    assert d["metadata"] == {"key": "val"}


def test_candidate_invoice_universe_serialization() -> None:
    """Test CandidateInvoiceUniverse structure and serialization."""
    p_id = uuid.uuid4()
    c_id = uuid.uuid4()
    cust_id = uuid.uuid4()

    universe = CandidateInvoiceUniverse(
        payment_id=p_id,
        company_id=c_id,
        customer_id=cust_id,
        candidates=[],
        total_eligible_invoices=0,
        truncated=False,
        candidate_limit=30,
        truncation_reason=None,
        currency_mismatches_detected=0,
        status_code="NO_ELIGIBLE_INVOICES",
    )
    d = universe.to_dict()
    assert d["payment_id"] == str(p_id)
    assert d["company_id"] == str(c_id)
    assert d["customer_id"] == str(cust_id)
    assert d["status_code"] == "NO_ELIGIBLE_INVOICES"
    assert d["is_deterministic"] is True
