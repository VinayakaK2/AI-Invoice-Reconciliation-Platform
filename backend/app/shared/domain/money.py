"""Money value object with exact decimal representation.

Prevents floating point inaccuracy in financial operations.
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Union


class Money:
    """Immutable monetary value object encapsulating exact amount and ISO currency."""

    __slots__ = ("_amount", "_currency")

    def __init__(self, amount: Union[Decimal, int, str, "Money"], currency: str = "INR") -> None:
        """Initialize Money with exact decimal amount and currency."""
        if isinstance(amount, float):
            raise TypeError("Floating point numbers are forbidden for financial amounts. Use Decimal, int, or str.")

        if isinstance(amount, Money):
            self._amount = amount.amount
            self._currency = amount.currency
            return

        # Ensure exact decimal with 2 decimal places standard rounding
        try:
            dec_amount = Decimal(str(amount))
        except Exception as exc:
            raise ValueError(f"Invalid monetary amount '{amount}'. Must be a valid numeric representation.") from exc

        self._amount = dec_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self._currency = currency.upper().strip()

    @property
    def amount(self) -> Decimal:
        """Return the underlying exact Decimal amount."""
        return self._amount

    @property
    def currency(self) -> str:
        """Return the ISO currency code."""
        return self._currency

    def __add__(self, other: Any) -> "Money":
        """Add two Money objects of the same currency."""
        if not isinstance(other, Money):
            raise TypeError(f"Cannot add Money and {type(other).__name__}")
        if self._currency != other._currency:
            raise ValueError(f"Currency mismatch: cannot add {self._currency} and {other._currency}")
        return Money(self._amount + other._amount, self._currency)

    def __sub__(self, other: Any) -> "Money":
        """Subtract another Money object of the same currency."""
        if not isinstance(other, Money):
            raise TypeError(f"Cannot subtract {type(other).__name__} from Money")
        if self._currency != other._currency:
            raise ValueError(f"Currency mismatch: cannot subtract {other._currency} from {self._currency}")
        return Money(self._amount - other._amount, self._currency)

    def __mul__(self, factor: Union[int, Decimal]) -> "Money":
        """Multiply Money by an exact scalar factor."""
        if isinstance(factor, float):
            raise TypeError("Cannot multiply Money by float. Use int or Decimal.")
        return Money(self._amount * Decimal(str(factor)), self._currency)

    def __rmul__(self, factor: Union[int, Decimal]) -> "Money":
        """Multiply scalar factor by Money."""
        return self.__mul__(factor)

    def __eq__(self, other: Any) -> bool:
        """Check equality between Money objects."""
        if not isinstance(other, Money):
            return False
        return self._amount == other._amount and self._currency == other._currency

    def __lt__(self, other: "Money") -> bool:
        """Check if amount is strictly less than other."""
        self._assert_same_currency(other)
        return self._amount < other._amount

    def __le__(self, other: "Money") -> bool:
        """Check if amount is less than or equal to other."""
        self._assert_same_currency(other)
        return self._amount <= other._amount

    def __gt__(self, other: "Money") -> bool:
        """Check if amount is strictly greater than other."""
        self._assert_same_currency(other)
        return self._amount > other._amount

    def __ge__(self, other: "Money") -> bool:
        """Check if amount is greater than or equal to other."""
        self._assert_same_currency(other)
        return self._amount >= other._amount

    def _assert_same_currency(self, other: Any) -> None:
        """Verify comparison is made between identical currencies."""
        if not isinstance(other, Money):
            raise TypeError(f"Cannot compare Money with {type(other).__name__}")
        if self._currency != other._currency:
            raise ValueError(f"Cannot compare different currencies: {self._currency} vs {other._currency}")

    def is_zero(self) -> bool:
        """Return True if amount is exactly zero."""
        return self._amount == Decimal("0.00")

    def is_positive(self) -> bool:
        """Return True if amount is strictly positive."""
        return self._amount > Decimal("0.00")

    def is_negative(self) -> bool:
        """Return True if amount is negative."""
        return self._amount < Decimal("0.00")

    def __hash__(self) -> int:
        """Compute hash for set operations and dictionary lookup."""
        return hash((self._amount, self._currency))

    def to_dict(self) -> dict:
        """Serialize to dictionary representation."""
        return {"amount": str(self._amount), "currency": self._currency}

    @classmethod
    def zero(cls, currency: str = "INR") -> "Money":
        """Factory method returning exact zero money in specified currency."""
        return cls(amount=Decimal("0.00"), currency=currency)

    @classmethod
    def from_dict(cls, data: dict) -> "Money":
        """Deserialize from dictionary representation."""
        return cls(amount=data["amount"], currency=data.get("currency", "INR"))

    def __repr__(self) -> str:
        """Developer string representation."""
        return f"Money(amount='{self._amount}', currency='{self._currency}')"

    def __str__(self) -> str:
        """User-facing formatted string."""
        symbol = "₹" if self._currency == "INR" else f"{self._currency} "
        return f"{symbol}{self._amount:,.2f}"
