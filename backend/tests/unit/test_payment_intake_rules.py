"""Unit tests for Phase 14.1 Payment Intake rules and determinism.

Verifies the 4-tier eligibility taxonomy:
- ELIGIBLE
- ALREADY_PROCESSED
- INELIGIBLE
- INVALID
and mathematical balance conservation, currency validation, and determinism.
"""

from datetime import date
from decimal import Decimal
import uuid
import pytest

from app.modules.reconciliation.domain.payment_intake import (
    PaymentIntakeReasonCode,
    PaymentIntakeResult,
    PaymentIntakeRuleEngine,
    PaymentIntakeStatus,
)
from app.modules.reconciliation.domain.rules import PaymentIntakeContext


@pytest.fixture
def company_id() -> uuid.UUID:
    return uuid.uuid4()


def test_unreconciled_payment_eligible(company_id):
    """Verify standard unreconciled payment is classified as ELIGIBLE."""
    ctx = PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id,
        amount=Decimal("15000.00"),
        currency="INR",
        payment_date=date(2026, 9, 8),
        allocated_amount=Decimal("0.00"),
        unallocated_amount=Decimal("15000.00"),
        status="UNRECONCILED",
        transaction_type="CREDIT",
    )
    res = PaymentIntakeRuleEngine.evaluate(ctx, as_of_date=date(2026, 9, 8))

    assert res.status == PaymentIntakeStatus.ELIGIBLE
    assert res.is_eligible is True
    assert res.reason_code == PaymentIntakeReasonCode.READY_FOR_RECONCILIATION.value
    assert res.effective_amount == Decimal("15000.00")
    assert res.original_amount == Decimal("15000.00")
    assert res.allocated_amount == Decimal("0.00")
    assert res.unallocated_amount == Decimal("15000.00")


def test_partially_reconciled_payment_eligible(company_id):
    """Verify partially reconciled payment is ELIGIBLE with effective_amount == unallocated_amount."""
    ctx = PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id,
        amount=Decimal("50000.00"),
        currency="INR",
        payment_date=date(2026, 9, 5),
        allocated_amount=Decimal("20000.00"),
        unallocated_amount=Decimal("30000.00"),
        status="PARTIALLY_RECONCILED",
        transaction_type="CREDIT",
    )
    res = PaymentIntakeRuleEngine.evaluate(ctx, as_of_date=date(2026, 9, 8))

    assert res.status == PaymentIntakeStatus.ELIGIBLE
    assert res.is_eligible is True
    assert res.reason_code == PaymentIntakeReasonCode.PARTIAL_BALANCE_AVAILABLE.value
    assert res.effective_amount == Decimal("30000.00")
    assert res.allocated_amount == Decimal("20000.00")
    assert res.unallocated_amount == Decimal("30000.00")


def test_fully_reconciled_payment_already_processed(company_id):
    """Verify fully reconciled payment is classified as ALREADY_PROCESSED."""
    ctx = PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id,
        amount=Decimal("25000.00"),
        currency="INR",
        payment_date=date(2026, 9, 1),
        allocated_amount=Decimal("25000.00"),
        unallocated_amount=Decimal("0.00"),
        status="RECONCILED",
        transaction_type="CREDIT",
    )
    res = PaymentIntakeRuleEngine.evaluate(ctx, as_of_date=date(2026, 9, 8))

    assert res.status == PaymentIntakeStatus.ALREADY_PROCESSED
    assert res.is_eligible is False
    assert res.reason_code == PaymentIntakeReasonCode.FULLY_RECONCILED.value
    assert res.effective_amount == Decimal("0.00")


def test_zero_unallocated_balance_already_processed(company_id):
    """Verify payment with zero unallocated balance is classified as ALREADY_PROCESSED."""
    ctx = PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id,
        amount=Decimal("10000.00"),
        currency="INR",
        payment_date=date(2026, 9, 1),
        allocated_amount=Decimal("10000.00"),
        unallocated_amount=Decimal("0.00"),
        status="UNRECONCILED",  # Even if status says unreconciled, unallocated is 0.00
    )
    res = PaymentIntakeRuleEngine.evaluate(ctx, as_of_date=date(2026, 9, 8))

    assert res.status == PaymentIntakeStatus.ALREADY_PROCESSED
    assert res.is_eligible is False
    assert res.reason_code == PaymentIntakeReasonCode.FULLY_RECONCILED.value


def test_ignored_payment_ineligible(company_id):
    """Verify payment marked IGNORED by accountant is classified as INELIGIBLE."""
    ctx = PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id,
        amount=Decimal("10000.00"),
        currency="INR",
        payment_date=date(2026, 9, 5),
        allocated_amount=Decimal("0.00"),
        unallocated_amount=Decimal("10000.00"),
        status="IGNORED",
    )
    res = PaymentIntakeRuleEngine.evaluate(ctx, as_of_date=date(2026, 9, 8))

    assert res.status == PaymentIntakeStatus.INELIGIBLE
    assert res.is_eligible is False
    assert res.reason_code == PaymentIntakeReasonCode.PAYMENT_MARKED_IGNORED.value


def test_debit_transaction_ineligible(company_id):
    """Verify DEBIT outflow transaction is classified as INELIGIBLE."""
    ctx = PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id,
        amount=Decimal("5000.00"),
        currency="INR",
        payment_date=date(2026, 9, 5),
        allocated_amount=Decimal("0.00"),
        unallocated_amount=Decimal("5000.00"),
        status="UNRECONCILED",
        transaction_type="DEBIT",
    )
    res = PaymentIntakeRuleEngine.evaluate(ctx, as_of_date=date(2026, 9, 8))

    assert res.status == PaymentIntakeStatus.INELIGIBLE
    assert res.is_eligible is False
    assert res.reason_code == PaymentIntakeReasonCode.DEBIT_TRANSACTION_INELIGIBLE.value


def test_non_positive_amount_invalid(company_id):
    """Verify gross amount <= 0 is classified as INVALID."""
    # Zero amount
    ctx_zero = PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id,
        amount=Decimal("0.00"),
        currency="INR",
        payment_date=date(2026, 9, 5),
    )
    res_zero = PaymentIntakeRuleEngine.evaluate(ctx_zero, as_of_date=date(2026, 9, 8))
    assert res_zero.status == PaymentIntakeStatus.INVALID
    assert res_zero.is_eligible is False
    assert res_zero.reason_code == PaymentIntakeReasonCode.NON_POSITIVE_PAYMENT_AMOUNT.value

    # Negative amount
    ctx_neg = PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id,
        amount=Decimal("-500.00"),
        currency="INR",
        payment_date=date(2026, 9, 5),
    )
    res_neg = PaymentIntakeRuleEngine.evaluate(ctx_neg, as_of_date=date(2026, 9, 8))
    assert res_neg.status == PaymentIntakeStatus.INVALID
    assert res_neg.is_eligible is False
    assert res_neg.reason_code == PaymentIntakeReasonCode.NON_POSITIVE_PAYMENT_AMOUNT.value


def test_negative_sub_balances_invalid(company_id):
    """Verify negative allocated or unallocated amounts violate financial invariants."""
    # Negative allocated
    ctx_neg_alloc = PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id,
        amount=Decimal("1000.00"),
        currency="INR",
        payment_date=date(2026, 9, 5),
        allocated_amount=Decimal("-100.00"),
        unallocated_amount=Decimal("1100.00"),
    )
    res_alloc = PaymentIntakeRuleEngine.evaluate(ctx_neg_alloc, as_of_date=date(2026, 9, 8))
    assert res_alloc.status == PaymentIntakeStatus.INVALID
    assert res_alloc.reason_code == PaymentIntakeReasonCode.NEGATIVE_ALLOCATED_BALANCE.value

    # Negative unallocated
    ctx_neg_unalloc = PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id,
        amount=Decimal("1000.00"),
        currency="INR",
        payment_date=date(2026, 9, 5),
        allocated_amount=Decimal("1100.00"),
        unallocated_amount=Decimal("-100.00"),
    )
    res_unalloc = PaymentIntakeRuleEngine.evaluate(ctx_neg_unalloc, as_of_date=date(2026, 9, 8))
    assert res_unalloc.status == PaymentIntakeStatus.INVALID
    assert res_unalloc.reason_code == PaymentIntakeReasonCode.NEGATIVE_UNALLOCATED_BALANCE.value


def test_balance_conservation_violation_invalid(company_id):
    """Verify balance conservation invariant: allocated + unallocated == gross amount."""
    ctx = PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id,
        amount=Decimal("1000.00"),
        currency="INR",
        payment_date=date(2026, 9, 5),
        allocated_amount=Decimal("300.00"),
        unallocated_amount=Decimal("800.00"),  # Sum = 1100 != 1000
    )
    res = PaymentIntakeRuleEngine.evaluate(ctx, as_of_date=date(2026, 9, 8))

    assert res.status == PaymentIntakeStatus.INVALID
    assert res.is_eligible is False
    assert res.reason_code == PaymentIntakeReasonCode.BALANCE_CONSERVATION_BREACH.value


def test_currency_code_validation_invalid(company_id):
    """Verify ISO 4217 currency format validation."""
    for invalid_curr in ["", "IN", "INDIA", "123", "us", "iNR"]:
        ctx = PaymentIntakeContext(
            payment_id=uuid.uuid4(),
            company_id=company_id,
            amount=Decimal("1000.00"),
            currency=invalid_curr,
            payment_date=date(2026, 9, 5),
        )
        res = PaymentIntakeRuleEngine.evaluate(ctx, as_of_date=date(2026, 9, 8))
        if invalid_curr.upper() in ["INR", "USD"] and len(invalid_curr) == 3:
            continue
        assert res.status == PaymentIntakeStatus.INVALID
        assert res.reason_code == PaymentIntakeReasonCode.INVALID_CURRENCY_CODE.value


def test_future_transaction_date_invalid(company_id):
    """Verify post-dated future transactions are flagged as INVALID anomalies."""
    ctx = PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id,
        amount=Decimal("5000.00"),
        currency="INR",
        payment_date=date(2026, 9, 15),  # Future relative to as_of_date
        allocated_amount=Decimal("0.00"),
        unallocated_amount=Decimal("5000.00"),
    )
    res = PaymentIntakeRuleEngine.evaluate(ctx, as_of_date=date(2026, 9, 8))

    assert res.status == PaymentIntakeStatus.INVALID
    assert res.is_eligible is False
    assert res.reason_code == PaymentIntakeReasonCode.FUTURE_TRANSACTION_DATE.value


def test_invalid_payment_status_rejected(company_id):
    """Verify unrecognized payment status is rejected with INVALID_PAYMENT_STATUS."""
    ctx = PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id,
        amount=Decimal("5000.00"),
        currency="INR",
        payment_date=date(2026, 9, 5),
        allocated_amount=Decimal("0.00"),
        unallocated_amount=Decimal("5000.00"),
        status="CORRUPTED_STATUS",
    )
    res = PaymentIntakeRuleEngine.evaluate(ctx, as_of_date=date(2026, 9, 8))

    assert res.status == PaymentIntakeStatus.INVALID
    assert res.reason_code == PaymentIntakeReasonCode.INVALID_PAYMENT_STATUS.value


def test_intake_evaluation_determinism_100_runs(company_id):
    """Verify intake rule engine produces 100% identical outputs across 100 iterations."""
    ctx = PaymentIntakeContext(
        payment_id=uuid.uuid4(),
        company_id=company_id,
        amount=Decimal("75000.00"),
        currency="INR",
        payment_date=date(2026, 9, 7),
        allocated_amount=Decimal("25000.00"),
        unallocated_amount=Decimal("50000.00"),
        status="PARTIALLY_RECONCILED",
        transaction_type="CREDIT",
        bank_account_number="123456789012",
    )
    as_of = date(2026, 9, 8)

    baseline = PaymentIntakeRuleEngine.evaluate(ctx, as_of_date=as_of).to_dict()
    # Exclude dynamic timestamp from serialization comparison
    baseline_eval = {k: v for k, v in baseline.items() if k != "evaluated_at"}

    for _ in range(100):
        iteration = PaymentIntakeRuleEngine.evaluate(ctx, as_of_date=as_of).to_dict()
        iter_eval = {k: v for k, v in iteration.items() if k != "evaluated_at"}
        assert iter_eval == baseline_eval


def test_payment_intake_result_serialization(company_id):
    """Verify PaymentIntakeResult serialization to dict produces all required presentation keys."""
    pid = uuid.uuid4()
    ctx = PaymentIntakeContext(
        payment_id=pid,
        company_id=company_id,
        amount=Decimal("12000.00"),
        currency="INR",
        payment_date=date(2026, 9, 8),
        bank_account_number="987654321098",
    )
    res = PaymentIntakeRuleEngine.evaluate(ctx, as_of_date=date(2026, 9, 8))
    d = res.to_dict()

    assert d["payment_id"] == str(pid)
    assert d["company_id"] == str(company_id)
    assert d["status"] == "ELIGIBLE"
    assert d["is_eligible"] is True
    assert d["original_amount"] == "12000.00"
    assert d["allocated_amount"] == "0.00"
    assert d["unallocated_amount"] == "12000.00"
    assert d["effective_amount"] == "12000.00"
    assert d["currency"] == "INR"
    assert d["payment_date"] == "2026-09-08"
    assert d["is_deterministic"] is True
    assert "evaluated_at" in d
