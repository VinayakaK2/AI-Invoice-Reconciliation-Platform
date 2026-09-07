"""Integration tests for Multi-Tenant Isolation & Security Defenses in Payment Module."""

import io
from fastapi.testclient import TestClient


def test_cross_tenant_idor_payment_detail(
    client: TestClient, registered_owner: dict, second_company_owner: dict
):
    """Assert Company B querying Company A's payment fails closed with 404 Not Found."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company A creates payment
    payload = {
        "transaction_date": "2026-03-15",
        "amount": 1000.00,
        "narration": "Payment for Company A",
        "reference_number": "REF-A-001",
    }
    resp = client.post("/api/v1/payments", headers=headers_a, json=payload)
    assert resp.status_code == 201
    payment_id = resp.json()["data"]["id"]

    # Company B attempts to access Company A's payment -> 404 (Never 403)
    resp_b = client.get(f"/api/v1/payments/{payment_id}", headers=headers_b)
    assert resp_b.status_code == 404


def test_cross_tenant_idor_ignore_and_unignore(
    client: TestClient, registered_owner: dict, second_company_owner: dict
):
    """Assert Company B attempting to ignore Company A's payment fails closed with 404."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company A creates payment
    payload = {
        "transaction_date": "2026-03-15",
        "amount": 2000.00,
        "narration": "Payment for Company A",
    }
    resp = client.post("/api/v1/payments", headers=headers_a, json=payload)
    payment_id = resp.json()["data"]["id"]

    # Company B tries to ignore Company A's payment -> 404
    ignore_payload = {"reason": "Malicious attempt"}
    resp_b = client.post(
        f"/api/v1/payments/{payment_id}/ignore",
        headers=headers_b,
        json=ignore_payload,
    )
    assert resp_b.status_code == 404

    # Company B tries to unignore Company A's payment -> 404
    unignore_resp_b = client.post(
        f"/api/v1/payments/{payment_id}/unignore",
        headers=headers_b,
    )
    assert unignore_resp_b.status_code == 404


def test_cross_tenant_idor_batch_detail(
    client: TestClient, registered_owner: dict, second_company_owner: dict
):
    """Assert Company B attempting to access Company A's batch details fails closed with 404."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    csv_content = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,Company A Transaction,5000.00,\n"
    ).encode("utf-8")

    upload_resp = client.post(
        "/api/v1/payments/upload-statement",
        headers=headers_a,
        files={"file": ("stmt_a.csv", io.BytesIO(csv_content), "text/csv")},
    )
    batch_id = upload_resp.json()["data"]["batch_id"]

    # Company B requests Company A's batch
    resp_b = client.get(f"/api/v1/payments/batches/{batch_id}", headers=headers_b)
    assert resp_b.status_code == 404


def test_tenant_list_isolation(
    client: TestClient, registered_owner: dict, second_company_owner: dict
):
    """Assert Company B payment and batch listing never leaks Company A records."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    # Company A uploads statement
    csv_content = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,Company A Receipt,3000.00,\n"
    ).encode("utf-8")

    client.post(
        "/api/v1/payments/upload-statement",
        headers=headers_a,
        files={"file": ("stmt_a.csv", io.BytesIO(csv_content), "text/csv")},
    )

    # Company B queries payments -> Expect 0
    resp_b_payments = client.get("/api/v1/payments", headers=headers_b)
    assert resp_b_payments.status_code == 200
    assert resp_b_payments.json()["data"]["total"] == 0

    # Company B queries batches -> Expect 0
    resp_b_batches = client.get("/api/v1/payments/batches", headers=headers_b)
    assert resp_b_batches.status_code == 200
    assert resp_b_batches.json()["data"]["total"] == 0


def test_cross_company_duplicate_reference_coexistence(
    client: TestClient, registered_owner: dict, second_company_owner: dict
):
    """Assert Company A and Company B can independently record payments with identical references."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    payload_a = {
        "transaction_date": "2026-03-10",
        "amount": 7500.00,
        "reference_number": "SHARED-UTR-777",
        "narration": "Payment to Company A",
    }
    resp_a = client.post("/api/v1/payments", headers=headers_a, json=payload_a)
    assert resp_a.status_code == 201

    payload_b = {
        "transaction_date": "2026-03-10",
        "amount": 7500.00,
        "reference_number": "SHARED-UTR-777",
        "narration": "Payment to Company B",
    }
    resp_b = client.post("/api/v1/payments", headers=headers_b, json=payload_b)
    assert resp_b.status_code == 201


def test_unauthenticated_request_rejected(client: TestClient):
    """Assert unauthenticated access to payment endpoints returns 401."""
    resp = client.get("/api/v1/payments")
    assert resp.status_code == 401

    resp_create = client.post(
        "/api/v1/payments",
        json={"transaction_date": "2026-03-15", "amount": 1000.00, "narration": "Test"},
    )
    assert resp_create.status_code == 401


def test_disallowed_extension_rejected(client: TestClient, registered_owner: dict):
    """Assert uploading non-CSV files (.pdf, .exe) is rejected with 400 Validation Error."""
    headers = registered_owner["headers"]

    files = {"file": ("malicious.exe", io.BytesIO(b"binary content"), "application/octet-stream")}
    resp = client.post("/api/v1/payments/upload-statement", headers=headers, files=files)
    assert resp.status_code == 400
    assert "not supported" in resp.json()["error"]["message"]


def test_file_upload_path_traversal_neutralized(client: TestClient, registered_owner: dict):
    """Assert path traversal in filename is sanitized without breaching tenant directory."""
    headers = registered_owner["headers"]

    csv_content = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,Valid payment,1000.00,\n"
    ).encode("utf-8")

    files = {"file": ("../../etc/passwd.csv", io.BytesIO(csv_content), "text/csv")}
    resp = client.post("/api/v1/payments/upload-statement", headers=headers, files=files)
    assert resp.status_code == 201
    assert resp.json()["data"]["imported_payments_count"] == 1
