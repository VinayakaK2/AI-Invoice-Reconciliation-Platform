"""Unit tests verifying determinism, repeatability, and order invariance of customer identification."""

from datetime import date
from decimal import Decimal
import random
from uuid import uuid4

from app.modules.reconciliation.domain.entities import IdentificationStatus
from app.modules.reconciliation.domain.rules import (
    CustomerLookupContext,
    PayerIdentificationRuleEngine,
    PaymentIntakeContext,
)


def test_repeated_evaluation_determinism():
    """Verify that 100 repeated evaluations on identical data produce 100% identical results."""
    rule_engine = PayerIdentificationRuleEngine()
    comp_id = uuid4()
    cust_id = uuid4()

    customers = [
        CustomerLookupContext(
            customer_id=cust_id,
            name="Alpha Delta Technologies Private Limited",
            aliases=["Alpha Delta Tech"],
            identifiers=[("BANK_ACCOUNT", "987654321098"), ("UPI_VPA", "alphadelta@icici")],
        )
    ]

    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=comp_id,
        amount=Decimal("12500.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        bank_account_number="987654321098",
        payer_raw_name="Alpha Delta Tech",
        narration="NEFT CR-009988-ALPHADELTA",
    )

    baseline = rule_engine.evaluate(payment, customers)

    for _ in range(100):
        iteration_result = rule_engine.evaluate(payment, customers)
        assert iteration_result.status == baseline.status
        assert iteration_result.total_evidence_score == baseline.total_evidence_score
        assert iteration_result.reason_code == baseline.reason_code
        assert iteration_result.primary_candidate.customer_id == baseline.primary_candidate.customer_id
        assert iteration_result.primary_candidate.composite_score == baseline.primary_candidate.composite_score
        assert len(iteration_result.evidence_signals) == len(baseline.evidence_signals)


def test_candidate_tie_breaking_alphabetical_determinism():
    """Verify that equal-score candidates are ordered deterministically by customer name."""
    rule_engine = PayerIdentificationRuleEngine()
    comp_id = uuid4()

    # Three customers matching the same trade alias
    c_zeta = CustomerLookupContext(
        customer_id=uuid4(),
        name="Zeta Logistics Pvt Ltd",
        aliases=["Shared Trade Name"],
    )
    c_alpha = CustomerLookupContext(
        customer_id=uuid4(),
        name="Alpha Logistics Pvt Ltd",
        aliases=["Shared Trade Name"],
    )
    c_gamma = CustomerLookupContext(
        customer_id=uuid4(),
        name="Gamma Logistics Pvt Ltd",
        aliases=["Shared Trade Name"],
    )

    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=comp_id,
        amount=Decimal("50000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        payer_raw_name="Shared Trade Name",
    )

    result = rule_engine.evaluate(payment, [c_zeta, c_alpha, c_gamma])
    assert result.status == IdentificationStatus.AMBIGUOUS
    names = [c.customer_name for c in result.candidates]
    # Equal score tie-breaking must sort alphabetically ASC
    assert names == [
        "Alpha Logistics Pvt Ltd",
        "Gamma Logistics Pvt Ltd",
        "Zeta Logistics Pvt Ltd",
    ]


def test_catalog_permutation_invariance():
    """Verify customer catalog ordering does not affect outcome or candidate ranking."""
    rule_engine = PayerIdentificationRuleEngine()
    comp_id = uuid4()

    c1 = CustomerLookupContext(
        customer_id=uuid4(),
        name="Apex Industrial Suppliers Pvt Ltd",
        aliases=["Apex Suppliers"],
        identifiers=[("BANK_ACCOUNT", "111100002222")],
    )
    c2 = CustomerLookupContext(
        customer_id=uuid4(),
        name="Apex Solutions",
        aliases=["Apex Sol"],
    )
    c3 = CustomerLookupContext(
        customer_id=uuid4(),
        name="Apex Global Exports",
    )

    catalog = [c1, c2, c3]
    payment = PaymentIntakeContext(
        payment_id=uuid4(),
        company_id=comp_id,
        amount=Decimal("75000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        bank_account_number="111100002222",
        payer_raw_name="Apex Suppliers",
    )

    baseline = rule_engine.evaluate(payment, catalog)

    for _ in range(10):
        shuffled = list(catalog)
        random.shuffle(shuffled)
        shuffled_res = rule_engine.evaluate(payment, shuffled)
        assert shuffled_res.status == baseline.status
        assert shuffled_res.primary_candidate.customer_id == baseline.primary_candidate.customer_id
        assert [c.customer_id for c in shuffled_res.candidates] == [c.customer_id for c in baseline.candidates]
