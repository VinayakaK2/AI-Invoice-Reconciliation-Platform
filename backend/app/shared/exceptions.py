"""Classified application error and exception hierarchy.

Separates validation, domain, authorization, and infrastructure failures.
"""

from typing import Any, Dict, Optional


class AppError(Exception):
    """Base exception for all domain and application errors."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_SERVER_ERROR",
        status_code: int = 500,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}


class ValidationError(AppError):
    """Input payload or query parameter failed validation."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            status_code=400,
            details=details,
        )


class UnauthorizedError(AppError):
    """User lacks valid authentication credentials."""

    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(
            message=message,
            code="UNAUTHORIZED",
            status_code=401,
        )


class ForbiddenError(AppError):
    """User lacks sufficient permission to perform the requested action."""

    def __init__(self, message: str = "Access forbidden") -> None:
        super().__init__(
            message=message,
            code="FORBIDDEN",
            status_code=403,
        )


class NotFoundError(AppError):
    """Requested entity does not exist in the company scope."""

    def __init__(self, entity_name: str, entity_id: Any) -> None:
        super().__init__(
            message=f"{entity_name} with identifier '{entity_id}' not found.",
            code=f"{entity_name.upper()}_NOT_FOUND",
            status_code=404,
            details={"entity": entity_name, "id": str(entity_id)},
        )


class ConflictError(AppError):
    """Resource already exists or operation conflicts with current state."""

    def __init__(self, message: str, code: str = "CONFLICT") -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=409,
        )


class DomainError(AppError):
    """Business rule or domain invariant violation."""

    def __init__(self, message: str, code: str = "DOMAIN_ERROR", details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=422,
            details=details,
        )


class FinancialInvariantError(DomainError):
    """Severe financial integrity violation (e.g. over-allocation, balance mismatch)."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(
            message=message,
            code="FINANCIAL_INVARIANT_VIOLATION",
            details=details,
        )


class InfrastructureError(AppError):
    """External provider, database, or network dependency failed."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(
            message=message,
            code="INFRASTRUCTURE_ERROR",
            status_code=503,
            details=details,
        )
