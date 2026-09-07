"""Result type pattern for domain and application layers.

Encapsulates success with a value or failure with an AppError.
"""

from typing import Generic, Optional, TypeVar, Union
from app.shared.exceptions import AppError

T = TypeVar("T")
E = TypeVar("E", bound=AppError)


class Result(Generic[T]):
    """Represents either a success value or an error."""

    __slots__ = ("_is_success", "_value", "_error")

    def __init__(self, is_success: bool, value: Optional[T] = None, error: Optional[AppError] = None) -> None:
        self._is_success = is_success
        self._value = value
        self._error = error

    @property
    def is_success(self) -> bool:
        """Return True if result is a success."""
        return self._is_success

    @property
    def is_failure(self) -> bool:
        """Return True if result is a failure."""
        return not self._is_success

    @property
    def value(self) -> T:
        """Get success value. Raises ValueError if result is a failure."""
        if not self._is_success:
            raise ValueError(f"Cannot access value on failed Result: {self._error}")
        return self._value  # type: ignore

    @property
    def error(self) -> AppError:
        """Get failure error. Raises ValueError if result is a success."""
        if self._is_success:
            raise ValueError("Cannot access error on successful Result")
        return self._error  # type: ignore

    @classmethod
    def ok(cls, value: T) -> "Result[T]":
        """Construct a successful Result with the given value."""
        return cls(is_success=True, value=value)

    @classmethod
    def fail(cls, error: AppError) -> "Result[T]":
        """Construct a failed Result with the given error."""
        return cls(is_success=False, error=error)

    def __repr__(self) -> str:
        """Developer string representation."""
        if self._is_success:
            return f"Result.ok({self._value!r})"
        return f"Result.fail({self._error!r})"
