"""Security integration tests verifying complete multi-tenant isolation (IDOR protection) for documents.

Verifies:
- Company A's uploaded documents cannot be accessed, read, streamed, corrected, promoted, retried, or deleted by Company B.
- Any attempt by Company B returns a 404 Not Found (information leakage prevention).
- Document listing queries strictly isolate tenant records.
"""

import io
import pytest
from starlette.testclient import TestClient


def _register_tenant(client: TestClient, tenant_suffix: str) -> dict:
    """Helper to register an isolated company and return authentication headers."""
    reg_payload = {
        "company_name": f"Tenant {tenant_suffix} Enterprise",
        "email": f"owner_{tenant_suffix}@tenantsecurity.com",
        "password": "SecurePassword123!",
        "full_name": f"Owner {tenant_suffix}",
        "base_currency": "INR",
    }
    resp = client.post("/api/v1/auth/register", json=reg_payload)
    assert resp.status_code == 201
    auth_data = resp.json()["data"]
    token = auth_data["tokens"]["access_token"]
    return {
        "headers": {"Authorization": f"Bearer {token}"},
        "company_id": auth_data["user"]["company_id"],
    }


def test_cross_tenant_document_idor_protection(client: TestClient) -> None:
    """Company B cannot perform any read or write operations on Company A's documents."""
    tenant_a = _register_tenant(client, "tenant_a")
    tenant_b = _register_tenant(client, "tenant_b")

    # 1. Tenant A uploads a document
    doc_content = (
        b"%PDF-1.4\n"
        b"Invoice Number: INV-TENANT-A-99\n"
        b"Total Amount: 85000.00\n"
        b"Issue Date: 2026-09-01\n"
    )
    files = {"file": ("confidential_a.pdf", io.BytesIO(doc_content), "application/pdf")}
    up_resp = client.post("/api/v1/invoices/upload?auto_create_draft=false", headers=tenant_a["headers"], files=files)
    assert up_resp.status_code == 201
    doc_a_id = up_resp.json()["data"]["document"]["id"]

    # 2. Tenant B attempts GET /documents/{id} -> 404 Not Found
    get_resp = client.get(f"/api/v1/invoices/documents/{doc_a_id}", headers=tenant_b["headers"])
    assert get_resp.status_code == 404

    # 3. Tenant B attempts GET /documents/{id}/file -> 404 Not Found
    file_resp = client.get(f"/api/v1/invoices/documents/{doc_a_id}/file", headers=tenant_b["headers"])
    assert file_resp.status_code == 404

    # 4. Tenant B attempts PUT /documents/{id}/correct -> 404 Not Found
    correct_payload = {"invoice_number": "MALICIOUS-OVERWRITE"}
    corr_resp = client.put(f"/api/v1/invoices/documents/{doc_a_id}/correct", headers=tenant_b["headers"], json=correct_payload)
    assert corr_resp.status_code == 404

    # 5. Tenant B attempts POST /documents/{id}/retry -> 404 Not Found
    retry_resp = client.post(f"/api/v1/invoices/documents/{doc_a_id}/retry", headers=tenant_b["headers"], json={})
    assert retry_resp.status_code == 404

    # 6. Tenant B attempts POST /documents/{id}/promote -> 404 Not Found
    promote_payload = {
        "customer_id": "00000000-0000-0000-0000-000000000001",
        "invoice_number": "HACKED-INV",
    }
    promote_resp = client.post(f"/api/v1/invoices/documents/{doc_a_id}/promote", headers=tenant_b["headers"], json=promote_payload)
    assert promote_resp.status_code == 404

    # 7. Tenant B attempts DELETE /documents/{id} -> 404 Not Found
    del_resp = client.delete(f"/api/v1/invoices/documents/{doc_a_id}", headers=tenant_b["headers"])
    assert del_resp.status_code == 404

    # 8. Tenant B lists documents: Tenant A's document MUST NOT appear
    list_b = client.get("/api/v1/invoices/documents", headers=tenant_b["headers"])
    assert list_b.status_code == 200
    doc_ids_b = [d["id"] for d in list_b.json()["data"]["items"]]
    assert doc_a_id not in doc_ids_b
