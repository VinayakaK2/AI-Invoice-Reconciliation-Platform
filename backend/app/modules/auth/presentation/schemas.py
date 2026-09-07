"""Pydantic v2 schemas and validation contracts for Authentication."""

from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    """Payload for registering a new tenant company and its founding Owner user."""
    company_name: str = Field(..., min_length=2, max_length=255, description="Name of the company/organization")
    base_currency: str = Field(default="INR", min_length=3, max_length=3, description="Base ISO currency code (e.g. INR, USD)")
    email: EmailStr = Field(..., description="Owner's business email address")
    password: str = Field(..., min_length=8, max_length=128, description="Strong password (minimum 8 characters)")
    full_name: str = Field(..., min_length=2, max_length=255, description="Owner's full name")


class LoginRequest(BaseModel):
    """Payload for user authentication."""
    email: EmailStr = Field(..., description="Registered email address")
    password: str = Field(..., min_length=1, description="Account password")
    company_id: Optional[UUID] = Field(default=None, description="Optional tenant context for multi-company memberships")


class TokenResponse(BaseModel):
    """Standard OAuth2 / JWT bearer token response."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshTokenRequest(BaseModel):
    """Payload for refreshing an expired access token."""
    refresh_token: str


class UserResponse(BaseModel):
    """Serialized user representation for API responses."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    is_verified: bool
    created_at: datetime


class CompanyResponse(BaseModel):
    """Serialized company workspace representation for API responses."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    base_currency: str
    is_active: bool
    created_at: datetime


class AuthResponse(BaseModel):
    """Complete response returned upon registration or login."""
    user: UserResponse
    company: CompanyResponse
    tokens: TokenResponse


class InviteUserRequest(BaseModel):
    """Payload for inviting a new accountant user to the tenant company."""
    email: EmailStr
    role: str = Field(default="ACCOUNTANT", description="Role to assign (ACCOUNTANT)")


class InvitationResponse(BaseModel):
    """Response returned upon successful user invitation creation."""
    id: UUID
    company_id: UUID
    email: str
    role: str
    expires_at: datetime
    invitation_token: str


class AcceptInvitationRequest(BaseModel):
    """Payload submitted by an invited user to join a company."""
    token: str = Field(..., description="Invitation token received via email/invite link")
    full_name: str = Field(..., min_length=2, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class PasswordResetRequest(BaseModel):
    """Payload for initiating a password reset email."""
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    """Payload for resetting password with a validated token."""
    token: str
    new_password: str = Field(..., min_length=8, max_length=128)
