"""Company workspace module presentation router.

Provides endpoints for managing company workspace details and team member invitations.
"""

from typing import Any, Dict, List
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.modules.auth.application.use_cases import InviteUserUseCase, ListCompanyUsersUseCase
from app.modules.auth.domain.entities import User, UserRole
from app.modules.auth.infrastructure.repositories import InvitationRepository, UserRepository
from app.modules.auth.presentation.dependencies import get_current_company, get_current_user, require_role
from app.modules.auth.presentation.schemas import (
    CompanyResponse,
    InvitationResponse,
    InviteUserRequest,
    UserResponse,
)
from app.modules.company.domain.entities import Company

router = APIRouter(prefix="/company", tags=["Company"])


@router.get("/status", summary="Company module status probe")
def company_module_status() -> Dict[str, str]:
    """Return company module readiness status."""
    return {"module": "company", "status": "initialized"}



@router.get(
    "/current",
    response_model=Dict[str, Any],
    summary="Get current tenant company workspace profile",
)
def get_current_company_profile(
    current_company: Company = Depends(get_current_company),
) -> Dict[str, Any]:
    """Return details of the current tenant company workspace."""
    return {
        "success": True,
        "data": CompanyResponse.model_validate(current_company),
    }


@router.post(
    "/users/invite",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Invite a new accountant to the company workspace (Owner only)",
)
def invite_user(
    request: InviteUserRequest,
    current_user: User = Depends(require_role(UserRole.OWNER)),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Invite an accountant to the company. Restricted to Owners."""
    user_repo = UserRepository(db)
    invite_repo = InvitationRepository(db)
    use_case = InviteUserUseCase(user_repo, invite_repo)

    invitation = use_case.execute(current_user, request)
    return {
        "success": True,
        "data": InvitationResponse(
            id=invitation.id,
            company_id=invitation.company_id,
            email=invitation.email,
            role=invitation.role.value,
            expires_at=invitation.expires_at,
            invitation_token=invitation.invitation_token,
        ),
    }


@router.get(
    "/users",
    response_model=Dict[str, Any],
    summary="List all users belonging to the caller's company workspace",
)
def list_company_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """List all team members within the caller's company tenant."""
    user_repo = UserRepository(db)
    use_case = ListCompanyUsersUseCase(user_repo)

    users = use_case.execute(current_user.company_id)
    return {
        "success": True,
        "data": [UserResponse.model_validate(u) for u in users],
    }
