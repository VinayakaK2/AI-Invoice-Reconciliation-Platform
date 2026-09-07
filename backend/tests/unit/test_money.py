"""Unit tests for Money value object and financial precision rules."""

from decimal import Decimal
import pytest
from app.shared.domain.money import Money


def test_money_initialization_with_exact_decimals():
    """Verify Money initializes properly with Decimal and integer values."""
    m1 = Money(Decimal("100.50"))
    assert m1.amount == Decimal("100.50")
    assert m1.currency == "INR"

    m2 = Money(500, currency="USD")
    assert m2.amount == Decimal("500.00")
    assert m2.currency == "USD"


def test_money_rejects_float_values():
    """Verify Money strictly rejects binary float types to prevent IEEE-754 inaccuracy."""
    with pytest.raises(TypeError, match="Floating point numbers are forbidden"):
        Money(100.50)  # Passing float must raise TypeError


def test_money_addition_same_currency():
    """Verify adding two Money objects of same currency computes exact sum."""
    m1 = Money("100.25")
    m2 = Money("200.75")
    res = m1 + m2
    assert res.amount == Decimal("301.00")
    assert res.currency == "INR"


def test_money_addition_currency_mismatch():
    """Verify adding different currencies raises ValueError."""
    m1 = Money("100.00", currency="INR")
    m2 = Money("50.00", currency="USD")
    with pytest.raises(ValueError, match="Currency mismatch"):
        _ = m1 + m2


def test_money_subtraction():
    """Verify subtracting Money computes exact difference."""
    m1 = Money("500.00")
    m2 = Money("150.75")
    res = m1 - m2
    assert res.amount == Decimal("349.25")


def test_money_multiplication_by_exact_scalar():
    """Verify scalar multiplication."""
    m = Money("120.00")
    res = m * 3
    assert res.amount == Decimal("360.00")

    with pytest.raises(TypeError, match="Cannot multiply Money by float"):
        _ = m * 1.5


def test_money_comparisons():
    """Verify comparison operators."""
    m1 = Money("100.00")
    m2 = Money("200.00")
    m3 = Money("100.00")

    assert m1 < m2
    assert m1 <= m3
    assert m2 > m1
    assert m1 == m3
    assert m1 != m2


def test_money_state_predicates():
    """Verify zero, positive, and negative checks."""
    zero = Money("0.00")
    pos = Money("50.00")
    neg = Money("-25.00")

    assert zero.is_zero()
    assert not zero.is_positive()

    assert pos.is_positive()
    assert not pos.is_negative()

    assert neg.is_negative()
    assert not neg.is_positive()


def test_money_rmul():
    """Verify right scalar multiplication (3 * Money)."""
    m = Money("50.00")
    res = 3 * m
    assert res.amount == Decimal("150.00")
    assert res.currency == "INR"


def test_money_hashable_in_sets_and_dicts():
    """Verify Money can be hashed and used in sets and dictionary keys."""
    m1 = Money("100.00", "INR")
    m2 = Money("100.00", "INR")
    m3 = Money("200.00", "INR")

    money_set = {m1, m2, m3}
    assert len(money_set) == 2

    lookup = {m1: "first_match"}
    assert lookup[m2] == "first_match"


def test_money_serialization_roundtrip():
    """Verify to_dict and from_dict serialization."""
    m = Money("1234.56", "USD")
    d = m.to_dict()
    assert d == {"amount": "1234.56", "currency": "USD"}

    restored = Money.from_dict(d)
    assert restored == m


def test_money_invalid_string_raises_value_error():
    """Verify passing non-numeric strings raises ValueError."""
    with pytest.raises(ValueError, match="Invalid monetary amount"):
        Money("not-a-number")

