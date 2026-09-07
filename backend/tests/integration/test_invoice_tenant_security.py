"""Security and multi-tenant isolation tests for Invoice API.

Verifies strict tenant containment, IDOR attack prevention across all invoice and document routes,
cross-company invoice number coexistence, and rejection of unauthenticated requests.
"""

import io
import uuid
from fastapi.testclient import TestClient


def _register_and_login(client: TestClient, suffix: str) -> dict:
    """Helper to register a company and return its auth header and customer."""
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "company_name": f"Sec Company {suffix}",
            "email": f"sec_owner_{suffix}@test.com",
            "password": "SecurePassword123!",
            "full_name": f"Sec Owner {suffix}",
            "base_currency": "INR",
        },
    )
    assert resp.status_code == 201
    auth_data = resp.json()["data"]
    token = auth_data["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={"name": f"Sec Customer {suffix}"},
    )
    assert cust_resp.status_code == 201
    return {"headers": headers, "customer_id": cust_resp.json()["data"]["id"]}


def test_invoice_cross_tenant_isolation(client: TestClient) -> None:
    """Company B cannot view, modify, cancel, delete, or download Company A's invoices."""
    comp_a = _register_and_login(client, "A")
    comp_b = _register_and_login(client, "B")

    # 1. Company A uploads document and creates invoice
    file_bytes = b"%PDF-1.4 Company A Confidential Invoice"
    files = {"file": ("comp_a_invoice.pdf", io.BytesIO(file_bytes), "application/pdf")}
    upload_resp = client.post(
        "/api/v1/invoices/upload?auto_create_draft=false",
        headers=comp_a["headers"],
        files=files,
    )
    assert upload_resp.status_code == 201
    doc_id_a = upload_resp.json()["data"]["document"]["id"]

    inv_resp_a = client.post(
        "/api/v1/invoices",
        headers=comp_a["headers"],
        json={
            "invoice_number": "INV-A-SHARED-001",
            "customer_id": comp_a["customer_id"],
            "issue_date": "2026-09-01",
            "due_date": "2026-09-15",
            "total_amount": 50000.00,
            "document_id": doc_id_a,
        },
    )
    assert inv_resp_a.status_code == 201
    inv_id_a = inv_resp_a.json()["data"]["id"]

    # 2. Company B attempts IDOR on Company A's invoice
    # Detail
    assert client.get(f"/api/v1/invoices/{inv_id_a}", headers=comp_b["headers"]).status_code == 404
    # Document download
    assert client.get(f"/api/v1/invoices/{inv_id_a}/document", headers=comp_b["headers"]).status_code == 404
    # Update
    assert client.put(f"/api/v1/invoices/{inv_id_a}", headers=comp_b["headers"], json={"notes": "Hacked"}).status_code == 404
    # Cancel
    assert client.post(f"/api/v1/invoices/{inv_id_a}/cancel", headers=comp_b["headers"], json={"reason": "Attack"}).status_code == 404
    # Delete
    assert client.delete(f"/api/v1/invoices/{inv_id_a}", headers=comp_b["headers"]).status_code == 404
    # Confirm Draft
    assert client.post(
        f"/api/v1/invoices/{inv_id_a}/confirm-draft",
        headers=comp_b["headers"],
        json={"customer_id": comp_b["customer_id"]},
    ).status_code == 404
    # Archive
    assert client.post(f"/api/v1/invoices/{inv_id_a}/archive", headers=comp_b["headers"]).status_code == 404
    # Unarchive
    assert client.post(f"/api/v1/invoices/{inv_id_a}/unarchive", headers=comp_b["headers"]).status_code == 404

    # 3. Company B list and search isolation
    list_b = client.get("/api/v1/invoices", headers=comp_b["headers"]).json()["data"]
    assert all(inv["id"] != inv_id_a for inv in list_b["items"])

    search_b = client.get("/api/v1/invoices/search?q=INV-A", headers=comp_b["headers"]).json()["data"]
    assert search_b["total"] == 0

    # 4. Same invoice number permitted across distinct companies
    inv_resp_b = client.post(
        "/api/v1/invoices",
        headers=comp_b["headers"],
        json={
            "invoice_number": "INV-A-SHARED-001",  # Same invoice number as Company A
            "customer_id": comp_b["customer_id"],
            "issue_date": "2026-09-02",
            "due_date": "2026-09-16",
            "total_amount": 25000.00,
        },
    )
    assert inv_resp_b.status_code == 201
    assert inv_resp_b.json()["data"]["invoice_number"] == "INV-A-SHARED-001"


def test_unauthenticated_invoice_endpoints_rejected(client: TestClient) -> None:
    """Requests to protected invoice endpoints without valid authentication tokens return 401."""
    dummy_id = str(uuid.uuid4())

    assert client.get("/api/v1/invoices").status_code == 401
    assert client.get(f"/api/v1/invoices/{dummy_id}").status_code == 401
    assert client.post("/api/v1/invoices", json={}).status_code == 401
    assert client.put(f"/api/v1/invoices/{dummy_id}", json={}).status_code == 401
    assert client.delete(f"/api/v1/invoices/{dummy_id}").status_code == 401
    assert client.get(f"/api/v1/invoices/{dummy_id}/document").status_code == 401
    assert client.get("/api/v1/invoices/search?q=test").status_code == 401
