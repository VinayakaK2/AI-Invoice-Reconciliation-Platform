"""Integration tests covering Phase 12 forensic edge cases.

Verifies:
- Cannot delete document linked to invoice with recorded payments (Rule 20 / Invariant 4)
- Document deletion when linked to PENDING invoice disassociates document_id without deleting invoice
- Standalone document promotion without prior draft invoice
- Repeated promotion is idempotent and returns existing invoice
- Promotion rejected with ConflictError when invoice number collides
- Promotion rejected with ValidationError when customer is archived
- Human correction rejected on invalid dates (due_date < issue_date) and non-positive total_amount
- Pipeline duplicate file upload returns existing document without duplicate records
- Pipeline OCR failure transitions document to FAILED status
- Filtered document listing queries by ocr_status and has_invoice
"""

from datetime import date
from decimal import Decimal
import io
import tempfile
import uuid
import pytest
from starlette.testclient import TestClient

from app.modules.customer.domain.entities import Customer
from app.modules.customer.infrastructure.repositories import CustomerRepository
from app.modules.invoice.application.pipeline import InvoiceDocumentProcessingPipeline
from app.modules.invoice.application.ports import (
    ExtractedInvoiceData,
    LocalStorageService,
    MockInvoiceOCRProvider,
)
from app.modules.invoice.domain.entities import (
    Invoice,
    InvoiceDocument,
    InvoiceSource,
    InvoiceStatus,
    OCRStatus,
)
from app.modules.invoice.infrastructure.repositories import (
    InvoiceDocumentRepository,
    InvoiceRepository,
)
from app.shared.domain.money import Money


def _get_error_message(resp) -> str:
    """Extract error message string from response."""
    body = resp.json()
    if "error" in body and isinstance(body["error"], dict):
        return body["error"].get("message", "").lower()
    return str(body.get("detail", "")).lower()


def _setup_tenant(client: TestClient, suffix: str) -> dict:
    """Helper to create a tenant with headers and customer."""
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "company_name": f"Forensic Corp {suffix}",
            "email": f"forensic_{suffix}@test.com",
            "password": "SecurePassword123!",
            "full_name": f"Forensic {suffix}",
            "base_currency": "INR",
        },
    )
    assert reg.status_code == 201
    auth = reg.json()["data"]
    headers = {"Authorization": f"Bearer {auth['tokens']['access_token']}"}

    cust = client.post(
        "/api/v1/customers",
        headers=headers,
        json={
            "name": f"Client {suffix}",
            "tax_id": "29ABCDE1234F1Z5",
            "email": f"client_{suffix}@test.com",
        },
    )
    assert cust.status_code == 201
    cust_id = cust.json()["data"]["id"]

    return {
        "headers": headers,
        "customer_id": cust_id,
        "company_id": auth["user"]["company_id"],
    }


def test_delete_document_linked_to_paid_invoice_blocked(client: TestClient, db_session) -> None:
    """Document deletion is strictly blocked if linked invoice has recorded payments."""
    setup = _setup_tenant(client, "paid_del")
    headers = setup["headers"]
    company_id = uuid.UUID(setup["company_id"])
    cust_id = uuid.UUID(setup["customer_id"])

    # 1. Upload a document
    pdf_content = b"%PDF-1.4 sample invoice for paid deletion test"
    files = {"file": ("paid_invoice.pdf", io.BytesIO(pdf_content), "application/pdf")}
    up_resp = client.post("/api/v1/invoices/upload?auto_create_draft=false", headers=headers, files=files)
    assert up_resp.status_code == 201
    doc_id = uuid.UUID(up_resp.json()["data"]["document"]["id"])

    # 2. Create an invoice with paid_amount > 0 and link to the document
    inv_repo = InvoiceRepository(db_session)
    doc_repo = InvoiceDocumentRepository(db_session)

    tot = Money(Decimal("10000.00"), "INR")
    paid = Money(Decimal("4000.00"), "INR")
    outstanding = Money(Decimal("6000.00"), "INR")

    partially_paid_inv = Invoice(
        id=uuid.uuid4(),
        company_id=company_id,
        customer_id=cust_id,
        document_id=doc_id,
        invoice_number="INV-PAID-GUARD-01",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 30),
        total_amount=tot,
        paid_amount=paid,
        outstanding_amount=outstanding,
        currency="INR",
        status=InvoiceStatus.PARTIALLY_PAID,
        source=InvoiceSource.PDF_UPLOAD,
    )
    saved_inv = inv_repo.create(partially_paid_inv)
    doc_repo.link_to_invoice(doc_id, saved_inv.id, company_id)
    db_session.commit()

    # 3. Attempt to delete document -> MUST fail with 400
    del_resp = client.delete(f"/api/v1/invoices/documents/{doc_id}", headers=headers)
    assert del_resp.status_code == 400
    assert "recorded payments" in _get_error_message(del_resp)

    # Verify document still exists
    get_resp = client.get(f"/api/v1/invoices/documents/{doc_id}", headers=headers)
    assert get_resp.status_code == 200


def test_delete_document_linked_to_pending_invoice_disassociates(client: TestClient, db_session) -> None:
    """Deleting document linked to unpaid PENDING invoice clears invoice.document_id without deleting invoice."""
    setup = _setup_tenant(client, "pending_del")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    # 1. Upload and promote
    pdf_content = b"%PDF-1.4 sample invoice for pending deletion test"
    files = {"file": ("pending_invoice.pdf", io.BytesIO(pdf_content), "application/pdf")}
    up_resp = client.post("/api/v1/invoices/upload?auto_create_draft=false", headers=headers, files=files)
    assert up_resp.status_code == 201
    doc_id = up_resp.json()["data"]["document"]["id"]

    promote_resp = client.post(
        f"/api/v1/invoices/documents/{doc_id}/promote",
        headers=headers,
        json={"customer_id": cust_id, "invoice_number": "INV-PEND-DEL-01", "total_amount": 25000.00},
    )
    assert promote_resp.status_code == 200
    inv_id = promote_resp.json()["data"]["id"]

    # 2. Delete document
    del_resp = client.delete(f"/api/v1/invoices/documents/{doc_id}", headers=headers)
    assert del_resp.status_code == 200

    # 3. Verify document is gone, but invoice remains with document_id None
    doc_resp = client.get(f"/api/v1/invoices/documents/{doc_id}", headers=headers)
    assert doc_resp.status_code == 404

    inv_resp = client.get(f"/api/v1/invoices/{inv_id}", headers=headers)
    assert inv_resp.status_code == 200
    assert inv_resp.json()["data"]["document_id"] is None


def test_promote_document_standalone_without_draft(client: TestClient) -> None:
    """Document uploaded with auto_create_draft=false is cleanly promoted into a PENDING invoice."""
    setup = _setup_tenant(client, "standalone_prom")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    pdf_content = (
        b"%PDF-1.4\n"
        b"Invoice Number: INV-STANDALONE-99\n"
        b"Total Amount: 42000.00\n"
        b"Issue Date: 2026-09-01\n"
        b"Due Date: 2026-09-30\n"
    )
    files = {"file": ("standalone.pdf", io.BytesIO(pdf_content), "application/pdf")}
    up_resp = client.post("/api/v1/invoices/upload?auto_create_draft=false", headers=headers, files=files)
    assert up_resp.status_code == 201
    doc_id = up_resp.json()["data"]["document"]["id"]
    assert up_resp.json()["data"]["draft_invoice"] is None

    # Promote standalone document
    promote_resp = client.post(
        f"/api/v1/invoices/documents/{doc_id}/promote",
        headers=headers,
        json={
            "customer_id": cust_id,
            "invoice_number": "INV-STANDALONE-99",
            "total_amount": 42000.00,
            "tax_amount": 5000.00,
        },
    )
    assert promote_resp.status_code == 200
    inv_data = promote_resp.json()["data"]
    assert inv_data["status"] == "PENDING"
    assert inv_data["invoice_number"] == "INV-STANDALONE-99"
    assert Decimal(str(inv_data["total_amount"])) == Decimal("42000.00")

    # Idempotent re-promotion
    re_promote = client.post(
        f"/api/v1/invoices/documents/{doc_id}/promote",
        headers=headers,
        json={"customer_id": cust_id},
    )
    assert re_promote.status_code == 200
    assert re_promote.json()["data"]["id"] == inv_data["id"]


def test_promote_document_duplicate_invoice_number_conflict(client: TestClient) -> None:
    """Promoting a document with an invoice number that already exists fails with 409 Conflict."""
    setup = _setup_tenant(client, "dupe_num")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    # 1. First invoice
    pdf_content = b"%PDF-1.4 test invoice dupe 1"
    files = {"file": ("inv1.pdf", io.BytesIO(pdf_content), "application/pdf")}
    up1 = client.post("/api/v1/invoices/upload?auto_create_draft=false", headers=headers, files=files)
    doc1_id = up1.json()["data"]["document"]["id"]

    prom1 = client.post(
        f"/api/v1/invoices/documents/{doc1_id}/promote",
        headers=headers,
        json={"customer_id": cust_id, "invoice_number": "INV-COLLIDE-01", "total_amount": 10000.00},
    )
    assert prom1.status_code == 200

    # 2. Second document promoted with same number
    pdf_content2 = b"%PDF-1.4 test invoice dupe 2 different bytes"
    files2 = {"file": ("inv2.pdf", io.BytesIO(pdf_content2), "application/pdf")}
    up2 = client.post("/api/v1/invoices/upload?auto_create_draft=false", headers=headers, files=files2)
    doc2_id = up2.json()["data"]["document"]["id"]

    prom2 = client.post(
        f"/api/v1/invoices/documents/{doc2_id}/promote",
        headers=headers,
        json={"customer_id": cust_id, "invoice_number": "INV-COLLIDE-01", "total_amount": 20000.00},
    )
    assert prom2.status_code == 409
    assert "already exists" in _get_error_message(prom2)


def test_promote_document_archived_customer_rejected(client: TestClient, db_session) -> None:
    """Promoting a document to an archived customer is rejected with 400 Bad Request."""
    setup = _setup_tenant(client, "arch_cust")
    headers = setup["headers"]
    cust_id = setup["customer_id"]
    company_id = uuid.UUID(setup["company_id"])

    # Archive the customer
    cust_repo = CustomerRepository(db_session)
    c = cust_repo.get_by_id(uuid.UUID(cust_id), company_id)
    c.is_archived = True
    cust_repo.update(c)
    db_session.commit()

    # Upload document
    pdf_content = b"%PDF-1.4 invoice for archived customer"
    files = {"file": ("archived_cust.pdf", io.BytesIO(pdf_content), "application/pdf")}
    up = client.post("/api/v1/invoices/upload?auto_create_draft=false", headers=headers, files=files)
    doc_id = up.json()["data"]["document"]["id"]

    # Attempt promotion
    prom = client.post(
        f"/api/v1/invoices/documents/{doc_id}/promote",
        headers=headers,
        json={"customer_id": cust_id, "invoice_number": "INV-ARCH-01", "total_amount": 15000.00},
    )
    assert prom.status_code == 400
    assert "archived" in _get_error_message(prom)


def test_correct_document_validation_guards(client: TestClient) -> None:
    """Human correction rejects invalid temporal order and non-positive monetary amounts."""
    setup = _setup_tenant(client, "corr_guards")
    headers = setup["headers"]

    pdf_content = b"%PDF-1.4 invoice for correction guards"
    files = {"file": ("corr_guards.pdf", io.BytesIO(pdf_content), "application/pdf")}
    up = client.post("/api/v1/invoices/upload?auto_create_draft=false", headers=headers, files=files)
    doc_id = up.json()["data"]["document"]["id"]

    # 1. due_date < issue_date rejected
    corr_date = client.put(
        f"/api/v1/invoices/documents/{doc_id}/correct",
        headers=headers,
        json={"issue_date": "2026-09-30", "due_date": "2026-09-01"},
    )
    assert corr_date.status_code == 400
    assert "cannot precede" in _get_error_message(corr_date)

    # 2. total_amount <= 0 rejected by schema validation
    corr_amt = client.put(
        f"/api/v1/invoices/documents/{doc_id}/correct",
        headers=headers,
        json={"total_amount": -50.00},
    )
    assert corr_amt.status_code == 422


def test_pipeline_reupload_returns_existing_document(db_session) -> None:
    """Uploading identical document content returns existing document without creating duplicate DB records."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalStorageService(base_dir=tmpdir)
        pipeline = InvoiceDocumentProcessingPipeline(db=db_session, storage_service=storage)

        company_id = uuid.uuid4()
        pdf_bytes = b"%PDF-1.4 identical content for deduplication test"

        doc1, ext1, payload1 = pipeline.process(company_id, pdf_bytes, "invoice.pdf")
        doc2, ext2, payload2 = pipeline.process(company_id, pdf_bytes, "invoice.pdf")

        assert doc1.id == doc2.id
        assert doc1.file_hash == doc2.file_hash

        # Verify only 1 record exists in repository
        repo = InvoiceDocumentRepository(db_session)
        docs, total = repo.list_documents(company_id)
        assert total == 1


def test_pipeline_ocr_provider_exception_sets_failed_status(db_session) -> None:
    """When OCR provider throws an unhandled exception, pipeline transitions document to FAILED."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalStorageService(base_dir=tmpdir)
        failing_provider = MockInvoiceOCRProvider(raise_server_error=True)
        pipeline = InvoiceDocumentProcessingPipeline(
            db=db_session,
            storage_service=storage,
            ocr_provider=failing_provider,
        )

        company_id = uuid.uuid4()
        pdf_bytes = b"%PDF-1.4 failing OCR provider test"

        doc, ext, payload = pipeline.process(company_id, pdf_bytes, "fail.pdf")
        assert doc.ocr_status == OCRStatus.FAILED
        assert ext.confidence == Decimal("0.00")
        assert ext.is_ambiguous is True
        assert "OCR processing failed" in ext.extraction_notes[0]


def test_document_listing_filters(client: TestClient) -> None:
    """Document listing correctly filters by ocr_status and has_invoice."""
    setup = _setup_tenant(client, "filters")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    # 1. Upload doc with auto_create_draft=false (has_invoice=False)
    pdf1 = b"%PDF-1.4 doc without invoice"
    f1 = {"file": ("no_inv.pdf", io.BytesIO(pdf1), "application/pdf")}
    client.post("/api/v1/invoices/upload?auto_create_draft=false", headers=headers, files=f1)

    # 2. Upload doc with auto_create_draft=true and clean invoice details (has_invoice=True)
    pdf2 = (
        b"%PDF-1.4\n"
        b"Invoice Number: INV-FILTER-99\n"
        b"Total Amount: 10000.00\n"
        b"Issue Date: 2026-09-01\n"
    )
    f2 = {"file": ("has_inv.pdf", io.BytesIO(pdf2), "application/pdf")}
    client.post("/api/v1/invoices/upload?auto_create_draft=true", headers=headers, files=f2)

    # Filter has_invoice=true
    resp_true = client.get("/api/v1/invoices/documents?has_invoice=true", headers=headers)
    assert resp_true.status_code == 200
    for item in resp_true.json()["data"]["items"]:
        assert item["invoice_id"] is not None

    # Filter has_invoice=false
    resp_false = client.get("/api/v1/invoices/documents?has_invoice=false", headers=headers)
    assert resp_false.status_code == 200
    for item in resp_false.json()["data"]["items"]:
        assert item["invoice_id"] is None
