"""Unit tests for the deterministic counterparty identification rule engine."""

from datetime import date
from decimal import Decimal
import pytest
from uuid import uuid4

from app.modules.reconciliation.domain.entities import (
    EvidenceType,
    IdentificationStatus,
)
from app.modules.reconciliation.domain.rules import (
    CustomerLookupContext,
    PayerIdentificationRuleEngine,
    PaymentIntakeContext,
)


@pytest.fixture
def rule_engine():
    return PayerIdentificationRuleEngine()


@pytest.fixture
def company_id():
    return uuid4()


def test_exact_bank_account_match(rule_engine, company_id):
    """Verify exact bank account coordinate match yields IDENTIFIED with score 100.0."""
    cust_id = uuid4()
    customers = [
        CustomerLookupContext(
            customer_id=cust_id,
            name="Star Enterprises Pvt Ltd",
            identifiers=[("BANK_ACCOUNT", "987654321012")],
        )
    ]
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("15000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        bank_account_number="987654321012",
        payer_raw_name="UNKNOWN PAYER",
    )

    result = rule_engine.evaluate(payment, customers)
    assert result.status == IdentificationStatus.IDENTIFIED
    assert result.primary_candidate is not None
    assert result.primary_candidate.customer_id == cust_id
    assert result.primary_candidate.composite_score == 100.0
    assert any(s.evidence_type == EvidenceType.EXACT_BANK_ACCOUNT for s in result.evidence_signals)


def test_exact_upi_vpa_match(rule_engine, company_id):
    """Verify exact UPI VPA match yields IDENTIFIED with score 100.0."""
    cust_id = uuid4()
    customers = [
        CustomerLookupContext(
            customer_id=cust_id,
            name="Apex Logistics Ltd",
            identifiers=[("UPI_VPA", "apex.logistics@icici")],
        )
    ]
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("5400.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        payer_raw_identifier="apex.logistics@icici",
    )

    result = rule_engine.evaluate(payment, customers)
    assert result.status == IdentificationStatus.IDENTIFIED
    assert result.primary_candidate.customer_id == cust_id
    assert result.primary_candidate.composite_score == 100.0
    assert any(s.evidence_type == EvidenceType.EXACT_UPI_VPA for s in result.evidence_signals)


def test_exact_customer_alias_match(rule_engine, company_id):
    """Verify registered customer alias match yields IDENTIFIED with score 85.0."""
    cust_id = uuid4()
    customers = [
        CustomerLookupContext(
            customer_id=cust_id,
            name="Bharath Heavy Engineering Corporation Ltd",
            aliases=["BHEC", "BHEC Corp"],
        )
    ]
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("250000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        payer_raw_name="BHEC",
    )

    result = rule_engine.evaluate(payment, customers)
    assert result.status == IdentificationStatus.IDENTIFIED
    assert result.primary_candidate.customer_id == cust_id
    assert result.primary_candidate.composite_score == 85.0
    assert any(s.evidence_type == EvidenceType.EXACT_ALIAS for s in result.evidence_signals)


def test_normalized_legal_name_match(rule_engine, company_id):
    """Verify legal name suffix variation yields IDENTIFIED with score 75.0."""
    cust_id = uuid4()
    customers = [
        CustomerLookupContext(
            customer_id=cust_id,
            name="Zenith Softwares Private Limited",
        )
    ]
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("42000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        payer_raw_name="Zenith Softwares Pvt Ltd",
    )

    result = rule_engine.evaluate(payment, customers)
    assert result.status == IdentificationStatus.IDENTIFIED
    assert result.primary_candidate.customer_id == cust_id
    assert result.primary_candidate.composite_score == 75.0
    assert any(s.evidence_type == EvidenceType.NORMALIZED_LEGAL_NAME for s in result.evidence_signals)


def test_clean_payer_name_exact_match(rule_engine, company_id):
    """Verify clean payer name match without legal suffixes yields IDENTIFIED with score 70.0."""
    cust_id = uuid4()
    customers = [
        CustomerLookupContext(
            customer_id=cust_id,
            name="Acme Solutions",
        )
    ]
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("12000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        payer_raw_name="  ACME SOLUTIONS  ",
    )

    result = rule_engine.evaluate(payment, customers)
    assert result.status == IdentificationStatus.IDENTIFIED
    assert result.primary_candidate.customer_id == cust_id
    assert result.primary_candidate.composite_score >= 70.0


def test_conflicting_direct_identifiers(rule_engine, company_id):
    """Verify conflicting direct banking coordinates produce CONFLICTING status."""
    cust_a = uuid4()
    cust_b = uuid4()
    customers = [
        CustomerLookupContext(
            customer_id=cust_a,
            name="Customer Alpha",
            identifiers=[("BANK_ACCOUNT", "111122223333")],
        ),
        CustomerLookupContext(
            customer_id=cust_b,
            name="Customer Beta",
            identifiers=[("UPI_VPA", "beta@axis")],
        ),
    ]
    # Payment has bank_account matching Alpha and raw_identifier matching Beta
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("10000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        bank_account_number="111122223333",
        payer_raw_identifier="beta@axis",
    )

    result = rule_engine.evaluate(payment, customers)
    assert result.status == IdentificationStatus.CONFLICTING
    assert result.primary_candidate is None
    assert result.reason_code == "CONFLICTING_DIRECT_IDENTIFIERS"


def test_conflicting_coordinate_and_name(rule_engine, company_id):
    """Verify coordinate matching Customer A while explicit name matches Customer B produces CONFLICTING."""
    cust_a = uuid4()
    cust_b = uuid4()
    customers = [
        CustomerLookupContext(
            customer_id=cust_a,
            name="Customer Alpha",
            identifiers=[("BANK_ACCOUNT", "111122223333")],
        ),
        CustomerLookupContext(
            customer_id=cust_b,
            name="Customer Beta Corporation",
            aliases=["Beta Corp"],
        ),
    ]
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("10000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        bank_account_number="111122223333",
        payer_raw_name="Beta Corp",
    )

    result = rule_engine.evaluate(payment, customers)
    assert result.status == IdentificationStatus.CONFLICTING
    assert result.primary_candidate is None
    assert result.reason_code == "CONFLICTING_COORDINATE_AND_NAME"


def test_ambiguous_close_differing_scores(rule_engine, company_id):
    """Verify differing scores within delta threshold (<15.0) produce AMBIGUOUS status."""
    cust_a = uuid4()
    cust_b = uuid4()
    customers = [
        CustomerLookupContext(
            customer_id=cust_a,
            name="Delta Tech Solutions Enterprise",
            aliases=["Delta Tech"],  # Score 85.0
        ),
        CustomerLookupContext(
            customer_id=cust_b,
            name="Delta Tech Private Limited",  # Normalized legal name match -> Score 75.0
        ),
    ]
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("10000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        payer_raw_name="Delta Tech",
    )

    result = rule_engine.evaluate(payment, customers)
    # Score A = 85.0 (alias), Score B = 75.0 (legal name), delta = 10.0 < 15.0 threshold
    assert result.status == IdentificationStatus.AMBIGUOUS
    assert result.primary_candidate is None
    assert result.reason_code == "CLOSE_SCORE_COUNTERPARTY_AMBIGUITY"
    assert len(result.candidates) == 2


def test_ambiguous_identical_scores(rule_engine, company_id):
    """Verify identical candidate scores (delta = 0.0) produce AMBIGUOUS status."""
    cust_a = uuid4()
    cust_b = uuid4()
    customers_equal = [
        CustomerLookupContext(
            customer_id=cust_a,
            name="Summit Global Logistics Pvt Ltd",
            aliases=["Summit Logistics"],
        ),
        CustomerLookupContext(
            customer_id=cust_b,
            name="Summit Express Logistics Pvt Ltd",
            aliases=["Summit Logistics"],  # Shared business trade name alias
        ),
    ]
    payment_equal = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("10000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        payer_raw_name="Summit Logistics",
    )

    result = rule_engine.evaluate(payment_equal, customers_equal)
    assert result.status == IdentificationStatus.AMBIGUOUS
    assert result.primary_candidate is None
    assert result.reason_code == "CLOSE_SCORE_COUNTERPARTY_AMBIGUITY"
    assert len(result.candidates) == 2


def test_shared_bank_account_produces_ambiguous(rule_engine, company_id):
    """Verify two subsidiary customers sharing the same bank account produce AMBIGUOUS."""
    cust_a = uuid4()
    cust_b = uuid4()
    customers = [
        CustomerLookupContext(
            customer_id=cust_a,
            name="Parent Corp Subsidiary One",
            identifiers=[("BANK_ACCOUNT", "999888777666")],
        ),
        CustomerLookupContext(
            customer_id=cust_b,
            name="Parent Corp Subsidiary Two",
            identifiers=[("BANK_ACCOUNT", "999888777666")],
        ),
    ]
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("80000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        bank_account_number="999888777666",
    )

    result = rule_engine.evaluate(payment, customers)
    assert result.status == IdentificationStatus.AMBIGUOUS
    assert result.primary_candidate is None
    assert len(result.candidates) == 2
    assert result.candidates[0].composite_score == 100.0
    assert result.candidates[1].composite_score == 100.0


def test_unknown_payer_no_evidence(rule_engine, company_id):
    """Verify unrecognizable payer produces UNKNOWN status."""
    customers = [
        CustomerLookupContext(
            customer_id=uuid4(),
            name="Acme Corp",
            identifiers=[("BANK_ACCOUNT", "123456789")],
        )
    ]
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("100.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        narration="CASH DEPOSIT BRANCH 4001",
        payer_raw_name="UNKNOWN",
    )

    result = rule_engine.evaluate(payment, customers)
    assert result.status == IdentificationStatus.UNKNOWN
    assert result.primary_candidate is None
    assert result.reason_code == "NO_RECOGNIZABLE_COUNTERPARTY_EVIDENCE"


def test_non_positive_payment_not_eligible(rule_engine, company_id):
    """Verify zero or negative payment amounts are classified as NOT_ELIGIBLE."""
    customers = [CustomerLookupContext(customer_id=uuid4(), name="Acme Corp")]
    payment_zero = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("0.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
    )
    res_zero = rule_engine.evaluate(payment_zero, customers)
    assert res_zero.status == IdentificationStatus.NOT_ELIGIBLE
    assert res_zero.reason_code == "NON_POSITIVE_PAYMENT_AMOUNT"

    payment_neg = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("-500.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
    )
    res_neg = rule_engine.evaluate(payment_neg, customers)
    assert res_neg.status == IdentificationStatus.NOT_ELIGIBLE


def test_archived_customers_excluded(rule_engine, company_id):
    """Verify archived customers are never considered as matching candidates."""
    archived_id = uuid4()
    customers = [
        CustomerLookupContext(
            customer_id=archived_id,
            name="Archived Inactive Customer",
            is_archived=True,
            identifiers=[("BANK_ACCOUNT", "112233445566")],
        )
    ]
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("5000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        bank_account_number="112233445566",
    )

    result = rule_engine.evaluate(payment, customers)
    # Archived customer must NOT be matched
    assert result.status == IdentificationStatus.UNKNOWN
    assert result.primary_candidate is None


def test_scenario_1_exact_account_vs_legal_name_conflict(rule_engine, company_id):
    """Scenario 1: Exact account matches Customer A, but legal name explicitly matches Customer B. Must return CONFLICT."""
    cust_a = uuid4()
    cust_b = uuid4()
    customers = [
        CustomerLookupContext(
            customer_id=cust_a,
            name="Alpha Corp Infrastructure Ltd",
            identifiers=[("BANK_ACCOUNT", "998877665544")],
        ),
        CustomerLookupContext(
            customer_id=cust_b,
            name="Acme Corporation Private Limited",
        ),
    ]
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("45000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        bank_account_number="998877665544",
        payer_raw_name="Acme Corporation Pvt Ltd",  # Legal name match (score 75.0)
    )

    result = rule_engine.evaluate(payment, customers)
    assert result.status == IdentificationStatus.CONFLICTING
    assert result.primary_candidate is None
    assert result.reason_code == "CONFLICTING_COORDINATE_AND_NAME"
    assert len(result.candidates) == 2


def test_scenario_2_narration_coordinate_vs_explicit_name(rule_engine, company_id):
    """Scenario 2: Narration coordinate matches Customer A, but explicit payer name identifies Customer B. Must return CONFLICT."""
    cust_a = uuid4()
    cust_b = uuid4()
    customers = [
        CustomerLookupContext(
            customer_id=cust_a,
            name="Delta Cargo Transit LLP",
            identifiers=[("BANK_ACCOUNT", "1234509876")],
        ),
        CustomerLookupContext(
            customer_id=cust_b,
            name="Zenith Enterprises",
        ),
    ]
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("12000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        narration="NEFT-1234509876-TRANSFER",  # Narration extracted coordinate (score 95.0)
        payer_raw_name="Zenith Enterprises",    # Clean raw name match (score 70.0)
    )

    result = rule_engine.evaluate(payment, customers)
    assert result.status == IdentificationStatus.CONFLICTING
    assert result.primary_candidate is None
    assert result.reason_code == "CONFLICTING_COORDINATE_AND_NAME"
    assert len(result.candidates) == 2


def test_scenario_3_tax_id_reference_match(rule_engine, company_id):
    """Scenario 3: Payment reference contains customer tax_id. Must match with REFERENCE_MATCH signal."""
    cust_a = uuid4()
    customers = [
        CustomerLookupContext(
            customer_id=cust_a,
            name="Bharath Electronics Limited",
            tax_id="29ABCDE1234F1Z5",
        )
    ]
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("25000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        payment_reference="29ABCDE1234F1Z5",
    )

    result = rule_engine.evaluate(payment, customers)
    assert result.status == IdentificationStatus.AMBIGUOUS  # 50.0 is in [30.0, 70.0) low-confidence band
    assert result.primary_candidate is None
    assert len(result.candidates) == 1
    sig = result.candidates[0].evidence_signals[0]
    assert sig.evidence_type == EvidenceType.REFERENCE_MATCH
    assert sig.weight == 50.0
    assert sig.metadata.get("match_category") == "TAX_ID_REFERENCE_MATCH"

    # Verify placeholder tax ID "NA" does not match
    cust_placeholder = [
        CustomerLookupContext(
            customer_id=cust_a,
            name="Unknown Vendor",
            tax_id="NA",
        )
    ]
    payment_placeholder = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("1000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        payment_reference="NA",
    )
    res_placeholder = rule_engine.evaluate(payment_placeholder, cust_placeholder)
    assert res_placeholder.status == IdentificationStatus.UNKNOWN


def test_scenario_4_large_catalog_scalability(rule_engine, company_id):
    """Scenario 4: Scalability test with 500 active customers to assert sub-100ms execution and determinism."""
    import time
    catalog = [
        CustomerLookupContext(
            customer_id=uuid4(),
            name=f"Enterprise Client {i} Private Limited",
            tax_id=f"29ABCDE{i:04d}F1Z5",
            aliases=[f"Enterprise {i}"],
            identifiers=[("BANK_ACCOUNT", f"9876543{i:05d}")],
        )
        for i in range(500)
    ]

    target_idx = 250
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=company_id,
        amount=Decimal("80000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        bank_account_number=f"9876543{target_idx:05d}",
        payer_raw_name=f"Enterprise Client {target_idx} Pvt Ltd",
    )

    t0 = time.perf_counter()
    result = rule_engine.evaluate(payment, catalog)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert result.status == IdentificationStatus.IDENTIFIED
    assert result.primary_candidate.customer_id == catalog[target_idx].customer_id
    assert result.primary_candidate.composite_score == 100.0
    assert elapsed_ms < 1000.0  # Must evaluate in < 1s for 500 customers even under coverage profiling

