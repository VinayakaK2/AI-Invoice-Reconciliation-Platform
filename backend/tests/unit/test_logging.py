"""Unit tests for structured logging and sensitive credential sanitization."""

import logging
from app.core.logging import sanitize_log_message, JSONFormatter


def test_sanitize_bearer_token():
    """Verify Bearer authentication tokens are redacted from log messages."""
    raw = "User requested with Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz"
    sanitized = sanitize_log_message(raw)
    assert "Bearer [REDACTED]" in sanitized
    assert "eyJhbGciOiJIUzI1Ni" not in sanitized


def test_sanitize_password_and_secrets():
    """Verify password and secret parameters are redacted."""
    raw = 'Failed login with password="SuperSecretPassword123!" and secret=my_api_key_456'
    sanitized = sanitize_log_message(raw)
    assert "SuperSecretPassword123!" not in sanitized
    assert "my_api_key_456" not in sanitized
    assert "password=[REDACTED]" in sanitized
    assert "secret=[REDACTED]" in sanitized


def test_json_formatter_outputs_valid_json():
    """Verify JSONFormatter produces valid structured JSON with metadata."""
    import json
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Transaction processed for company",
        args=(),
        exc_info=None,
    )
    record.request_id = "req_12345"
    record.company_id = "comp_67890"

    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test_logger"
    assert parsed["requestId"] == "req_12345"
    assert parsed["companyId"] == "comp_67890"
    assert "timestamp" in parsed
