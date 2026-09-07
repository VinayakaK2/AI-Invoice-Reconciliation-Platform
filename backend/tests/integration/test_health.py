"""Integration tests for health probes, correlation IDs, and module endpoints."""

from fastapi.testclient import TestClient


def test_liveness_check(client: TestClient):
    """Verify GET /health returns 200 and expected payload."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "service" in data
    assert "version" in data
    assert "environment" in data


def test_live_probe(client: TestClient):
    """Verify GET /health/live returns 200."""
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_correlation_id_and_timing_headers(client: TestClient):
    """Verify request correlation ID and latency headers are injected into response."""
    response = client.get("/health")
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Request-ID"].startswith("req_")
    assert "X-Process-Time-Ms" in response.headers

    # Verify custom supplied X-Request-ID is preserved
    custom_id = "test-custom-id-999"
    response_custom = client.get("/health", headers={"X-Request-ID": custom_id})
    assert response_custom.headers["X-Request-ID"] == custom_id


def test_v1_module_skeletons(client: TestClient):
    """Verify modular presentation routers are mounted and responding under /api/v1."""
    modules = [
        "auth",
        "company",
        "customers",
        "invoices",
        "payments",
        "reconciliation",
        "review",
        "dashboard",
        "audit",
    ]

    for mod in modules:
        response = client.get(f"/api/v1/{mod}/status")
        assert response.status_code == 200, f"Module router for {mod} failed"
        data = response.json()
        assert data["status"] == "initialized"


def test_readiness_probe_success(client: TestClient, monkeypatch):
    """Verify /health/ready returns 200 when database connectivity succeeds."""
    from app.api.v1 import health
    monkeypatch.setattr(health, "check_database_connection", lambda: True)
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "connected"}


def test_readiness_probe_failure(client: TestClient, monkeypatch):
    """Verify /health/ready returns 503 without crashing when database is unreachable."""
    from app.api.v1 import health
    monkeypatch.setattr(health, "check_database_connection", lambda: False)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"
    assert response.json()["database"] == "disconnected"

