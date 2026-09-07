"""Unit tests for Authentication domain entities, value objects, and repository logic."""

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from app.core.security import hash_password, hash_token, verify_password
from app.modules.auth.domain.entities import PasswordResetToken, User, UserInvitation, UserRole
from app.modules.auth.infrastructure.repositories import (
    InvitationRepository,
    PasswordResetRepository,
    UserRepository,
)
from app.modules.company.domain.entities import Company
from app.modules.company.infrastructure.repositories import CompanyRepository


def test_user_entity_predicates():
    """Verify User entity role and tenant belonging predicates."""
    company_a_id = uuid.uuid4()
    company_b_id = uuid.uuid4()
    user_id = uuid.uuid4()

    owner_user = User(
        id=user_id,
        company_id=company_a_id,
        email="owner@test.com",
        hashed_password="hash",
        full_name="Owner User",
        role=UserRole.OWNER,
    )
    assert owner_user.is_owner() is True
    assert owner_user.belongs_to(company_a_id) is True
    assert owner_user.belongs_to(company_b_id) is False

    accountant_user = User(
        id=uuid.uuid4(),
        company_id=company_a_id,
        email="accountant@test.com",
        hashed_password="hash",
        full_name="Staff Accountant",
        role=UserRole.ACCOUNTANT,
    )
    assert accountant_user.is_owner() is False
    assert accountant_user.belongs_to(company_a_id) is True


def test_user_invitation_expiry_check():
    """Verify invitation expiration checks based on datetime comparison."""
    company_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    active_invite = UserInvitation(
        id=uuid.uuid4(),
        company_id=company_id,
        email="invitee@test.com",
        role=UserRole.ACCOUNTANT,
        invitation_token="valid-token-123",
        expires_at=now + timedelta(days=1),
    )
    assert active_invite.is_expired() is False

    expired_invite = UserInvitation(
        id=uuid.uuid4(),
        company_id=company_id,
        email="expired@test.com",
        role=UserRole.ACCOUNTANT,
        invitation_token="expired-token-456",
        expires_at=now - timedelta(hours=2),
    )
    assert expired_invite.is_expired() is True


def test_password_reset_token_validity():
    """Verify password reset token validity predicate."""
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    valid_token = PasswordResetToken(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash="hash-123",
        expires_at=now + timedelta(hours=1),
        is_used=False,
    )
    assert valid_token.is_valid() is True

    # Token already used
    used_token = PasswordResetToken(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash="hash-used",
        expires_at=now + timedelta(hours=1),
        is_used=True,
    )
    assert used_token.is_valid() is False

    # Token expired
    expired_token = PasswordResetToken(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash="hash-expired",
        expires_at=now - timedelta(minutes=10),
        is_used=False,
    )
    assert expired_token.is_valid() is False


def test_hash_token_deterministic():
    """Verify hash_token produces consistent SHA-256 hex string."""
    token = "super-secret-random-token"
    h1 = hash_token(token)
    h2 = hash_token(token)
    assert h1 == h2
    assert len(h1) == 64
    assert h1 != hash_token(token + "x")


def test_company_repository_crud(db_session):
    """Verify CompanyRepository creates, retrieves, and updates company entities."""
    repo = CompanyRepository(db_session)
    company_id = uuid.uuid4()
    company = Company(
        id=company_id,
        name="Stark Enterprises",
        base_currency="USD",
        is_active=True,
    )

    created = repo.create(company)
    assert created.id == company_id
    assert created.name == "Stark Enterprises"
    assert created.base_currency == "USD"

    fetched = repo.get_by_id(company_id)
    assert fetched is not None
    assert fetched.name == "Stark Enterprises"

    # Update company
    fetched.name = "Stark Global"
    updated = repo.update(fetched)
    assert updated.name == "Stark Global"


def test_user_repository_tenant_isolation(db_session):
    """Verify UserRepository enforces company_id tenant boundaries on lookups."""
    comp_repo = CompanyRepository(db_session)
    user_repo = UserRepository(db_session)

    c1 = comp_repo.create(Company(id=uuid.uuid4(), name="Company 1"))
    c2 = comp_repo.create(Company(id=uuid.uuid4(), name="Company 2"))

    u1 = user_repo.create(
        User(
            id=uuid.uuid4(),
            company_id=c1.id,
            email="shared@test.com",
            hashed_password=hash_password("Pass123!"),
            full_name="User One",
            role=UserRole.OWNER,
        )
    )

    # u1 can be fetched with matching company
    assert user_repo.get_by_id(u1.id, company_id=c1.id) is not None

    # u1 cannot be fetched with mismatched company
    assert user_repo.get_by_id(u1.id, company_id=c2.id) is None

    # Scoped email lookup
    assert user_repo.get_by_email_and_company("shared@test.com", c1.id) is not None
    assert user_repo.get_by_email_and_company("shared@test.com", c2.id) is None
