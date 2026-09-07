"""Unit tests for configuration validation and production security gates."""

import pytest
from app.config import Settings


def test_development_defaults():
    """Verify development settings load with safe defaults."""
    cfg = Settings(APP_ENV="development")
    assert cfg.APP_ENV == "development"
    assert cfg.PROJECT_NAME == "AI Invoice Reconciliation Platform"


def test_production_rejects_default_dev_secret():
    """Verify production environment refuses to boot with insecure default JWT secret."""
    with pytest.raises(ValueError, match="JWT_SECRET_KEY must be a secure random key"):
        Settings(
            APP_ENV="production",
            JWT_SECRET_KEY="dev_secret_key_change_in_production_min_32_bytes_long_123",
        )


def test_production_rejects_short_secret():
    """Verify production environment refuses secrets under 32 characters."""
    with pytest.raises(ValueError, match="JWT_SECRET_KEY must be a secure random key of at least 32 chars"):
        Settings(
            APP_ENV="production",
            JWT_SECRET_KEY="too_short_secret",
        )


def test_production_rejects_debug_mode():
    """Verify production environment refuses to boot with DEBUG=True."""
    with pytest.raises(ValueError, match="DEBUG mode must be False in production"):
        Settings(
            APP_ENV="production",
            DEBUG=True,
            JWT_SECRET_KEY="a" * 32,
        )


def test_cors_origins_string_parsing():
    """Verify comma-separated string CORS origins are parsed into a list."""
    cfg = Settings(CORS_ORIGINS="http://localhost:3000,http://app.example.com")
    assert cfg.CORS_ORIGINS == ["http://localhost:3000", "http://app.example.com"]
