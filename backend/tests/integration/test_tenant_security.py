"""Multi-tenant isolation and security tests for authentication and role boundaries."""

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.modules.auth.domain.entities import UserInvitation, UserRole
from app.modules.auth.infrastructure.models import UserModel
from app.modules.auth.infrastructure.repositories import InvitationRepository


def test_cross_tenant_isolation(
    client: TestClient,
    registered_owner: dict,
    second_company_owner: dict,
):
    """Verify complete tenant isolation between two distinct companies."""
    # Owner A requests company users
    res_a = client.get("/api/v1/company/users", headers=registered_owner["headers"])
    assert res_a.status_code == 200
    users_a = res_a.json()["data"]
    for u in users_a:
        assert u["company_id"] == registered_owner["company"]["id"]
        assert u["company_id"] != second_company_owner["company"]["id"]

    # Owner B requests company users
    res_b = client.get("/api/v1/company/users", headers=second_company_owner["headers"])
    assert res_b.status_code == 200
    users_b = res_b.json()["data"]
    for u in users_b:
        assert u["company_id"] == second_company_owner["company"]["id"]
        assert u["company_id"] != registered_owner["company"]["id"]


def test_accountant_forbidden_from_inviting_users(client: TestClient, registered_owner: dict):
    """Verify Role-Based Access Control: Accountants cannot invite new team members."""
    # 1. Owner invites an accountant
    invite_res = client.post(
        "/api/v1/company/users/invite",
        headers=registered_owner["headers"],
        json={"email": "staff@acmecorp.com", "role": "ACCOUNTANT"},
    )
    assert invite_res.status_code == 201
    token = invite_res.json()["data"]["invitation_token"]

    # 2. Accountant accepts and gets token
    accept_res = client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "full_name": "Junior Accountant", "password": "Password123!"},
    )
    assert accept_res.status_code == 201
    accountant_token = accept_res.json()["data"]["tokens"]["access_token"]
    accountant_headers = {"Authorization": f"Bearer {accountant_token}"}

    # 3. Accountant attempts to invite another user -> must be rejected with 403 Forbidden
    unauthorized_invite = client.post(
        "/api/v1/company/users/invite",
        headers=accountant_headers,
        json={"email": "another@acmecorp.com", "role": "ACCOUNTANT"},
    )
    assert unauthorized_invite.status_code == 403
    assert unauthorized_invite.json()["error"]["code"] == "FORBIDDEN"


def test_tampered_jwt_token_rejected(client: TestClient, registered_owner: dict):
    """Verify tampered JWT tokens fail cryptographically with 401 Unauthorized."""
    real_token = registered_owner["tokens"]["access_token"]
    # Tamper with token signature
    tampered_token = real_token[:-4] + "fake"
    headers = {"Authorization": f"Bearer {tampered_token}"}

    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401


def test_expired_invitation_rejected(client: TestClient, db_session: Session, registered_owner: dict):
    """Verify expired invitations cannot be accepted."""
    invite_repo = InvitationRepository(db_session)
    expired_token_str = "expired-token-xyz"
    invitation = UserInvitation(
        id=uuid.uuid4(),
        company_id=uuid.UUID(registered_owner["company"]["id"]),
        email="latecomer@acmecorp.com",
        role=UserRole.ACCOUNTANT,
        invitation_token=expired_token_str,
        expires_at=datetime.now(timezone.utc) - timedelta(days=2),
        is_accepted=False,
    )
    invite_repo.create(invitation)

    response = client.post(
        "/api/v1/auth/invitations/accept",
        json={
            "token": expired_token_str,
            "full_name": "Late Commer",
            "password": "Password123!",
        },
    )
    assert response.status_code == 400


def test_already_accepted_invitation_rejection(client: TestClient, registered_owner: dict):
    """Verify an invitation token cannot be re-used after being accepted."""
    invite_res = client.post(
        "/api/v1/company/users/invite",
        headers=registered_owner["headers"],
        json={"email": "first@acmecorp.com", "role": "ACCOUNTANT"},
    )
    token = invite_res.json()["data"]["invitation_token"]

    # First accept
    accept_1 = client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "full_name": "First Guy", "password": "Password123!"},
    )
    assert accept_1.status_code == 201

    # Second accept attempt with same token
    accept_2 = client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "full_name": "Second Guy", "password": "Password123!"},
    )
    assert accept_2.status_code == 409


def test_deactivated_user_cannot_access_api(client: TestClient, db_session: Session, registered_owner: dict):
    """Verify deactivated users are immediately blocked from API access."""
    # Deactivate user in database
    user_id = uuid.UUID(registered_owner["user"]["id"])
    db_user = db_session.query(UserModel).filter(UserModel.id == user_id).first()
    assert db_user is not None
    db_user.is_active = False
    db_session.commit()

    # Attempt to query /api/v1/auth/me with existing valid JWT
    response = client.get("/api/v1/auth/me", headers=registered_owner["headers"])
    assert response.status_code == 401
