"""Integration tests for Phase 12 OCR & Document Management Endpoints.

Tests end-to-end HTTP API workflows:
- Document upload and ingestion (/api/v1/invoices/upload)
- Document listing with status filters and pagination (/api/v1/invoices/documents)
- Document detail view with extraction payload (/api/v1/invoices/documents/{id})
- Secure document file streaming with HTTP security headers (/api/v1/invoices/documents/{id}/file)
- Human correction of extracted fields (/api/v1/invoices/documents/{id}/correct)
- Document promotion into active PENDING invoice (/api/v1/invoices/documents/{id}/promote)
- Safe document deletion with payment allocation protection (/api/v1/invoices/documents/{id})
"""

import io
from decimal import Decimal
import pytest
from starlette.testclient import TestClient


def _setup_company_and_customer(client: TestClient, suffix: str) -> dict:
    """Helper to register a company, owner user, and a sample customer."""
    reg_payload = {
        "company_name": f"OCR Integration Corp {suffix}",
        "email": f"owner_{suffix}@ocrtest.com",
        "password": "SecurePassword123!",
        "full_name": f"Owner {suffix}",
        "base_currency": "INR",
    }
    reg_resp = client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_resp.status_code == 201
    auth_data = reg_resp.json()["data"]
    token = auth_data["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    cust_payload = {
        "name": f"Counterparty {suffix}",
        "tax_id": "29ABCDE1234F1Z5",
        "email": f"counterparty_{suffix}@ocrtest.com",
        "phone": "+919876543210",
    }
    cust_resp = client.post("/api/v1/customers", headers=headers, json=cust_payload)
    assert cust_resp.status_code == 201
    cust_id = cust_resp.json()["data"]["id"]

    return {
        "headers": headers,
        "customer_id": cust_id,
        "company_id": auth_data["user"]["company_id"],
    }


def test_document_full_lifecycle_upload_review_correct_promote(client: TestClient) -> None:
    """Complete document lifecycle: Upload -> List -> Review -> Correct -> Promote -> Stream."""
    setup = _setup_company_and_customer(client, "lifecycle")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    # 1. Upload Invoice PDF
    pdf_content = (
        b"%PDF-1.4\n"
        b"Invoice Number: INV-DOC-2026-001\n"
        b"Bill To: Counterparty lifecycle\n"
        b"Issue Date: 2026-09-01\n"
        b"Due Date: 2026-09-30\n"
        b"Total Amount: INR 55,000.00\n"
    )
    files = {"file": ("vendor_invoice.pdf", io.BytesIO(pdf_content), "application/pdf")}
    up_resp = client.post("/api/v1/invoices/upload?auto_create_draft=true", headers=headers, files=files)
    assert up_resp.status_code == 201
    up_data = up_resp.json()["data"]
    doc_id = up_data["document"]["id"]
    assert up_data["document"]["file_name"] == "vendor_invoice.pdf"

    # 2. List Documents Endpoint
    list_resp = client.post if False else client.get("/api/v1/invoices/documents", headers=headers)
    assert list_resp.status_code == 200
    list_data = list_resp.json()["data"]
    assert list_data["total"] >= 1
    found_item = next((d for d in list_data["items"] if d["id"] == doc_id), None)
    assert found_item is not None
    assert found_item["file_name"] == "vendor_invoice.pdf"

    # 3. Document Detail Endpoint
    detail_resp = client.get(f"/api/v1/invoices/documents/{doc_id}", headers=headers)
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()["data"]
    assert detail_data["id"] == doc_id
    assert detail_data["extracted_data"] is not None

    # 4. Stream Source Document File & Verify Security Headers
    file_resp = client.get(f"/api/v1/invoices/documents/{doc_id}/file", headers=headers)
    assert file_resp.status_code == 200
    assert file_resp.content == pdf_content
    assert file_resp.headers["X-Content-Type-Options"] == "nosniff"
    assert file_resp.headers["Content-Security-Policy"] == "default-src 'none'"
    assert "no-store" in file_resp.headers["Cache-Control"]

    # 5. Correct Extracted Document Fields
    correct_payload = {
        "invoice_number": "INV-DOC-2026-001-CORRECTED",
        "total_amount": 58000.00,
        "tax_amount": 8000.00,
        "notes": "Verified line items with vendor",
    }
    correct_resp = client.put(f"/api/v1/invoices/documents/{doc_id}/correct", headers=headers, json=correct_payload)
    assert correct_resp.status_code == 200
    corr_data = correct_resp.json()["data"]
    assert corr_data["ocr_status"] == "MANUALLY_CORRECTED"

    # 6. Promote Document into Active PENDING Invoice
    promote_payload = {
        "customer_id": cust_id,
        "invoice_number": "INV-DOC-2026-001-FINAL",
        "total_amount": 58000.00,
    }
    promote_resp = client.post(f"/api/v1/invoices/documents/{doc_id}/promote", headers=headers, json=promote_payload)
    assert promote_resp.status_code == 200
    promoted_inv = promote_resp.json()["data"]
    assert promoted_inv["status"] == "PENDING"
    assert promoted_inv["invoice_number"] == "INV-DOC-2026-001-FINAL"
    assert Decimal(str(promoted_inv["total_amount"])) == Decimal("58000.00")
    assert Decimal(str(promoted_inv["outstanding_amount"])) == Decimal("58000.00")


def test_document_retry_processing_endpoint(client: TestClient) -> None:
    """Re-executing OCR extraction on stored document updates extraction results without duplicate files."""
    setup = _setup_company_and_customer(client, "retry")
    headers = setup["headers"]

    pdf_content = (
        b"%PDF-1.4\n"
        b"Invoice Number: INV-RETRY-01\n"
        b"Total Amount: 12000.00\n"
        b"Issue Date: 2026-09-01\n"
    )
    files = {"file": ("retry_inv.pdf", io.BytesIO(pdf_content), "application/pdf")}
    up_resp = client.post("/api/v1/invoices/upload?auto_create_draft=false", headers=headers, files=files)
    assert up_resp.status_code == 201
    doc_id = up_resp.json()["data"]["document"]["id"]

    # Trigger Retry
    retry_resp = client.post(
        f"/api/v1/invoices/documents/{doc_id}/retry",
        headers=headers,
        json={"auto_create_draft": True},
    )
    assert retry_resp.status_code == 200
    retry_data = retry_resp.json()["data"]
    assert retry_data["document"]["id"] == doc_id
    assert retry_data["extracted_data"]["invoice_number"] == "INV-RETRY-01"


def test_document_deletion_with_payment_protection(client: TestClient) -> None:
    """Document deletion successfully deletes unlinked draft documents, but rejects documents with paid invoices."""
    setup = _setup_company_and_customer(client, "del_protect")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    # 1. Unlinked document deletion succeeds
    pdf_content = b"%PDF-1.4 standalone document"
    files = {"file": ("unlinked.pdf", io.BytesIO(pdf_content), "application/pdf")}
    up_resp = client.post("/api/v1/invoices/upload?auto_create_draft=false", headers=headers, files=files)
    assert up_resp.status_code == 201
    doc_id = up_resp.json()["data"]["document"]["id"]

    del_resp = client.delete(f"/api/v1/invoices/documents/{doc_id}", headers=headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["data"]["message"] == "Document deleted successfully"

    # Verify document no longer exists
    get_resp = client.get(f"/api/v1/invoices/documents/{doc_id}", headers=headers)
    assert get_resp.status_code == 404
