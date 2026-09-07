"""Integration tests for Authentication API endpoints and user lifecycles."""

import pytest
from fastapi.testclient import TestClient


def test_registration_success(client: TestClient):
    """Verify company and owner user registration returns 201 and valid tokens."""
    payload = {
        "company_name": "Zenith Financials",
        "base_currency": "INR",
        "email": "owner@zenith.com",
        "password": "SecurePassword123!",
        "full_name": "Vikram Zenith",
    }
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    res = response.json()
    assert res["success"] is True

    data = res["data"]
    assert data["company"]["name"] == "Zenith Financials"
    assert data["company"]["base_currency"] == "INR"
    assert data["user"]["email"] == "owner@zenith.com"
    assert data["user"]["role"] == "OWNER"
    assert data["user"]["is_active"] is True
    assert "tokens" in data
    assert "access_token" in data["tokens"]
    assert "refresh_token" in data["tokens"]


def test_registration_duplicate_email_rejected(client: TestClient, registered_owner: dict):
    """Verify registering an email that already exists returns 409 Conflict."""
    payload = {
        "company_name": "Another Corp",
        "base_currency": "INR",
        "email": registered_owner["user"]["email"],
        "password": "AnotherPassword123!",
        "full_name": "Imposter User",
    }
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 409
    res = response.json()
    assert res["success"] is False
    assert res["error"]["code"] == "USER_EMAIL_ALREADY_EXISTS"


def test_registration_validation_short_password(client: TestClient):
    """Verify passwords shorter than 8 characters are rejected with 422."""
    payload = {
        "company_name": "Short Pass Co",
        "base_currency": "INR",
        "email": "user@shortpass.com",
        "password": "123",
        "full_name": "Shorty",
    }
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 422


def test_login_success(client: TestClient, registered_owner: dict):
    """Verify authenticating with correct credentials returns valid JWT tokens."""
    payload = {
        "email": registered_owner["user"]["email"],
        "password": registered_owner["password"],
    }
    response = client.post("/api/v1/auth/login", json=payload)
    assert response.status_code == 200
    res = response.json()
    assert res["success"] is True
    assert res["data"]["user"]["email"] == registered_owner["user"]["email"]
    assert "access_token" in res["data"]["tokens"]


def test_login_invalid_password(client: TestClient, registered_owner: dict):
    """Verify incorrect password returns 401 Unauthorized."""
    payload = {
        "email": registered_owner["user"]["email"],
        "password": "WrongPassword!",
    }
    response = client.post("/api/v1/auth/login", json=payload)
    assert response.status_code == 401
    assert response.json()["success"] is False


def test_login_unknown_email(client: TestClient):
    """Verify non-existent email returns 401 Unauthorized."""
    payload = {
        "email": "nonexistent@unknown.com",
        "password": "SomePassword123!",
    }
    response = client.post("/api/v1/auth/login", json=payload)
    assert response.status_code == 401


def test_get_me_authenticated(client: TestClient, registered_owner: dict):
    """Verify GET /api/v1/auth/me returns profile and company information."""
    response = client.get("/api/v1/auth/me", headers=registered_owner["headers"])
    assert response.status_code == 200
    res = response.json()
    assert res["success"] is True
    assert res["data"]["user"]["email"] == registered_owner["user"]["email"]
    assert res["data"]["company"]["name"] == registered_owner["company"]["name"]


def test_get_me_unauthenticated(client: TestClient):
    """Verify requesting /api/v1/auth/me without token returns 401 or 403."""
    response = client.get("/api/v1/auth/me")
    assert response.status_code in (401, 403)


def test_refresh_token_lifecycle(client: TestClient, registered_owner: dict):
    """Verify refreshing access token using valid refresh token."""
    refresh_token = registered_owner["tokens"]["refresh_token"]
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200
    res = response.json()
    assert res["success"] is True
    assert "access_token" in res["data"]["tokens"]


def test_refresh_token_invalid(client: TestClient):
    """Verify invalid refresh token is rejected."""
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": "bogus.token.here"})
    assert response.status_code == 401


def test_password_reset_workflow(client: TestClient, registered_owner: dict):
    """Verify complete password reset workflow: request, confirm, and login with new password."""
    # 1. Request reset
    req_res = client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": registered_owner["user"]["email"]},
    )
    assert req_res.status_code == 200
    reset_token = req_res.json()["data"]["reset_token"]
    assert reset_token is not None

    # 2. Confirm reset with new password
    new_password = "BrandNewPassword123!"
    confirm_res = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": reset_token, "new_password": new_password},
    )
    assert confirm_res.status_code == 200
    assert confirm_res.json()["success"] is True

    # 3. Login with old password fails
    old_login = client.post(
        "/api/v1/auth/login",
        json={"email": registered_owner["user"]["email"], "password": registered_owner["password"]},
    )
    assert old_login.status_code == 401

    # 4. Login with new password succeeds
    new_login = client.post(
        "/api/v1/auth/login",
        json={"email": registered_owner["user"]["email"], "password": new_password},
    )
    assert new_login.status_code == 200
    assert "access_token" in new_login.json()["data"]["tokens"]


def test_invitation_and_join_workflow(client: TestClient, registered_owner: dict):
    """Verify Owner invites an Accountant, who accepts and joins the company workspace."""
    # 1. Owner invites an accountant
    invite_res = client.post(
        "/api/v1/company/users/invite",
        headers=registered_owner["headers"],
        json={"email": "staff.accountant@acmecorp.com", "role": "ACCOUNTANT"},
    )
    assert invite_res.status_code == 201
    invite_data = invite_res.json()["data"]
    invitation_token = invite_data["invitation_token"]

    # 2. Accountant accepts invitation
    accept_res = client.post(
        "/api/v1/auth/invitations/accept",
        json={
            "token": invitation_token,
            "full_name": "Bob Accountant",
            "password": "AccountantPassword123!",
        },
    )
    assert accept_res.status_code == 201
    accountant_data = accept_res.json()["data"]
    assert accountant_data["user"]["role"] == "ACCOUNTANT"
    assert accountant_data["user"]["company_id"] == registered_owner["company"]["id"]

    # 3. List company users shows both owner and accountant
    users_res = client.get("/api/v1/company/users", headers=registered_owner["headers"])
    assert users_res.status_code == 200
    user_list = users_res.json()["data"]
    assert len(user_list) == 2
    roles = {u["role"] for u in user_list}
    assert roles == {"OWNER", "ACCOUNTANT"}


def test_logout_endpoint(client: TestClient, registered_owner: dict):
    """Verify POST /api/v1/auth/logout succeeds."""
    response = client.post("/api/v1/auth/logout", headers=registered_owner["headers"])
    assert response.status_code == 200
    assert response.json()["success"] is True
