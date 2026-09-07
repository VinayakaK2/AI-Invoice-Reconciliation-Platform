"""Unit tests for exception hierarchy and Result type."""

import pytest
from app.shared.exceptions import (
    AppError,
    ValidationError,
    DomainError,
    FinancialInvariantError,
    NotFoundError,
    UnauthorizedError,
    ForbiddenError,
)
from app.shared.result import Result


def test_exception_status_codes_and_defaults():
    """Verify standard status codes across error categories."""
    val_err = ValidationError("Invalid customer tax ID")
    assert val_err.status_code == 400
    assert val_err.code == "VALIDATION_ERROR"

    auth_err = UnauthorizedError()
    assert auth_err.status_code == 401

    forb_err = ForbiddenError()
    assert forb_err.status_code == 403

    nf_err = NotFoundError("Invoice", "inv_123")
    assert nf_err.status_code == 404
    assert nf_err.code == "INVOICE_NOT_FOUND"

    fin_err = FinancialInvariantError("Payment over-allocated")
    assert fin_err.status_code == 422
    assert fin_err.code == "FINANCIAL_INVARIANT_VIOLATION"


def test_result_success():
    """Verify successful Result properties."""
    res = Result.ok(100)
    assert res.is_success is True
    assert res.is_failure is False
    assert res.value == 100

    with pytest.raises(ValueError, match="Cannot access error"):
        _ = res.error


def test_result_failure():
    """Verify failed Result properties."""
    err = ValidationError("Bad input")
    res: Result[int] = Result.fail(err)
    assert res.is_success is False
    assert res.is_failure is True
    assert res.error == err

    with pytest.raises(ValueError, match="Cannot access value"):
        _ = res.value
