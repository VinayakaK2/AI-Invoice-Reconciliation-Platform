"""Authentication module presentation router.

Provides HTTP REST endpoints for registration, authentication, token refresh,
password reset, and invitation acceptance.
"""

from typing import Any, Dict
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.modules.auth.application.use_cases import (
    AcceptInvitationUseCase,
    ConfirmPasswordResetUseCase,
    LoginUseCase,
    RefreshTokenUseCase,
    RegisterCompanyAndOwnerUseCase,
    RequestPasswordResetUseCase,
)
from app.modules.auth.domain.entities import User
from app.modules.auth.infrastructure.repositories import (
    InvitationRepository,
    PasswordResetRepository,
    UserRepository,
)
from app.modules.auth.presentation.dependencies import get_current_user
from app.modules.auth.presentation.schemas import (
    AcceptInvitationRequest,
    AuthResponse,
    CompanyResponse,
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.modules.company.domain.entities import Company
from app.modules.company.infrastructure.repositories import CompanyRepository

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.get("/status", summary="Auth module status probe")
def auth_module_status() -> Dict[str, str]:
    """Return auth module readiness status."""
    return {"module": "auth", "status": "initialized"}



@router.post(
    "/register",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Register a new company and owner account",
)
def register(
    request: RegisterRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Register a new tenant company and its initial Owner administrator."""
    user_repo = UserRepository(db)
    company_repo = CompanyRepository(db)
    use_case = RegisterCompanyAndOwnerUseCase(user_repo, company_repo)

    user, company, tokens = use_case.execute(request)
    return {
        "success": True,
        "data": {
            "user": UserResponse.model_validate(user),
            "company": CompanyResponse.model_validate(company),
            "tokens": TokenResponse(**tokens),
        },
    }


@router.post(
    "/login",
    response_model=Dict[str, Any],
    summary="Authenticate user and issue JWT tokens",
)
def login(
    request: LoginRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Authenticate with email & password, returning JWT access and refresh tokens."""
    user_repo = UserRepository(db)
    company_repo = CompanyRepository(db)
    use_case = LoginUseCase(user_repo, company_repo)

    user, company, tokens = use_case.execute(request)
    return {
        "success": True,
        "data": {
            "user": UserResponse.model_validate(user),
            "company": CompanyResponse.model_validate(company),
            "tokens": TokenResponse(**tokens),
        },
    }


@router.post(
    "/refresh",
    response_model=Dict[str, Any],
    summary="Refresh access token using refresh token",
)
def refresh_token(
    request: RefreshTokenRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Issue a new access token using a valid refresh token."""
    user_repo = UserRepository(db)
    company_repo = CompanyRepository(db)
    use_case = RefreshTokenUseCase(user_repo, company_repo)

    tokens = use_case.execute(request.refresh_token)
    return {
        "success": True,
        "data": {
            "tokens": TokenResponse(**tokens),
        },
    }


@router.get(
    "/me",
    response_model=Dict[str, Any],
    summary="Get current authenticated user and company profile",
)
def get_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Return the profile and tenant details of the currently authenticated caller."""
    company_repo = CompanyRepository(db)
    company = company_repo.get_by_id(current_user.company_id)

    return {
        "success": True,
        "data": {
            "user": UserResponse.model_validate(current_user),
            "company": CompanyResponse.model_validate(company) if company else None,
        },
    }


@router.post(
    "/logout",
    response_model=Dict[str, Any],
    summary="Logout user and acknowledge session termination",
)
def logout(
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Acknowledge logout request for current user session."""
    return {
        "success": True,
        "data": {"message": "Successfully logged out"},
    }


@router.post(
    "/password-reset/request",
    response_model=Dict[str, Any],
    summary="Request a password reset link/token",
)
def request_password_reset(
    request: PasswordResetRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Request a password reset token. Returns token in dev/testing mode."""
    user_repo = UserRepository(db)
    reset_repo = PasswordResetRepository(db)
    use_case = RequestPasswordResetUseCase(user_repo, reset_repo)

    token = use_case.execute(request)
    return {
        "success": True,
        "data": {
            "message": "If this email is registered, a password reset instruction has been generated.",
            # Include token in response for local testing and developer integration
            "reset_token": token,
        },
    }


@router.post(
    "/password-reset/confirm",
    response_model=Dict[str, Any],
    summary="Confirm password reset with token and set new password",
)
def confirm_password_reset(
    request: PasswordResetConfirmRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Validate password reset token and update account password."""
    user_repo = UserRepository(db)
    reset_repo = PasswordResetRepository(db)
    use_case = ConfirmPasswordResetUseCase(user_repo, reset_repo)

    use_case.execute(request)
    return {
        "success": True,
        "data": {"message": "Password successfully updated. You may now log in with your new password."},
    }


@router.post(
    "/invitations/accept",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Accept company invitation and complete account setup",
)
def accept_invitation(
    request: AcceptInvitationRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Accept an accountant invitation, specify account password, and join the company."""
    user_repo = UserRepository(db)
    invite_repo = InvitationRepository(db)
    company_repo = CompanyRepository(db)
    use_case = AcceptInvitationUseCase(user_repo, invite_repo, company_repo)

    user, company, tokens = use_case.execute(request)
    return {
        "success": True,
        "data": {
            "user": UserResponse.model_validate(user),
            "company": CompanyResponse.model_validate(company),
            "tokens": TokenResponse(**tokens),
        },
    }
