"""Unit tests for Phase 13.2 Candidate Invoice deterministic rule engine."""

from datetime import date
from decimal import Decimal
import uuid

from app.modules.reconciliation.domain.candidate_invoices import (
    InvoiceEvidenceType,
)
from app.modules.reconciliation.domain.invoice_rules import (
    CandidateInvoiceRuleEngine,
    InvoiceCandidateContext,
)
from app.modules.reconciliation.domain.rules import PaymentIntakeContext


def _make_payment(
    amount: Decimal = Decimal("5000.00"),
    currency: str = "INR",
    payment_date: date = date(2026, 8, 15),
    narration: str = "NEFT TRANSFER FROM CUSTOMER",
    reference_number: str = "REF12345",
) -> PaymentIntakeContext:
    return PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        amount=amount,
        currency=currency,
        payment_date=payment_date,
        narration=narration,
        payment_reference=reference_number,
    )


def _make_invoice(
    company_id: uuid.UUID,
    customer_id: uuid.UUID,
    invoice_number: str = "INV-2026-101",
    total_amount: Decimal = Decimal("5000.00"),
    paid_amount: Decimal = Decimal("0.00"),
    outstanding_amount: Decimal = Decimal("5000.00"),
    currency: str = "INR",
    issue_date: date = date(2026, 8, 1),
    due_date: date = date(2026, 8, 15),
    status: str = "PENDING",
    is_archived: bool = False,
) -> InvoiceCandidateContext:
    return InvoiceCandidateContext(
        id=uuid.uuid4(),
        company_id=company_id,
        customer_id=customer_id,
        invoice_number=invoice_number,
        issue_date=issue_date,
        due_date=due_date,
        total_amount=total_amount,
        paid_amount=paid_amount,
        outstanding_amount=outstanding_amount,
        currency=currency,
        status=status,
        is_archived=is_archived,
    )


def test_exact_outstanding_amount_signal() -> None:
    """Test that an exact match between payment amount and outstanding balance triggers EXACT_AMOUNT_MATCH."""
    engine = CandidateInvoiceRuleEngine()
    payment = _make_payment(amount=Decimal("5000.00"))
    invoice = _make_invoice(
        company_id=payment.company_id,
        customer_id=uuid.uuid4(),
        outstanding_amount=Decimal("5000.00"),
        total_amount=Decimal("5000.00"),
    )

    candidate = engine.evaluate_candidate(invoice, payment)
    assert candidate.is_exact_amount_match is True
    assert candidate.is_partial_amount_match is False

    signals = {s.evidence_type: s for s in candidate.evidence_signals}
    assert InvoiceEvidenceType.EXACT_AMOUNT_MATCH in signals
    assert signals[InvoiceEvidenceType.EXACT_AMOUNT_MATCH].weight == 35.0


def test_exact_original_gross_amount_signal() -> None:
    """Test that a payment matching the gross total of a partially paid invoice triggers EXACT_ORIGINAL_AMOUNT_MATCH."""
    engine = CandidateInvoiceRuleEngine()
    payment = _make_payment(amount=Decimal("10000.00"))
    invoice = _make_invoice(
        company_id=payment.company_id,
        customer_id=uuid.uuid4(),
        total_amount=Decimal("10000.00"),
        paid_amount=Decimal("4000.00"),
        outstanding_amount=Decimal("6000.00"),
        status="PARTIALLY_PAID",
    )

    candidate = engine.evaluate_candidate(invoice, payment)
    signals = {s.evidence_type: s for s in candidate.evidence_signals}
    assert InvoiceEvidenceType.EXACT_ORIGINAL_AMOUNT_MATCH in signals
    assert signals[InvoiceEvidenceType.EXACT_ORIGINAL_AMOUNT_MATCH].weight == 25.0


def test_partial_payment_compatible_signal() -> None:
    """Test that a payment amount strictly less than outstanding balance triggers PARTIAL_AMOUNT_COMPATIBLE."""
    engine = CandidateInvoiceRuleEngine()
    payment = _make_payment(amount=Decimal("3000.00"))
    invoice = _make_invoice(
        company_id=payment.company_id,
        customer_id=uuid.uuid4(),
        outstanding_amount=Decimal("5000.00"),
    )

    candidate = engine.evaluate_candidate(invoice, payment)
    assert candidate.is_exact_amount_match is False
    assert candidate.is_partial_amount_match is True

    signals = {s.evidence_type: s for s in candidate.evidence_signals}
    assert InvoiceEvidenceType.PARTIAL_AMOUNT_COMPATIBLE in signals
    assert signals[InvoiceEvidenceType.PARTIAL_AMOUNT_COMPATIBLE].weight == 15.0


def test_invoice_reference_match_in_narration() -> None:
    """Test that invoice number embedded in payment narration triggers INVOICE_NUMBER_MATCH."""
    engine = CandidateInvoiceRuleEngine()
    payment = _make_payment(narration="CMS / INV-2026-889 / ACME CORP")
    invoice = _make_invoice(
        company_id=payment.company_id,
        customer_id=uuid.uuid4(),
        invoice_number="INV-2026-889",
    )

    candidate = engine.evaluate_candidate(invoice, payment)
    assert candidate.is_reference_match is True

    signals = {s.evidence_type: s for s in candidate.evidence_signals}
    assert InvoiceEvidenceType.INVOICE_NUMBER_MATCH in signals
    assert signals[InvoiceEvidenceType.INVOICE_NUMBER_MATCH].weight == 40.0


def test_invoice_reference_match_normalized_token() -> None:
    """Test normalized reference matching when narration has slight separator differences."""
    engine = CandidateInvoiceRuleEngine()
    payment = _make_payment(reference_number="BILL_9901")
    invoice = _make_invoice(
        company_id=payment.company_id,
        customer_id=uuid.uuid4(),
        invoice_number="BILL-9901",
    )

    candidate = engine.evaluate_candidate(invoice, payment)
    assert candidate.is_reference_match is True


def test_temporal_relevance_and_due_date_proximity() -> None:
    """Test temporal relevance scoring across close and far due dates."""
    engine = CandidateInvoiceRuleEngine()
    payment = _make_payment(payment_date=date(2026, 8, 15))

    # Same day due date: causality (+10) + proximity <= 7 days (+10) = 20.0
    inv_close = _make_invoice(
        company_id=payment.company_id,
        customer_id=uuid.uuid4(),
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 15),
    )
    cand_close = engine.evaluate_candidate(inv_close, payment)
    sig_close = next(s for s in cand_close.evidence_signals if s.evidence_type == InvoiceEvidenceType.DATE_RELEVANCE)
    assert sig_close.weight == 20.0

    # 20 days after due date: causality (+10) + proximity <= 30 days (+5) = 15.0
    inv_mid = _make_invoice(
        company_id=payment.company_id,
        customer_id=uuid.uuid4(),
        issue_date=date(2026, 7, 1),
        due_date=date(2026, 7, 26),
    )
    cand_mid = engine.evaluate_candidate(inv_mid, payment)
    sig_mid = next(s for s in cand_mid.evidence_signals if s.evidence_type == InvoiceEvidenceType.DATE_RELEVANCE)
    assert sig_mid.weight == 15.0

    # 45 days after due date: causality (+10) + proximity > 30 days (+2) = 12.0
    inv_far = _make_invoice(
        company_id=payment.company_id,
        customer_id=uuid.uuid4(),
        issue_date=date(2026, 6, 1),
        due_date=date(2026, 7, 1),
    )
    cand_far = engine.evaluate_candidate(inv_far, payment)
    sig_far = next(s for s in cand_far.evidence_signals if s.evidence_type == InvoiceEvidenceType.DATE_RELEVANCE)
    assert sig_far.weight == 12.0



def test_deterministic_candidate_ranking() -> None:
    """Test that candidate universe orders invoices deterministically by priority, exact match, and date."""
    engine = CandidateInvoiceRuleEngine()
    payment = _make_payment(
        amount=Decimal("5000.00"),
        narration="NEFT FOR INV-2026-001",
    )
    cust_id = uuid.uuid4()

    # Invoice 1: Exact reference match + exact amount match + timely date -> Top priority
    inv1 = _make_invoice(
        company_id=payment.company_id,
        customer_id=cust_id,
        invoice_number="INV-2026-001",
        outstanding_amount=Decimal("5000.00"),
        due_date=date(2026, 8, 15),
    )

    # Invoice 2: Exact amount match only (no reference)
    inv2 = _make_invoice(
        company_id=payment.company_id,
        customer_id=cust_id,
        invoice_number="INV-2026-002",
        outstanding_amount=Decimal("5000.00"),
        due_date=date(2026, 8, 15),
    )

    # Invoice 3: Partial payment compatible (no reference)
    inv3 = _make_invoice(
        company_id=payment.company_id,
        customer_id=cust_id,
        invoice_number="INV-2026-003",
        outstanding_amount=Decimal("8000.00"),
        total_amount=Decimal("8000.00"),
        due_date=date(2026, 8, 15),
    )

    universe = engine.generate_universe(
        payment=payment,
        invoices=[inv3, inv2, inv1],  # Given in random order
        customer_id=cust_id,
    )

    assert len(universe.candidates) == 3
    # inv1 has reference match (+40) + exact amount (+35) + date (+20) = 95.0
    assert universe.candidates[0].invoice_id == inv1.id
    assert universe.candidates[0].rank == 1
    assert universe.candidates[0].is_reference_match is True
    assert universe.candidates[0].is_exact_amount_match is True

    # inv2 has exact amount (+35) + date (+20) = 55.0
    assert universe.candidates[1].invoice_id == inv2.id
    assert universe.candidates[1].rank == 2

    # inv3 has partial (+15) + date (+20) = 35.0
    assert universe.candidates[2].invoice_id == inv3.id
    assert universe.candidates[2].rank == 3


def test_bounding_and_truncation_metadata() -> None:
    """Test that candidate retrieval bounds candidates to limit and records audit truncation metadata."""
    engine = CandidateInvoiceRuleEngine()
    payment = _make_payment(amount=Decimal("100.00"))
    cust_id = uuid.uuid4()

    # Generate 50 eligible invoices
    invoices = [
        _make_invoice(
            company_id=payment.company_id,
            customer_id=cust_id,
            invoice_number=f"INV-2026-{i:04d}",
            outstanding_amount=Decimal("500.00"),
            total_amount=Decimal("500.00"),
            due_date=date(2026, 8, 1 + (i % 20)),
        )
        for i in range(50)
    ]

    universe = engine.generate_universe(
        payment=payment,
        invoices=invoices,
        customer_id=cust_id,
        limit=20,  # Bound to 20
    )

    assert universe.total_eligible_invoices == 50
    assert universe.truncated is True
    assert universe.candidate_limit == 20
    assert len(universe.candidates) == 20
    assert "Truncated to top 20" in universe.truncation_reason


def test_strict_currency_filtering() -> None:
    """Test that invoices with mismatching currencies are excluded."""
    engine = CandidateInvoiceRuleEngine()
    payment = _make_payment(amount=Decimal("1000.00"), currency="INR")
    cust_id = uuid.uuid4()

    inv_usd = _make_invoice(
        company_id=payment.company_id,
        customer_id=cust_id,
        currency="USD",
        outstanding_amount=Decimal("1000.00"),
    )

    universe = engine.generate_universe(
        payment=payment,
        invoices=[inv_usd],
        customer_id=cust_id,
        currency_mismatches_detected=1,
    )

    assert len(universe.candidates) == 0
    assert universe.total_eligible_invoices == 0
    assert universe.status_code == "CURRENCY_MISMATCH"
    assert "none match payment currency 'INR'" in universe.truncation_reason



def test_ineligible_invoice_status_and_archive_filtered() -> None:
    """Test that PAID, CANCELLED, DRAFT, and archived invoices are strictly filtered out."""
    engine = CandidateInvoiceRuleEngine()
    payment = _make_payment(amount=Decimal("1000.00"))
    cust_id = uuid.uuid4()

    ineligible_invoices = [
        _make_invoice(payment.company_id, cust_id, status="PAID", outstanding_amount=Decimal("0.00")),
        _make_invoice(payment.company_id, cust_id, status="CANCELLED"),
        _make_invoice(payment.company_id, cust_id, status="DRAFT"),
        _make_invoice(payment.company_id, cust_id, is_archived=True),
    ]

    universe = engine.generate_universe(
        payment=payment,
        invoices=ineligible_invoices,
        customer_id=cust_id,
    )
    assert len(universe.candidates) == 0
    assert universe.total_eligible_invoices == 0
    assert universe.status_code == "NO_ELIGIBLE_INVOICES"


def test_repeatability_and_determinism_100_iterations() -> None:
    """Test that candidate universe ranking is 100% identical across 100 repeated evaluations."""
    engine = CandidateInvoiceRuleEngine()
    payment = _make_payment(amount=Decimal("5000.00"), narration="TEST RUN")
    cust_id = uuid.uuid4()

    invoices = [
        _make_invoice(
            payment.company_id,
            cust_id,
            invoice_number=f"INV-DETERM-{i:03d}",
            outstanding_amount=Decimal(f"{1000 * (i + 1)}.00"),
            total_amount=Decimal(f"{1000 * (i + 1)}.00"),
            due_date=date(2026, 8, 10 + i),
        )
        for i in range(10)
    ]

    baseline = engine.generate_universe(payment, invoices, cust_id)
    baseline_order = [c.invoice_id for c in baseline.candidates]
    baseline_scores = [c.retrieval_priority for c in baseline.candidates]

    for _ in range(100):
        # Permute input order to verify permutation invariance
        permuted_invoices = list(reversed(invoices))
        result = engine.generate_universe(payment, permuted_invoices, cust_id)
        assert [c.invoice_id for c in result.candidates] == baseline_order
        assert [c.retrieval_priority for c in result.candidates] == baseline_scores
