"""Authentication domain entities and value objects.

Defines pure Python business models for users, invitations, and password reset tokens.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4


class UserRole(str, Enum):
    """Authoritative user access roles within a company workspace."""
    OWNER = "OWNER"
    ACCOUNTANT = "ACCOUNTANT"


@dataclass
class User:
    """Core domain user entity belonging to an isolated company tenant."""
    id: UUID
    company_id: UUID
    email: str
    hashed_password: str
    full_name: str
    role: UserRole = UserRole.ACCOUNTANT
    is_active: bool = True
    is_verified: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_owner(self) -> bool:
        """Check if user has Owner privileges."""
        return self.role == UserRole.OWNER

    def belongs_to(self, company_id: UUID) -> bool:
        """Verify tenant ownership."""
        return self.company_id == company_id


@dataclass
class UserInvitation:
    """Domain entity representing an invitation sent by an Owner to join a company."""
    id: UUID
    company_id: UUID
    email: str
    role: UserRole
    invitation_token: str
    expires_at: datetime
    is_accepted: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_expired(self) -> bool:
        """Check whether the invitation token has expired."""
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expires


@dataclass
class PasswordResetToken:
    """Domain entity representing a secure, time-limited password reset request."""
    id: UUID
    user_id: UUID
    token_hash: str
    expires_at: datetime
    is_used: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_valid(self) -> bool:
        """Check whether the reset token is unused and unexpired."""
        if self.is_used:
            return False
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) <= expires

