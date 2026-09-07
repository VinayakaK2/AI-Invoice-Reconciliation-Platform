"""FastAPI authentication and tenant authorization dependencies.

Extracts caller identity, validates JWT claims, and enforces tenant scoping.
"""

from typing import Callable
from uuid import UUID
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import decode_access_token
from app.modules.auth.domain.entities import User, UserRole
from app.modules.auth.infrastructure.repositories import UserRepository
from app.modules.company.domain.entities import Company
from app.modules.company.infrastructure.repositories import CompanyRepository
from app.shared.exceptions import ForbiddenError, UnauthorizedError

# HTTP Bearer security scheme for Swagger UI and header extraction
security_scheme = HTTPBearer(auto_error=True)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Extract and validate JWT access token from Authorization header and return authenticated User."""
    token = credentials.credentials
    payload = decode_access_token(token)

    sub = payload.get("sub")
    company_id_str = payload.get("company_id")
    if not sub or not company_id_str:
        raise UnauthorizedError("Token missing subject or tenant claim")

    try:
        user_id = UUID(sub)
        company_id = UUID(company_id_str)
    except (ValueError, TypeError):
        raise UnauthorizedError("Malformed subject or tenant ID in token")

    user_repo = UserRepository(db)
    user = user_repo.get_by_id(user_id, company_id=company_id)
    if not user:
        raise UnauthorizedError("User does not exist or credentials invalid")

    if not user.is_active:
        raise UnauthorizedError("User account is inactive")

    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency verifying that the authenticated user is active."""
    return current_user


def get_current_company(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Company:
    """Validate and return the tenant company workspace associated with the authenticated user."""
    company_repo = CompanyRepository(db)
    company = company_repo.get_by_id(current_user.company_id)
    if not company:
        raise UnauthorizedError("Associated company workspace does not exist")

    if not company.is_active:
        raise UnauthorizedError("Company workspace is inactive or suspended")

    return company


def require_role(required_role: UserRole) -> Callable[[User], User]:
    """Factory dependency returning a guard verifying the caller possesses the required role."""

    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role != required_role:
            raise ForbiddenError(f"Action requires {required_role.value} role permissions.")
        return current_user

    return role_checker
