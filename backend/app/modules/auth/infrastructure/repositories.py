"""Repository implementations for User, Invitation, and Password Reset persistence."""

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID
from sqlalchemy.orm import Session
from app.modules.auth.domain.entities import PasswordResetToken, User, UserInvitation, UserRole
from app.modules.auth.infrastructure.models import (
    PasswordResetTokenModel,
    UserInvitationModel,
    UserModel,
)


class UserRepository:
    """Repository handling database operations for User entities with tenant scoping."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, user: User) -> User:
        """Persist a new user record."""
        model = UserModel(
            id=user.id,
            company_id=user.company_id,
            email=user.email.lower().strip(),
            hashed_password=user.hashed_password,
            full_name=user.full_name,
            role=user.role.value if isinstance(user.role, UserRole) else user.role,
            is_active=user.is_active,
            is_verified=user.is_verified,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return self._to_entity(model)

    def get_by_id(self, user_id: UUID, company_id: Optional[UUID] = None) -> Optional[User]:
        """Fetch user by ID, optionally scoping to a specific tenant company."""
        query = self.db.query(UserModel).filter(UserModel.id == user_id)
        if company_id:
            query = query.filter(UserModel.company_id == company_id)
        model = query.first()
        return self._to_entity(model) if model else None

    def get_by_email_and_company(self, email: str, company_id: UUID) -> Optional[User]:
        """Fetch user by email strictly within a specific company tenant."""
        model = (
            self.db.query(UserModel)
            .filter(
                UserModel.email == email.lower().strip(),
                UserModel.company_id == company_id,
            )
            .first()
        )
        return self._to_entity(model) if model else None

    def get_by_email(self, email: str) -> Optional[User]:
        """Fetch first user matching email across all companies (for initial login lookup)."""
        model = (
            self.db.query(UserModel)
            .filter(UserModel.email == email.lower().strip())
            .first()
        )
        return self._to_entity(model) if model else None

    def list_by_company(self, company_id: UUID) -> List[User]:
        """List all users belonging strictly to a specific company."""
        models = (
            self.db.query(UserModel)
            .filter(UserModel.company_id == company_id)
            .order_by(UserModel.created_at.asc())
            .all()
        )
        return [self._to_entity(m) for m in models]

    def update_password(self, user_id: UUID, new_hashed_password: str) -> None:
        """Update the bcrypt hashed password for a user."""
        model = self.db.query(UserModel).filter(UserModel.id == user_id).first()
        if model:
            model.hashed_password = new_hashed_password
            model.updated_at = datetime.now(timezone.utc)
            self.db.commit()

    @staticmethod
    def _to_entity(model: UserModel) -> User:
        """Convert ORM model to User domain entity."""
        return User(
            id=model.id,
            company_id=model.company_id,
            email=model.email,
            hashed_password=model.hashed_password,
            full_name=model.full_name,
            role=UserRole(model.role),
            is_active=model.is_active,
            is_verified=model.is_verified,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class InvitationRepository:
    """Repository handling database operations for UserInvitation entities."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, invitation: UserInvitation) -> UserInvitation:
        """Persist a new user invitation."""
        model = UserInvitationModel(
            id=invitation.id,
            company_id=invitation.company_id,
            email=invitation.email.lower().strip(),
            role=invitation.role.value if isinstance(invitation.role, UserRole) else invitation.role,
            invitation_token=invitation.invitation_token,
            expires_at=invitation.expires_at,
            is_accepted=invitation.is_accepted,
            created_at=invitation.created_at,
        )
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return self._to_entity(model)

    def get_by_token(self, token: str) -> Optional[UserInvitation]:
        """Fetch an invitation by its unique token."""
        model = (
            self.db.query(UserInvitationModel)
            .filter(UserInvitationModel.invitation_token == token)
            .first()
        )
        return self._to_entity(model) if model else None

    def mark_accepted(self, invitation_id: UUID) -> None:
        """Mark an invitation record as accepted."""
        model = (
            self.db.query(UserInvitationModel)
            .filter(UserInvitationModel.id == invitation_id)
            .first()
        )
        if model:
            model.is_accepted = True
            self.db.commit()

    @staticmethod
    def _to_entity(model: UserInvitationModel) -> UserInvitation:
        """Convert ORM model to UserInvitation domain entity."""
        return UserInvitation(
            id=model.id,
            company_id=model.company_id,
            email=model.email,
            role=UserRole(model.role),
            invitation_token=model.invitation_token,
            expires_at=model.expires_at,
            is_accepted=model.is_accepted,
            created_at=model.created_at,
        )


class PasswordResetRepository:
    """Repository handling database operations for PasswordResetToken entities."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, token_entity: PasswordResetToken) -> PasswordResetToken:
        """Persist a new password reset token."""
        model = PasswordResetTokenModel(
            id=token_entity.id,
            user_id=token_entity.user_id,
            token_hash=token_entity.token_hash,
            expires_at=token_entity.expires_at,
            is_used=token_entity.is_used,
            created_at=token_entity.created_at,
        )
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return self._to_entity(model)

    def get_by_token_hash(self, token_hash: str) -> Optional[PasswordResetToken]:
        """Fetch password reset record by token hash."""
        model = (
            self.db.query(PasswordResetTokenModel)
            .filter(PasswordResetTokenModel.token_hash == token_hash)
            .first()
        )
        return self._to_entity(model) if model else None

    def mark_used(self, token_id: UUID) -> None:
        """Mark a password reset token as used."""
        model = (
            self.db.query(PasswordResetTokenModel)
            .filter(PasswordResetTokenModel.id == token_id)
            .first()
        )
        if model:
            model.is_used = True
            self.db.commit()

    @staticmethod
    def _to_entity(model: PasswordResetTokenModel) -> PasswordResetToken:
        """Convert ORM model to PasswordResetToken domain entity."""
        return PasswordResetToken(
            id=model.id,
            user_id=model.user_id,
            token_hash=model.token_hash,
            expires_at=model.expires_at,
            is_used=model.is_used,
            created_at=model.created_at,
        )
