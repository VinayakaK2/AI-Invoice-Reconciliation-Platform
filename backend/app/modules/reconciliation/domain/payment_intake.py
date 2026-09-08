"""Domain models, status classifications, and rule engine for Payment Intake (Phase 14.1).

Payment Intake serves as the authoritative, deterministic, zero-mutation eligibility
gateway for the Reconciliation Engine.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import re
from typing import Any, Dict, Optional
from uuid import UUID


class PaymentIntakeStatus(str, Enum):
    """Authoritative 4-tier taxonomy for payment intake eligibility."""

    ELIGIBLE = "ELIGIBLE"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"
    INELIGIBLE = "INELIGIBLE"
    INVALID = "INVALID"


class PaymentIntakeReasonCode(str, Enum):
    """Machine-readable diagnostic reason codes for payment intake."""

    READY_FOR_RECONCILIATION = "READY_FOR_RECONCILIATION"
    PARTIAL_BALANCE_AVAILABLE = "PARTIAL_BALANCE_AVAILABLE"
    FULLY_RECONCILED = "FULLY_RECONCILED"
    PAYMENT_MARKED_IGNORED = "PAYMENT_MARKED_IGNORED"
    ZERO_UNALLOCATED_BALANCE = "ZERO_UNALLOCATED_BALANCE"
    NON_POSITIVE_PAYMENT_AMOUNT = "NON_POSITIVE_PAYMENT_AMOUNT"
    NEGATIVE_ALLOCATED_BALANCE = "NEGATIVE_ALLOCATED_BALANCE"
    NEGATIVE_UNALLOCATED_BALANCE = "NEGATIVE_UNALLOCATED_BALANCE"
    BALANCE_CONSERVATION_BREACH = "BALANCE_CONSERVATION_BREACH"
    INVALID_CURRENCY_CODE = "INVALID_CURRENCY_CODE"
    FUTURE_TRANSACTION_DATE = "FUTURE_TRANSACTION_DATE"
    DEBIT_TRANSACTION_INELIGIBLE = "DEBIT_TRANSACTION_INELIGIBLE"
    INVALID_PAYMENT_STATUS = "INVALID_PAYMENT_STATUS"


ISO_CURRENCY_REGEX = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class PaymentIntakeResult:
    """Immutable outcome of payment intake eligibility evaluation."""

    payment_id: UUID
    company_id: UUID
    status: PaymentIntakeStatus
    is_eligible: bool
    reason_code: str
    reason_description: str
    original_amount: Decimal
    allocated_amount: Decimal
    unallocated_amount: Decimal
    effective_amount: Decimal
    currency: str
    payment_date: date
    bank_account_number: Optional[str] = None
    is_deterministic: bool = True
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to serializable dictionary."""
        return {
            "payment_id": str(self.payment_id),
            "company_id": str(self.company_id),
            "status": self.status.value,
            "is_eligible": self.is_eligible,
            "reason_code": self.reason_code,
            "reason_description": self.reason_description,
            "original_amount": str(self.original_amount),
            "allocated_amount": str(self.allocated_amount),
            "unallocated_amount": str(self.unallocated_amount),
            "effective_amount": str(self.effective_amount),
            "currency": self.currency,
            "payment_date": self.payment_date.isoformat(),
            "bank_account_number": self.bank_account_number,
            "is_deterministic": self.is_deterministic,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class PaymentIntakeRuleEngine:
    """Pure, deterministic evaluation engine for payment intake eligibility.

    Guarantees:
    - 100% deterministic, zero LLM or probabilistic scoring.
    - Zero side-effects or mutations on domain entities.
    - Full validation of financial invariants and balance conservation.
    """

    @staticmethod
    def evaluate(
        payment: Any,
        as_of_date: Optional[date] = None,
    ) -> PaymentIntakeResult:
        """Evaluate a payment context for reconciliation intake eligibility.

        Args:
            payment: PaymentIntakeContext (or compatible duck-typed object).
            as_of_date: Reference date for future date anomaly check (defaults to current UTC date).

        Returns:
            PaymentIntakeResult containing status, eligibility flag, effective amount, and reason code.
        """
        eval_date = as_of_date or datetime.now(timezone.utc).date()

        # Resolve sub-balances defensively for backward compatibility
        orig_amount = payment.amount
        allocated = getattr(payment, "allocated_amount", Decimal("0.00")) or Decimal("0.00")
        unallocated = getattr(payment, "unallocated_amount", None)
        if unallocated is None:
            # Default unallocated amount: if allocated is 0, defaults to full amount
            unallocated = orig_amount - allocated

        payment_status = (getattr(payment, "status", None) or "UNRECONCILED").upper()
        transaction_type = (getattr(payment, "transaction_type", None) or "CREDIT").upper()
        currency = (getattr(payment, "currency", "") or "").strip().upper()

        def _make_result(
            status: PaymentIntakeStatus,
            is_eligible: bool,
            code: PaymentIntakeReasonCode,
            desc: str,
            eff_amount: Decimal = Decimal("0.00"),
        ) -> PaymentIntakeResult:
            return PaymentIntakeResult(
                payment_id=payment.payment_id,
                company_id=payment.company_id,
                status=status,
                is_eligible=is_eligible,
                reason_code=code.value,
                reason_description=desc,
                original_amount=orig_amount,
                allocated_amount=allocated,
                unallocated_amount=unallocated,
                effective_amount=eff_amount,
                currency=currency,
                payment_date=payment.payment_date,
                bank_account_number=payment.bank_account_number,
                is_deterministic=True,
            )

        # 1. Financial Invariant Check: Non-positive gross amount
        if orig_amount <= Decimal("0.00"):
            return _make_result(
                PaymentIntakeStatus.INVALID,
                False,
                PaymentIntakeReasonCode.NON_POSITIVE_PAYMENT_AMOUNT,
                f"Payment gross amount ({orig_amount}) must be strictly greater than zero.",
            )

        # 2. Currency Validation: Must conform to ISO 4217 (3-letter uppercase alphabetic)
        if not currency or not ISO_CURRENCY_REGEX.match(currency):
            return _make_result(
                PaymentIntakeStatus.INVALID,
                False,
                PaymentIntakeReasonCode.INVALID_CURRENCY_CODE,
                f"Payment currency '{currency}' is invalid. Must be a valid 3-letter ISO 4217 code.",
            )

        # 3. Transaction Direction Check: Only CREDIT inflows represent receivable payments
        if transaction_type == "DEBIT":
            return _make_result(
                PaymentIntakeStatus.INELIGIBLE,
                False,
                PaymentIntakeReasonCode.DEBIT_TRANSACTION_INELIGIBLE,
                "Debit transaction cannot enter receivable invoice reconciliation.",
            )

        # 4. Sub-balance Non-negative Invariants
        if allocated < Decimal("0.00"):
            return _make_result(
                PaymentIntakeStatus.INVALID,
                False,
                PaymentIntakeReasonCode.NEGATIVE_ALLOCATED_BALANCE,
                f"Allocated balance ({allocated}) cannot be negative.",
            )

        if unallocated < Decimal("0.00"):
            return _make_result(
                PaymentIntakeStatus.INVALID,
                False,
                PaymentIntakeReasonCode.NEGATIVE_UNALLOCATED_BALANCE,
                f"Unallocated balance ({unallocated}) cannot be negative.",
            )

        # 5. Financial Conservation Invariant: allocated + unallocated == gross amount
        if (allocated + unallocated) != orig_amount:
            return _make_result(
                PaymentIntakeStatus.INVALID,
                False,
                PaymentIntakeReasonCode.BALANCE_CONSERVATION_BREACH,
                (
                    f"Balance conservation violated: allocated ({allocated}) + "
                    f"unallocated ({unallocated}) != gross amount ({orig_amount})."
                ),
            )

        # 6. Temporal Invariant: Future date anomaly check
        # If an explicit reference as_of_date is provided, enforce strict non-future check against that date.
        # Otherwise, allow a reasonable 30-day forward window for bank value dates and clearing lags.
        if as_of_date is not None:
            if payment.payment_date > as_of_date:
                return _make_result(
                    PaymentIntakeStatus.INVALID,
                    False,
                    PaymentIntakeReasonCode.FUTURE_TRANSACTION_DATE,
                    f"Payment date ({payment.payment_date}) is in the future relative to system date ({as_of_date}).",
                )
        else:
            max_forward_date = eval_date + timedelta(days=30)
            if payment.payment_date > max_forward_date:
                return _make_result(
                    PaymentIntakeStatus.INVALID,
                    False,
                    PaymentIntakeReasonCode.FUTURE_TRANSACTION_DATE,
                    f"Payment date ({payment.payment_date}) exceeds maximum forward value date window ({max_forward_date}).",
                )

        # 7. Status Invariant: ALREADY_PROCESSED (RECONCILED or fully allocated)
        if payment_status == "RECONCILED" or unallocated == Decimal("0.00"):
            return _make_result(
                PaymentIntakeStatus.ALREADY_PROCESSED,
                False,
                PaymentIntakeReasonCode.FULLY_RECONCILED,
                "Payment is already fully reconciled with zero remaining unallocated balance.",
                eff_amount=Decimal("0.00"),
            )

        # 8. Status Invariant: INELIGIBLE (Accountant marked IGNORED)
        if payment_status == "IGNORED":
            return _make_result(
                PaymentIntakeStatus.INELIGIBLE,
                False,
                PaymentIntakeReasonCode.PAYMENT_MARKED_IGNORED,
                "Payment was marked IGNORED by an accountant and is excluded from reconciliation.",
            )

        # 9. Recognized Status Check
        if payment_status not in ("UNRECONCILED", "PARTIALLY_RECONCILED"):
            return _make_result(
                PaymentIntakeStatus.INVALID,
                False,
                PaymentIntakeReasonCode.INVALID_PAYMENT_STATUS,
                f"Payment status '{payment_status}' is not recognized for reconciliation intake.",
            )

        # 10. ELIGIBLE Case: Ready for downstream matching
        if payment_status == "PARTIALLY_RECONCILED":
            return _make_result(
                PaymentIntakeStatus.ELIGIBLE,
                True,
                PaymentIntakeReasonCode.PARTIAL_BALANCE_AVAILABLE,
                f"Payment is partially reconciled with {unallocated} {currency} available for matching.",
                eff_amount=unallocated,
            )

        return _make_result(
            PaymentIntakeStatus.ELIGIBLE,
            True,
            PaymentIntakeReasonCode.READY_FOR_RECONCILIATION,
            f"Payment is unreconciled and ready for matching with {unallocated} {currency}.",
            eff_amount=unallocated,
        )
