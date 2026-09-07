"""Structured logging module for production observability.

Outputs JSON formatted logs in production and human-readable logs in development.
"""

import logging
import sys
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict
from app.config import settings

# Sensitive patterns that must be redacted from logs
SENSITIVE_PATTERNS = [
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), "Bearer [REDACTED]"),
    (re.compile(r"(password|secret|token)\s*[:=]\s*['\"]?[^'\"\s,]+['\"]?", re.IGNORECASE), r"\1=[REDACTED]"),
]


def sanitize_log_message(message: str) -> str:
    """Scrub sensitive credentials such as passwords and JWTs from log messages."""
    sanitized = message
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


class JSONFormatter(logging.Formatter):
    """Custom formatter that serializes log records as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as a structured JSON string."""
        raw_message = record.getMessage()
        sanitized_message = sanitize_log_message(raw_message)

        log_payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": sanitized_message,
        }

        # Include custom attributes attached to the record
        if hasattr(record, "request_id"):
            log_payload["requestId"] = getattr(record, "request_id")
        if hasattr(record, "company_id"):
            log_payload["companyId"] = getattr(record, "company_id")
        if hasattr(record, "user_id"):
            log_payload["userId"] = getattr(record, "user_id")

        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_payload)


class SanitizingFilter(logging.Filter):
    """Filter that scrubs sensitive credentials from log records before emission."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Sanitize string messages in the log record."""
        if isinstance(record.msg, str):
            record.msg = sanitize_log_message(record.msg)
        return True


def setup_logging() -> logging.Logger:
    """Configure the root logger and return the application logger."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)

    # Clear existing handlers
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(SanitizingFilter())
    if settings.APP_ENV == "production":
        handler.setFormatter(JSONFormatter())
    else:
        # Clear human-readable formatter for local development
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)

    root_logger.addHandler(handler)
    return logging.getLogger("invoice_platform")


logger = setup_logging()
