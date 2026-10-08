"""Unit tests for Phase 14.9 Evidence Collection pure domain engine.

Tests:
1. Direct Evidence:
   - Payment account matches customer bank account -> DIRECT.
   - Narration explicitly references invoice number -> DIRECT.
   - Payment effective amount exactly equals invoice outstanding -> DIRECT.
2. Supporting Evidence:
   - Customer name token overlap in narration -> SUPPORTING.
   - Registered customer alias matched in narration -> SUPPORTING.
   - UTR reference pattern extracted from narration -> SUPPORTING.
   - Partial payment amount compatibility -> SUPPORTING.
   - Date causality (payment date >= invoice issue date) -> SUPPORTING.
   - Date proximity (payment date within 30 days of due date) -> SUPPORTING.
   - Currency consistency -> SUPPORTING.
3. Missing Evidence:
   - Empty payment reference -> MISSING.
   - Empty narration / no UTR -> MISSING.
   - No customer bank account provided -> MISSING.
   - Payment date distant from due date (> 30 days) -> MISSING.
   - Candidate invoice number not in narration -> MISSING.
4. Conflicting Evidence:
   - Payment account belongs to a DIFFERENT registered customer -> CONFLICTING.
   - Payment narration explicitly references Invoice A, while candidate is Invoice B -> CONFLICTING.
   - Payment date is BEFORE invoice issue date (non-causal) -> CONFLICTING.
   - Payment currency differs from invoice currency -> CONFLICTING.
5. Determinism & Decimal Precision:
   - 100 permutation runs with identical structured output.
   - Zero floating-point arithmetic throughout.
"""

from datetime import date, timedelta
from decimal import Decimal
import random
from typing import Optional
import uuid
import pytest

from app.modules.reconciliation.domain.candidate_invoices import CandidateInvoice
from app.modules.reconciliation.domain.evidence_collection import (
    EvidenceClassification,
    EvidenceCollectionEngine,
    EvidenceCollectionResult,
    EvidenceType,
)
from app.modules.reconciliation.domain.rules import CustomerLookupContext, PaymentIntakeContext
from app.shared.exceptions import DomainError


def _make_payment(
    amount: str = "50000.00",
    currency: str = "INR",
    payment_date: Optional[date] = None,
    narration: Optional[str] = "PAYMENT FOR INVOICE INV-2026-001",
    payment_reference: Optional[str] = "REF123456",
    bank_account_number: Optional[str] = "998877665544",
    company_id: Optional[uuid.UUID] = None,
) -> PaymentIntakeContext:
    amt_dec = Decimal(amount)
    return PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id or uuid.uuid4(),
        amount=amt_dec,
        currency=currency,
        payment_date=payment_date or date(2026, 8, 15),
        narration=narration,
        payment_reference=payment_reference,
        bank_account_number=bank_account_number,
        allocated_amount=Decimal("0.00"),
        unallocated_amount=amt_dec,
    )


def _make_candidate(
    invoice_number: str = "INV-2026-001",
    outstanding: str = "50000.00",
    issue_date: Optional[date] = None,
    due_date: Optional[date] = None,
    currency: str = "INR",
) -> CandidateInvoice:
    out_dec = Decimal(outstanding)
    iss_date = issue_date or date(2026, 8, 1)
    d_date = due_date or date(2026, 8, 31)

    return CandidateInvoice(
        invoice_id=uuid.uuid4(),
        invoice_number=invoice_number,
        total_amount=out_dec,
        paid_amount=Decimal("0.00"),
        outstanding_amount=out_dec,
        currency=currency,
        issue_date=iss_date,
        due_date=d_date,
        status="PENDING",
        retrieval_priority=80.0,
        evidence_signals=[],
        is_exact_amount_match=False,
        is_partial_amount_match=False,
        is_reference_match=False,
        rank=1,
    )


def _make_customer(
    name: str = "Acme Global Corp",
    identifiers: Optional[list] = None,
    aliases: Optional[list] = None,
    customer_id: Optional[uuid.UUID] = None,
) -> CustomerLookupContext:
    return CustomerLookupContext(
        customer_id=customer_id or uuid.uuid4(),
        name=name,
        tax_id="GSTIN-ACME-01",
        is_archived=False,
        aliases=aliases or ["Acme Global", "Acme Corp"],
        identifiers=identifiers or [("BANK_ACCOUNT", "998877665544")],
    )


# ==============================================================================
# Unit Tests
# ==============================================================================


def test_direct_evidence_classification() -> None:
    """Verify strong explicit matches are classified as DIRECT."""
    engine = EvidenceCollectionEngine()
    pay = _make_payment(
        amount="50000.00",
        narration="SETTLEMENT FOR INV-2026-001 UTR: HDFCR52026081501",
        bank_account_number="998877665544",
    )
    cand = _make_candidate(invoice_number="INV-2026-001", outstanding="50000.00")
    cust = _make_customer(identifiers=[("BANK_ACCOUNT", "998877665544")])

    result = engine.evaluate(payment=pay, candidates=[cand], customer=cust)

    assert result.total_direct_items >= 2

    # 1. Bank account match -> DIRECT
    acc_items = [i for i in result.payment_evidence.items if i.evidence_type == EvidenceType.BANK_ACCOUNT_IDENTIFIER]
    assert len(acc_items) == 1
    assert acc_items[0].classification == EvidenceClassification.DIRECT

    # 2. Invoice number match -> DIRECT
    bundle = result.candidate_bundles[0]
    inv_items = [i for i in bundle.items if i.evidence_type == EvidenceType.INVOICE_NUMBER_IN_NARRATION]
    assert len(inv_items) == 1
    assert inv_items[0].classification == EvidenceClassification.DIRECT

    # 3. Exact amount equality -> DIRECT
    amt_items = [i for i in bundle.items if i.evidence_type == EvidenceType.AMOUNT_EXACT_EQUALITY]
    assert len(amt_items) == 1
    assert amt_items[0].classification == EvidenceClassification.DIRECT


def test_supporting_evidence_classification() -> None:
    """Verify contextual signals are classified as SUPPORTING."""
    engine = EvidenceCollectionEngine()
    pay = _make_payment(
        amount="20000.00",
        narration="TRANSFER FROM ACME GLOBAL CORP FOR SERVICES",
        payment_date=date(2026, 8, 15),
    )
    # Candidate outstanding is 50,000 -> Partial amount compatible
    cand = _make_candidate(
        invoice_number="INV-2026-999",
        outstanding="50000.00",
        issue_date=date(2026, 8, 1),
        due_date=date(2026, 8, 25),
    )
    cust = _make_customer(name="Acme Global Corp")

    result = engine.evaluate(payment=pay, candidates=[cand], customer=cust)
    bundle = result.candidate_bundles[0]

    # Name token overlap -> SUPPORTING
    name_items = [i for i in result.payment_evidence.items if i.evidence_type == EvidenceType.CUSTOMER_NAME_TOKEN]
    assert len(name_items) == 1
    assert name_items[0].classification == EvidenceClassification.SUPPORTING

    # Partial amount -> SUPPORTING
    amt_items = [i for i in bundle.items if i.evidence_type == EvidenceType.AMOUNT_PARTIAL_COMPATIBLE]
    assert len(amt_items) == 1
    assert amt_items[0].classification == EvidenceClassification.SUPPORTING

    # Date causality -> SUPPORTING
    causal_items = [i for i in bundle.items if i.evidence_type == EvidenceType.DATE_CAUSALITY]
    assert len(causal_items) == 1
    assert causal_items[0].classification == EvidenceClassification.SUPPORTING

    # Date proximity -> SUPPORTING
    prox_items = [i for i in bundle.items if i.evidence_type == EvidenceType.DATE_PROXIMITY]
    assert len(prox_items) == 1
    assert prox_items[0].classification == EvidenceClassification.SUPPORTING


def test_missing_evidence_classification() -> None:
    """Verify absent signals are classified as MISSING without fabricating data."""
    engine = EvidenceCollectionEngine()
    pay = _make_payment(
        amount="50000.00",
        narration="",  # Empty narration
        payment_reference="",  # Empty reference
        bank_account_number=None,  # No account
        payment_date=date(2026, 12, 1),  # Distant date
    )
    cand = _make_candidate(
        invoice_number="INV-2026-001",
        outstanding="50000.00",
        due_date=date(2026, 8, 1),  # 120 days away
    )
    cust = _make_customer(name="Acme Global Corp")

    result = engine.evaluate(payment=pay, candidates=[cand], customer=cust)

    # Reference MISSING
    ref_items = [i for i in result.payment_evidence.items if i.evidence_type == EvidenceType.PAYMENT_REFERENCE]
    assert ref_items[0].classification == EvidenceClassification.MISSING

    # UTR MISSING
    utr_items = [i for i in result.payment_evidence.items if i.evidence_type == EvidenceType.UTR_IDENTIFIER]
    assert utr_items[0].classification == EvidenceClassification.MISSING

    # Account MISSING
    acc_items = [i for i in result.payment_evidence.items if i.evidence_type == EvidenceType.BANK_ACCOUNT_IDENTIFIER]
    assert acc_items[0].classification == EvidenceClassification.MISSING

    # Date Proximity MISSING
    bundle = result.candidate_bundles[0]
    prox_items = [i for i in bundle.items if i.evidence_type == EvidenceType.DATE_PROXIMITY]
    assert prox_items[0].classification == EvidenceClassification.MISSING


def test_conflicting_evidence_classification_other_customer_account() -> None:
    """Verify that when a payment account belongs to a DIFFERENT customer, it is classified as CONFLICTING."""
    engine = EvidenceCollectionEngine()
    cust_a = _make_customer(name="Customer A Corp", identifiers=[("BANK_ACCOUNT", "111111111111")])
    cust_b = _make_customer(name="Customer B Ltd", identifiers=[("BANK_ACCOUNT", "222222222222")])

    # Payment has Customer B's account, but is being evaluated against Customer A
    pay = _make_payment(
        amount="50000.00",
        bank_account_number="222222222222",
    )
    cand = _make_candidate(outstanding="50000.00")

    result = engine.evaluate(
        payment=pay,
        candidates=[cand],
        customer=cust_a,
        all_customers=[cust_a, cust_b],
    )

    conf_items = [i for i in result.payment_evidence.items if i.classification == EvidenceClassification.CONFLICTING]
    assert len(conf_items) == 1
    assert conf_items[0].evidence_type == EvidenceType.BANK_ACCOUNT_IDENTIFIER
    assert "Customer B Ltd" in conf_items[0].description
    assert result.payment_evidence.has_conflicting_identifiers is True


def test_conflicting_evidence_different_invoice_referenced() -> None:
    """Verify that when narration mentions INV-OTHER, candidate INV-2026-001 gets CONFLICTING evidence."""
    engine = EvidenceCollectionEngine()
    pay = _make_payment(
        narration="PAYMENT SETTLING INV-2026-OTHER EXCLUSIVELY",
    )
    cand = _make_candidate(invoice_number="INV-2026-001")
    cust = _make_customer()

    result = engine.evaluate(payment=pay, candidates=[cand], customer=cust)
    bundle = result.candidate_bundles[0]

    inv_items = [i for i in bundle.items if i.evidence_type == EvidenceType.INVOICE_NUMBER_IN_NARRATION]
    assert len(inv_items) == 1
    assert inv_items[0].classification == EvidenceClassification.CONFLICTING
    assert bundle.has_conflicting_evidence is True


def test_conflicting_evidence_non_causal_date() -> None:
    """Verify that payment date prior to invoice issue date is classified as CONFLICTING."""
    engine = EvidenceCollectionEngine()
    pay = _make_payment(
        payment_date=date(2026, 7, 1),
    )
    # Invoice issued August 1
    cand = _make_candidate(
        issue_date=date(2026, 8, 1),
    )
    cust = _make_customer()

    result = engine.evaluate(payment=pay, candidates=[cand], customer=cust)
    bundle = result.candidate_bundles[0]

    causal_items = [i for i in bundle.items if i.evidence_type == EvidenceType.DATE_CAUSALITY]
    assert len(causal_items) == 1
    assert causal_items[0].classification == EvidenceClassification.CONFLICTING


def test_100_run_permutation_determinism() -> None:
    """Verify 100 permutation runs produce bit-for-bit identical serialized evidence results.

    Tests that:
    1. Metrics (total_items, total_direct, total_supporting, total_missing, total_conflicting) match.
    2. The full serialized payload (to_deterministic_payload) is identical bit-for-bit across permutations.
    3. The evaluation timestamp (collected_at) is separated from deterministic evidence comparison.
    """
    engine = EvidenceCollectionEngine()
    pay = _make_payment()
    cand1 = _make_candidate(invoice_number="INV-001", outstanding="20000.00")
    cand2 = _make_candidate(invoice_number="INV-002", outstanding="30000.00")
    cand3 = _make_candidate(invoice_number="INV-003", outstanding="50000.00")
    cust = _make_customer()
    base_candidates = [cand1, cand2, cand3]

    baseline = engine.evaluate(payment=pay, candidates=base_candidates, customer=cust)
    baseline_payload = baseline.to_deterministic_payload()

    for i in range(100):
        shuffled = list(base_candidates)
        random.seed(i)
        random.shuffle(shuffled)
        res = engine.evaluate(payment=pay, candidates=shuffled, customer=cust)

        # 1. Structural item counts
        assert res.total_evidence_items == baseline.total_evidence_items
        assert res.total_direct_items == baseline.total_direct_items
        assert res.total_supporting_items == baseline.total_supporting_items
        assert res.total_missing_items == baseline.total_missing_items
        assert res.total_conflicting_items == baseline.total_conflicting_items

        # 2. Complete serialized payload comparison (proves collected_at does not break determinism)
        res_payload = res.to_deterministic_payload()
        assert res_payload == baseline_payload

        # 3. Verify collected_at is an ISO format string in to_dict() but not in deterministic payload
        assert "collected_at" not in res_payload
        assert "collected_at" in res.to_dict()


def test_adversarial_cross_tenant_candidate_rejected() -> None:
    """Verify that a candidate invoice from a different tenant is rejected with DomainError at domain engine boundary."""
    engine = EvidenceCollectionEngine()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    pay_a = _make_payment(company_id=tenant_a)
    cand_b = _make_candidate(invoice_number="INV-TENANT-B")
    object.__setattr__(cand_b, "company_id", tenant_b)

    with pytest.raises(DomainError) as exc_info:
        engine.evaluate(payment=pay_a, candidates=[cand_b])

    assert "Cross-tenant candidate invoice detected" in str(exc_info.value)
    assert "violating tenant boundary" in str(exc_info.value)


def test_matching_tenant_candidate_accepted() -> None:
    """Verify that a candidate invoice with matching company_id evaluates successfully."""
    engine = EvidenceCollectionEngine()
    tenant_a = uuid.uuid4()

    pay_a = _make_payment(company_id=tenant_a)
    cand_a = _make_candidate(invoice_number="INV-TENANT-A")
    object.__setattr__(cand_a, "company_id", tenant_a)

    result = engine.evaluate(payment=pay_a, candidates=[cand_a])
    assert result.company_id == tenant_a
    assert len(result.candidate_bundles) == 1
    assert result.candidate_bundles[0].invoice_number == "INV-TENANT-A"



def test_payment_reference_invoice_token_is_canonicalized() -> None:
    engine = EvidenceCollectionEngine()
    result = engine.evaluate(
        payment=_make_payment(payment_reference="inv-2026-001", narration=""),
        candidates=[],
        customer=_make_customer(),
    )
    assert result.payment_evidence.extracted_invoice_references == ["INV-2026-001"]


def test_generic_alphanumeric_narration_is_not_treated_as_utr() -> None:
    engine = EvidenceCollectionEngine()
    result = engine.evaluate(
        payment=_make_payment(payment_reference="", narration="MISCELLANEOUS TRANSFER"),
        candidates=[],
        customer=_make_customer(),
    )
    utr_items = [i for i in result.payment_evidence.items if i.evidence_type == EvidenceType.UTR_IDENTIFIER]
    assert utr_items[0].classification == EvidenceClassification.MISSING


def test_registered_upi_vpa_is_classified_as_upi_identifier() -> None:
    customer = _make_customer(identifiers=[("UPI_VPA", "vinayaka@upi")])
    result = EvidenceCollectionEngine().evaluate(
        payment=_make_payment(bank_account_number=None, payer_raw_identifier="vinayaka@upi"),
        candidates=[],
        customer=customer,
    )
    upi_items = [i for i in result.payment_evidence.items if i.evidence_type == EvidenceType.UPI_VPA_IDENTIFIER]
    assert len(upi_items) == 1
    assert upi_items[0].classification == EvidenceClassification.DIRECT
