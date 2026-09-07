"""Unit tests for Payment module domain entities, value objects, and invariants."""

from datetime import date
from decimal import Decimal
import pytest
from uuid import uuid4

from app.modules.payment.domain.entities import (
    BankTransaction,
    ImportBatch,
    ImportBatchStatus,
    Payment,
    PaymentSource,
    PaymentStatus,
    TransactionFingerprint,
    TransactionType,
)
from app.shared.domain.money import Money
from app.shared.exceptions import DomainError, FinancialInvariantError, ValidationError


def test_payment_creation_valid():
    """Verify standard valid Payment entity creation with balance conservation."""
    company_id = uuid4()
    payment = Payment(
        id=uuid4(),
        company_id=company_id,
        transaction_date=date(2026, 3, 15),
        amount=Money(Decimal("1500.00"), "INR"),
        allocated_amount=Money(Decimal("0.00"), "INR"),
        unallocated_amount=Money(Decimal("1500.00"), "INR"),
        currency="INR",
        narration="NEFT from Acme Corp",
        reference_number="NEFT12345678",
    )
    assert payment.amount == Money(Decimal("1500.00"), "INR")
    assert payment.allocated_amount.is_zero()
    assert payment.unallocated_amount == Money(Decimal("1500.00"), "INR")
    assert payment.status == PaymentStatus.UNRECONCILED


def test_payment_creation_zero_or_negative_amount_fails():
    """Assert payment instantiation rejects zero or negative amounts."""
    company_id = uuid4()
    with pytest.raises(FinancialInvariantError, match="strictly positive"):
        Payment(
            id=uuid4(),
            company_id=company_id,
            transaction_date=date(2026, 3, 15),
            amount=Money(Decimal("0.00"), "INR"),
            allocated_amount=Money(Decimal("0.00"), "INR"),
            unallocated_amount=Money(Decimal("0.00"), "INR"),
            currency="INR",
        )

    with pytest.raises(FinancialInvariantError, match="strictly positive"):
        Payment(
            id=uuid4(),
            company_id=company_id,
            transaction_date=date(2026, 3, 15),
            amount=Money(Decimal("-50.00"), "INR"),
            allocated_amount=Money(Decimal("0.00"), "INR"),
            unallocated_amount=Money(Decimal("-50.00"), "INR"),
            currency="INR",
        )


def test_payment_creation_balance_conservation_violation():
    """Assert payment instantiation rejects broken balance conservation."""
    company_id = uuid4()
    with pytest.raises(FinancialInvariantError, match="balance conservation violated"):
        Payment(
            id=uuid4(),
            company_id=company_id,
            transaction_date=date(2026, 3, 15),
            amount=Money(Decimal("1000.00"), "INR"),
            allocated_amount=Money(Decimal("200.00"), "INR"),
            unallocated_amount=Money(Decimal("700.00"), "INR"),  # Sum is 900 != 1000
            currency="INR",
        )


def test_payment_currency_mismatch_fails():
    """Assert payment rejects mismatch between entity currency and Money components."""
    company_id = uuid4()
    with pytest.raises(FinancialInvariantError, match="Currency mismatch on payment"):
        Payment(
            id=uuid4(),
            company_id=company_id,
            transaction_date=date(2026, 3, 15),
            amount=Money(Decimal("1000.00"), "USD"),
            allocated_amount=Money(Decimal("0.00"), "USD"),
            unallocated_amount=Money(Decimal("1000.00"), "USD"),
            currency="INR",  # Mismatch with USD
        )


def test_payment_allocation_lifecycle():
    """Verify payment allocation transitions through PARTIALLY_RECONCILED and RECONCILED."""
    payment = Payment(
        id=uuid4(),
        company_id=uuid4(),
        transaction_date=date(2026, 3, 15),
        amount=Money(Decimal("1000.00"), "INR"),
        allocated_amount=Money(Decimal("0.00"), "INR"),
        unallocated_amount=Money(Decimal("1000.00"), "INR"),
        currency="INR",
    )

    # 1. Partial allocation
    payment.record_allocation(Money(Decimal("400.00"), "INR"))
    assert payment.allocated_amount == Money(Decimal("400.00"), "INR")
    assert payment.unallocated_amount == Money(Decimal("600.00"), "INR")
    assert payment.status == PaymentStatus.PARTIALLY_RECONCILED

    # 2. Final allocation
    payment.record_allocation(Money(Decimal("600.00"), "INR"))
    assert payment.allocated_amount == Money(Decimal("1000.00"), "INR")
    assert payment.unallocated_amount.is_zero()
    assert payment.status == PaymentStatus.RECONCILED


def test_payment_allocation_exceeding_balance_fails():
    """Verify allocation greater than remaining unallocated balance raises error."""
    payment = Payment(
        id=uuid4(),
        company_id=uuid4(),
        transaction_date=date(2026, 3, 15),
        amount=Money(Decimal("500.00"), "INR"),
        allocated_amount=Money(Decimal("0.00"), "INR"),
        unallocated_amount=Money(Decimal("500.00"), "INR"),
        currency="INR",
    )
    with pytest.raises(FinancialInvariantError, match="exceeds unallocated balance"):
        payment.record_allocation(Money(Decimal("500.01"), "INR"))


def test_payment_unallocate_lifecycle():
    """Verify reverting allocation updates balances and transitions status correctly."""
    payment = Payment(
        id=uuid4(),
        company_id=uuid4(),
        transaction_date=date(2026, 3, 15),
        amount=Money(Decimal("1000.00"), "INR"),
        allocated_amount=Money(Decimal("1000.00"), "INR"),
        unallocated_amount=Money(Decimal("0.00"), "INR"),
        currency="INR",
        status=PaymentStatus.RECONCILED,
    )

    # Partial unallocation
    payment.unallocate(Money(Decimal("300.00"), "INR"))
    assert payment.allocated_amount == Money(Decimal("700.00"), "INR")
    assert payment.unallocated_amount == Money(Decimal("300.00"), "INR")
    assert payment.status == PaymentStatus.PARTIALLY_RECONCILED

    # Complete unallocation
    payment.unallocate(Money(Decimal("700.00"), "INR"))
    assert payment.allocated_amount.is_zero()
    assert payment.unallocated_amount == Money(Decimal("1000.00"), "INR")
    assert payment.status == PaymentStatus.UNRECONCILED


def test_payment_ignore_and_unignore():
    """Verify accountant ignore and unignore state transitions."""
    payment = Payment(
        id=uuid4(),
        company_id=uuid4(),
        transaction_date=date(2026, 3, 15),
        amount=Money(Decimal("250.00"), "INR"),
        allocated_amount=Money(Decimal("0.00"), "INR"),
        unallocated_amount=Money(Decimal("250.00"), "INR"),
        currency="INR",
    )

    # Cannot ignore without a reason
    with pytest.raises(ValidationError, match="reason must be provided"):
        payment.mark_ignored("")

    payment.mark_ignored("Bank interest credit, not customer payment")
    assert payment.status == PaymentStatus.IGNORED
    assert "[IGNORED]" in payment.notes

    # Cannot allocate an ignored payment
    with pytest.raises(DomainError, match="Cannot allocate an IGNORED payment"):
        payment.record_allocation(Money(Decimal("50.00"), "INR"))

    # Unignore restores to UNRECONCILED
    payment.unmark_ignored()
    assert payment.status == PaymentStatus.UNRECONCILED


def test_bank_transaction_credit_vs_debit_and_payment_candidate():
    """Verify Raw Bank Transaction credit/debit segregation and payment conversion."""
    company_id = uuid4()
    batch_id = uuid4()

    # Credit transaction -> Can convert to Payment
    credit_txn = BankTransaction(
        id=uuid4(),
        company_id=company_id,
        batch_id=batch_id,
        transaction_date=date(2026, 3, 10),
        amount=Money(Decimal("5000.00"), "INR"),
        transaction_type=TransactionType.CREDIT,
        narration="Deposit by Client ABC",
        reference_number="UTR987654",
    )
    assert credit_txn.is_credit is True
    assert credit_txn.is_debit is False
    assert credit_txn.deduplication_hash != ""

    payment_candidate = credit_txn.to_payment_candidate(payment_id=uuid4())
    assert isinstance(payment_candidate, Payment)
    assert payment_candidate.amount == Money(Decimal("5000.00"), "INR")
    assert payment_candidate.unallocated_amount == Money(Decimal("5000.00"), "INR")
    assert payment_candidate.reference_number == "UTR987654"

    # Debit transaction -> CANNOT convert to Payment
    debit_txn = BankTransaction(
        id=uuid4(),
        company_id=company_id,
        batch_id=batch_id,
        transaction_date=date(2026, 3, 11),
        amount=Money(Decimal("120.00"), "INR"),
        transaction_type=TransactionType.DEBIT,
        narration="Bank monthly ledger maintenance fee",
    )
    assert debit_txn.is_debit is True
    assert debit_txn.is_credit is False

    with pytest.raises(DomainError, match="Only incoming CREDIT transactions"):
        debit_txn.to_payment_candidate(payment_id=uuid4())


def test_transaction_fingerprint_deterministic():
    """Verify primary and fallback fingerprint hash calculation."""
    company_id = uuid4()
    ref_hash1 = TransactionFingerprint.compute_primary(company_id, "AXIS12345678")
    ref_hash2 = TransactionFingerprint.compute_primary(company_id, " axis12345678 ")
    assert ref_hash1 == ref_hash2

    fallback_hash1 = TransactionFingerprint.compute_fallback(
        company_id=company_id,
        account_identifier="9876543210",
        txn_date=date(2026, 3, 20),
        amount=Decimal("1500.00"),
        currency="INR",
        txn_type="CREDIT",
        narration="Payment from Sharma",
    )
    fallback_hash2 = TransactionFingerprint.compute_fallback(
        company_id=company_id,
        account_identifier=" 9876543210 ",
        txn_date=date(2026, 3, 20),
        amount=Decimal("1500.00"),
        currency="INR",
        txn_type="credit",
        narration="payment   from sharma",
    )
    assert fallback_hash1 == fallback_hash2


def test_import_batch_state():
    """Verify ImportBatch lifecycle updates."""
    batch = ImportBatch(
        id=uuid4(),
        company_id=uuid4(),
        file_name="statement_march.csv",
        file_hash="dummy_sha256_hash",
    )
    assert batch.status == ImportBatchStatus.PENDING

    batch.mark_completed(imported=10, failed=1, duplicates=2)
    assert batch.status == ImportBatchStatus.COMPLETED
    assert batch.imported_count == 10
    assert batch.failed_count == 1
    assert batch.duplicate_count == 2

    batch.mark_failed("Invalid CSV header structure")
    assert batch.status == ImportBatchStatus.FAILED
    assert batch.error_message == "Invalid CSV header structure"
