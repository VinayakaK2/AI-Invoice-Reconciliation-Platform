"""Unit tests for password hashing and JWT token handling."""

import pytest
from app.core.security import hash_password, verify_password, create_access_token, decode_access_token
from app.shared.exceptions import UnauthorizedError


def test_password_hashing_and_verification():
    """Verify passwords can be hashed and verified accurately."""
    pwd = "SecurePassword123!"
    hashed = hash_password(pwd)
    assert hashed != pwd
    assert verify_password(pwd, hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_jwt_token_lifecycle():
    """Verify JWT access token creation and decoding."""
    claims = {"sub": "user-uuid-123", "company_id": "comp-uuid-456", "role": "OWNER"}
    token = create_access_token(claims)
    assert isinstance(token, str)

    decoded = decode_access_token(token)
    assert decoded["sub"] == "user-uuid-123"
    assert decoded["company_id"] == "comp-uuid-456"
    assert decoded["role"] == "OWNER"
    assert "exp" in decoded


def test_invalid_jwt_token_raises_unauthorized():
    """Verify invalid JWT token raises UnauthorizedError."""
    with pytest.raises(UnauthorizedError):
        decode_access_token("invalid.jwt.token")
