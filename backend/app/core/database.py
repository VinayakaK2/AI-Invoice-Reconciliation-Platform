"""Database session and connection engine management.

Provides synchronous and asynchronous SQLAlchemy 2.0 session factories.
"""

from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.config import settings
from app.core.logging import logger

# SQLAlchemy 2.0 Engine with connection pool
engine = create_engine(
    settings.DATABASE_URL,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_timeout=settings.DATABASE_POOL_TIMEOUT,
    pool_pre_ping=True,  # Test connections before checkout to handle disconnects
    echo=settings.DEBUG,
)

# Session factory for transactional operations
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

# Declarative base for ORM models
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency to yield a database session per request with automatic cleanup."""
    session = SessionLocal()
    try:
        yield session
    except Exception as exc:
        session.rollback()
        logger.error(f"Database session rolled back due to error: {exc}")
        raise
    finally:
        session.close()


def check_database_connection() -> bool:
    """Execute a simple SELECT 1 query to verify database readiness."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning(f"Database readiness probe failed: {exc}")
        return False
