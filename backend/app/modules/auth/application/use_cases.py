"""Application use cases coordinating Authentication and User workflows."""

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from app.config import settings
from app.core.logging import logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.modules.auth.domain.entities import PasswordResetToken, User, UserInvitation, UserRole
from app.modules.auth.infrastructure.repositories import (
    InvitationRepository,
    PasswordResetRepository,
    UserRepository,
)
from app.modules.auth.presentation.schemas import (
    AcceptInvitationRequest,
    InviteUserRequest,
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RegisterRequest,
)
from app.modules.company.domain.entities import Company
from app.modules.company.infrastructure.repositories import CompanyRepository
from app.shared.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)


class RegisterCompanyAndOwnerUseCase:
    """Orchestrates company workspace registration and founding owner user creation."""

    def __init__(self, user_repo: UserRepository, company_repo: CompanyRepository) -> None:
        self.user_repo = user_repo
        self.company_repo = company_repo

    def execute(self, request: RegisterRequest) -> Tuple[User, Company, Dict[str, str]]:
        """Atomically create tenant company, owner user, and issue initial JWT tokens."""
        # Check if email is already used
        existing_user = self.user_repo.get_by_email(request.email)
        if existing_user:
            raise ConflictError(
                message=f"An account with email '{request.email}' already exists.",
                code="USER_EMAIL_ALREADY_EXISTS",
            )

        # 1. Create company entity
        company = Company(
            id=uuid.uuid4(),
            name=request.company_name.strip(),
            base_currency=request.base_currency.upper().strip(),
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        saved_company = self.company_repo.create(company)

        # 2. Create owner user
        hashed_pw = hash_password(request.password)
        user = User(
            id=uuid.uuid4(),
            company_id=saved_company.id,
            email=request.email.lower().strip(),
            hashed_password=hashed_pw,
            full_name=request.full_name.strip(),
            role=UserRole.OWNER,
            is_active=True,
            is_verified=True,  # Primary owner is verified by default on registration
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        saved_user = self.user_repo.create(user)

        # 3. Generate initial session tokens
        claims = {
            "sub": str(saved_user.id),
            "company_id": str(saved_company.id),
            "role": saved_user.role.value,
            "email": saved_user.email,
        }
        access_token = create_access_token(claims)
        refresh_token = create_refresh_token(claims)

        tokens = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }

        logger.info(
            f"Company '{saved_company.name}' registered successfully with Owner user {saved_user.email}",
            extra={"company_id": str(saved_company.id), "user_id": str(saved_user.id)},
        )
        return saved_user, saved_company, tokens


class LoginUseCase:
    """Authenticates user credentials and issues signed JWT bearer tokens."""

    def __init__(self, user_repo: UserRepository, company_repo: CompanyRepository) -> None:
        self.user_repo = user_repo
        self.company_repo = company_repo

    def execute(self, request: LoginRequest) -> Tuple[User, Company, Dict[str, str]]:
        """Validate email and password, check account & company active status, return tokens."""
        # Find user
        if request.company_id:
            user = self.user_repo.get_by_email_and_company(request.email, request.company_id)
        else:
            user = self.user_repo.get_by_email(request.email)

        if not user or not verify_password(request.password, user.hashed_password):
            raise UnauthorizedError("Invalid email or password")

        if not user.is_active:
            raise UnauthorizedError("Account is inactive or deactivated. Please contact your company administrator.")

        # Validate company is active
        company = self.company_repo.get_by_id(user.company_id)
        if not company or not company.is_active:
            raise UnauthorizedError("Associated company workspace is inactive or suspended.")

        # Generate tokens
        claims = {
            "sub": str(user.id),
            "company_id": str(company.id),
            "role": user.role.value,
            "email": user.email,
        }
        access_token = create_access_token(claims)
        refresh_token = create_refresh_token(claims)

        tokens = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }

        logger.info(
            f"User {user.email} logged in successfully",
            extra={"company_id": str(company.id), "user_id": str(user.id)},
        )
        return user, company, tokens


class RefreshTokenUseCase:
    """Validates an existing refresh token and issues a fresh access token."""

    def __init__(self, user_repo: UserRepository, company_repo: CompanyRepository) -> None:
        self.user_repo = user_repo
        self.company_repo = company_repo

    def execute(self, refresh_token_str: str) -> Dict[str, str]:
        """Decode refresh token and generate fresh access token."""
        payload = decode_access_token(refresh_token_str)
        if payload.get("type") != "refresh":
            raise UnauthorizedError("Invalid token type for refresh operation")

        user_id = uuid.UUID(payload.get("sub"))
        user = self.user_repo.get_by_id(user_id)
        if not user or not user.is_active:
            raise UnauthorizedError("User is no longer active")

        company = self.company_repo.get_by_id(user.company_id)
        if not company or not company.is_active:
            raise UnauthorizedError("Company workspace is inactive")

        claims = {
            "sub": str(user.id),
            "company_id": str(company.id),
            "role": user.role.value,
            "email": user.email,
        }
        new_access_token = create_access_token(claims)
        new_refresh_token = create_refresh_token(claims)

        return {
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }


class InviteUserUseCase:
    """Handles company Owner inviting an accountant to their workspace."""

    def __init__(self, user_repo: UserRepository, invite_repo: InvitationRepository) -> None:
        self.user_repo = user_repo
        self.invite_repo = invite_repo

    def execute(self, inviter: User, request: InviteUserRequest) -> UserInvitation:
        """Create invitation token scoped to inviter's company."""
        if not inviter.is_owner():
            raise ForbiddenError("Only company Owners can invite new team members.")

        # Check if already a member in this company
        existing = self.user_repo.get_by_email_and_company(request.email, inviter.company_id)
        if existing:
            raise ConflictError(
                message=f"User with email '{request.email}' is already a member of this company.",
                code="USER_ALREADY_MEMBER",
            )

        # Generate secure invitation token
        raw_token = secrets.token_urlsafe(32)
        invitation = UserInvitation(
            id=uuid.uuid4(),
            company_id=inviter.company_id,
            email=request.email.lower().strip(),
            role=UserRole(request.role.upper()),
            invitation_token=raw_token,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            is_accepted=False,
            created_at=datetime.now(timezone.utc),
        )
        saved = self.invite_repo.create(invitation)

        logger.info(
            f"Invitation created for {saved.email} to join company {inviter.company_id}",
            extra={"company_id": str(inviter.company_id), "inviter_id": str(inviter.id)},
        )
        return saved


class AcceptInvitationUseCase:
    """Allows an invited user to accept an invitation, set their password, and activate their account."""

    def __init__(
        self,
        user_repo: UserRepository,
        invite_repo: InvitationRepository,
        company_repo: CompanyRepository,
    ) -> None:
        self.user_repo = user_repo
        self.invite_repo = invite_repo
        self.company_repo = company_repo

    def execute(self, request: AcceptInvitationRequest) -> Tuple[User, Company, Dict[str, str]]:
        """Validate invitation token, create user, mark accepted, return session tokens."""
        invitation = self.invite_repo.get_by_token(request.token)
        if not invitation:
            raise NotFoundError("Invitation", "provided token")

        if invitation.is_accepted:
            raise ConflictError("This invitation has already been accepted.")

        if invitation.is_expired():
            raise ValidationError("This invitation token has expired. Please request a new invite.")

        company = self.company_repo.get_by_id(invitation.company_id)
        if not company or not company.is_active:
            raise ValidationError("The company workspace associated with this invitation is no longer active.")

        # Check if user already exists
        existing_user = self.user_repo.get_by_email_and_company(invitation.email, company.id)
        if existing_user:
            raise ConflictError("An account for this email already exists in the company.")

        # Create user
        hashed_pw = hash_password(request.password)
        new_user = User(
            id=uuid.uuid4(),
            company_id=company.id,
            email=invitation.email,
            hashed_password=hashed_pw,
            full_name=request.full_name.strip(),
            role=invitation.role,
            is_active=True,
            is_verified=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        saved_user = self.user_repo.create(new_user)
        self.invite_repo.mark_accepted(invitation.id)

        # Generate tokens
        claims = {
            "sub": str(saved_user.id),
            "company_id": str(company.id),
            "role": saved_user.role.value,
            "email": saved_user.email,
        }
        access_token = create_access_token(claims)
        refresh_token = create_refresh_token(claims)

        tokens = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }

        logger.info(
            f"User {saved_user.email} joined company {company.name} via invitation",
            extra={"company_id": str(company.id), "user_id": str(saved_user.id)},
        )
        return saved_user, company, tokens


class RequestPasswordResetUseCase:
    """Generates a secure password reset token for a registered user."""

    def __init__(self, user_repo: UserRepository, reset_repo: PasswordResetRepository) -> None:
        self.user_repo = user_repo
        self.reset_repo = reset_repo

    def execute(self, request: PasswordResetRequest) -> Optional[str]:
        """Generate reset token if user exists; returns raw token for dispatching email."""
        user = self.user_repo.get_by_email(request.email)
        if not user:
            # Silent return to avoid user enumeration
            logger.info(f"Password reset requested for non-existent email: {request.email}")
            return None

        raw_token = secrets.token_urlsafe(32)
        token_hashed = hash_token(raw_token)

        reset_entity = PasswordResetToken(
            id=uuid.uuid4(),
            user_id=user.id,
            token_hash=token_hashed,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            is_used=False,
            created_at=datetime.now(timezone.utc),
        )
        self.reset_repo.create(reset_entity)

        logger.info(f"Password reset token generated for user {user.email}")
        return raw_token


class ConfirmPasswordResetUseCase:
    """Validates a password reset token and updates the user's password."""

    def __init__(self, user_repo: UserRepository, reset_repo: PasswordResetRepository) -> None:
        self.user_repo = user_repo
        self.reset_repo = reset_repo

    def execute(self, request: PasswordResetConfirmRequest) -> None:
        """Validate token and update password."""
        token_hashed = hash_token(request.token)
        reset_token = self.reset_repo.get_by_token_hash(token_hashed)

        if not reset_token or not reset_token.is_valid():
            raise ValidationError("Invalid or expired password reset token.")

        user = self.user_repo.get_by_id(reset_token.user_id)
        if not user:
            raise NotFoundError("User", reset_token.user_id)

        new_hashed_password = hash_password(request.new_password)
        self.user_repo.update_password(user.id, new_hashed_password)
        self.reset_repo.mark_used(reset_token.id)

        logger.info(f"Password reset confirmed and updated for user {user.email}")


class ListCompanyUsersUseCase:
    """Lists all users belonging strictly to caller's company."""

    def __init__(self, user_repo: UserRepository) -> None:
        self.user_repo = user_repo

    def execute(self, company_id: uuid.UUID) -> List[User]:
        """Fetch all users in company."""
        return self.user_repo.list_by_company(company_id)
