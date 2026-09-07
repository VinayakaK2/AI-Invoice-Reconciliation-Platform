"""Application configuration module.

Loads environment variables using Pydantic Settings and enforces type validation.
"""

from typing import List, Union
from pydantic import AnyHttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global system configuration settings."""

    # Project metadata
    PROJECT_NAME: str = "AI Invoice Reconciliation Platform"
    VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/api/v1"
    APP_ENV: str = "development"
    DEBUG: bool = False

    # Database configuration
    # Default to local PostgreSQL 16 database
    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/invoice_reconciliation"
    ASYNC_DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/invoice_reconciliation"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_POOL_TIMEOUT: int = 30

    # Security and Authentication
    JWT_SECRET_KEY: str = "dev_secret_key_change_in_production_min_32_bytes_long_123"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # Storage and Document Upload configuration
    STORAGE_DIR: str = "storage/invoices"
    STATEMENT_STORAGE_DIR: str = "storage/statements"
    MAX_UPLOAD_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB limit
    ALLOWED_UPLOAD_EXTENSIONS: List[str] = [".pdf", ".png", ".jpg", ".jpeg"]
    ALLOWED_STATEMENT_EXTENSIONS: List[str] = [".csv", ".txt"]

    # CORS configuration
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        """Parse comma-separated CORS origins if provided as a string."""
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        """Enforce strict secret and debug policies in production environment."""
        if self.APP_ENV == "production":
            if "dev_secret" in self.JWT_SECRET_KEY or len(self.JWT_SECRET_KEY) < 32:
                raise ValueError("JWT_SECRET_KEY must be a secure random key of at least 32 chars in production.")
            if self.DEBUG:
                raise ValueError("DEBUG mode must be False in production.")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


# Singleton settings instance
settings = Settings()
