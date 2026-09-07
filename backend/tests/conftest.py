"""Pytest fixtures and test environment setup.

Configures an isolated in-memory SQLite database and TestClient with dependency overrides.
"""

from typing import Dict, Generator
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import Base, get_db
from app.main import app

# In-memory SQLite engine configured for single-connection concurrency in tests
TEST_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine,
    expire_on_commit=False,
)


@pytest.fixture(scope="function")
def db_session() -> Generator[Session, None, None]:
    """Create fresh schema tables and yield a clean database session for each test."""
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """Provide a TestClient instance with get_db overridden to use the test session."""

    def override_get_db() -> Generator[Session, None, None]:
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def registered_owner(client: TestClient) -> Dict:
    """Register a test company and owner, returning profile and authorization headers."""
    payload = {
        "company_name": "Acme Corp Ltd",
        "base_currency": "INR",
        "email": "owner@acmecorp.com",
        "password": "Password123!",
        "full_name": "Alice Acme",
    }
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()["data"]
    access_token = data["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    return {
        "user": data["user"],
        "company": data["company"],
        "tokens": data["tokens"],
        "headers": headers,
        "password": payload["password"],
    }


@pytest.fixture(scope="function")
def second_company_owner(client: TestClient) -> Dict:
    """Register a distinct second company and owner for multi-tenant isolation tests."""
    payload = {
        "company_name": "Beta Industries",
        "base_currency": "USD",
        "email": "owner@betaindustries.com",
        "password": "Password456!",
        "full_name": "Bob Beta",
    }
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()["data"]
    access_token = data["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    return {
        "user": data["user"],
        "company": data["company"],
        "tokens": data["tokens"],
        "headers": headers,
        "password": payload["password"],
    }
