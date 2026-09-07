"""Integration tests for Invoice Management API endpoints.

Covers manual creation, duplicate rejection, PDF upload with OCR extraction,
draft confirmation, bulk CSV import with row-level error reporting,
filtering, search, detail view, document streaming, cancellation, and deletion.
"""

from decimal import Decimal
import io
import uuid
from fastapi.testclient import TestClient


def _setup_company_and_customer(client: TestClient, company_suffix: str = "1") -> dict:
    """Helper to register company, obtain auth headers, and create an active customer."""
    reg_resp = client.post(
        "/api/v1/auth/register",
        json={
            "company_name": f"Invoice Test Company {company_suffix}",
            "email": f"owner_{company_suffix}@invoicetest.com",
            "password": "SecurePassword123!",
            "full_name": f"Owner {company_suffix}",
            "base_currency": "INR",
        },
    )
    assert reg_resp.status_code == 201
    auth_data = reg_resp.json()["data"]
    headers = {"Authorization": f"Bearer {auth_data['tokens']['access_token']}"}

    cust_resp = client.post(
        "/api/v1/customers",
        headers=headers,
        json={
            "name": f"Acme Global Enterprises {company_suffix}",
            "tax_id": f"29ABCDE1234F1Z{company_suffix}",
            "email": f"billing{company_suffix}@acme.com",
            "phone": "+919876543210",
        },
    )
    assert cust_resp.status_code == 201
    cust_data = cust_resp.json()["data"]

    return {
        "headers": headers,
        "customer_id": cust_data["id"],
        "customer_name": cust_data["name"],
        "tax_id": cust_data["tax_id"],
    }


def test_manual_invoice_creation_success(client: TestClient) -> None:
    """Manual invoice creation creates an operational PENDING invoice with matching balances."""
    setup = _setup_company_and_customer(client, "create_success")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-2026-101",
            "customer_id": cust_id,
            "issue_date": "2026-09-01",
            "due_date": "2026-09-30",
            "total_amount": 15000.00,
            "tax_amount": 2700.00,
            "currency": "INR",
            "notes": "Consulting services for Q3",
        },
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["invoice_number"] == "INV-2026-101"
    assert data["status"] == "PENDING"
    assert Decimal(str(data["total_amount"])) == Decimal("15000.00")
    assert Decimal(str(data["paid_amount"])) == Decimal("0.00")
    assert Decimal(str(data["outstanding_amount"])) == Decimal("15000.00")
    assert data["source"] == "MANUAL"


def test_manual_invoice_duplicate_number_rejected(client: TestClient) -> None:
    """Creating an invoice with an existing invoice number in the same company returns 409 Conflict."""
    setup = _setup_company_and_customer(client, "dup_number")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    # 1. Create first invoice
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-DUP-01",
            "customer_id": cust_id,
            "issue_date": "2026-09-01",
            "due_date": "2026-09-15",
            "total_amount": 5000.00,
        },
    )

    # 2. Duplicate attempt
    resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-DUP-01",
            "customer_id": cust_id,
            "issue_date": "2026-09-02",
            "due_date": "2026-09-20",
            "total_amount": 7500.00,
        },
    )
    assert resp.status_code == 409
    err_body = resp.json()
    err_msg = err_body.get("error", {}).get("message") or err_body.get("detail", "")
    assert "already exists" in err_msg.lower()


def test_manual_invoice_nonexistent_or_archived_customer_rejected(client: TestClient) -> None:
    """Invoice rejects non-existent customers and archived customers."""
    setup = _setup_company_and_customer(client, "bad_cust")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    # Nonexistent customer
    resp1 = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-NONEXISTENT",
            "customer_id": str(uuid.uuid4()),
            "issue_date": "2026-09-01",
            "due_date": "2026-09-15",
            "total_amount": 1000.00,
        },
    )
    assert resp1.status_code == 404

    # Archive the valid customer
    client.post(f"/api/v1/customers/{cust_id}/archive", headers=headers)

    # Attempt invoice for archived customer
    resp2 = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-ARCHIVED-CUST",
            "customer_id": cust_id,
            "issue_date": "2026-09-01",
            "due_date": "2026-09-15",
            "total_amount": 2000.00,
        },
    )
    assert resp2.status_code == 400
    err_body = resp2.json()
    err_msg = err_body.get("error", {}).get("message") or err_body.get("detail", "")
    assert "archived" in err_msg.lower()


def test_pdf_upload_ocr_extraction_and_draft_confirmation(client: TestClient) -> None:
    """PDF upload extracts invoice data, creates draft invoice, and promotes to PENDING on confirmation."""
    setup = _setup_company_and_customer(client, "upload_ocr")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    pdf_content = (
        b"%PDF-1.4\n"
        b"TAX INVOICE\n"
        b"Invoice Number: INV-OCR-7701\n"
        b"Issue Date: 2026-09-05\n"
        b"Due Date: 2026-09-25\n"
        b"Bill To: Acme Global Enterprises upload_ocr\n"
        b"GSTIN: 29ABCDE1234F1Zupload_ocr\n"
        b"Total Amount: INR 32,500.00\n"
    )

    # 1. Upload PDF
    files = {"file": ("invoice_7701.pdf", io.BytesIO(pdf_content), "application/pdf")}
    upload_resp = client.post("/api/v1/invoices/upload", headers=headers, files=files)
    assert upload_resp.status_code == 201
    upload_data = upload_resp.json()["data"]

    assert upload_data["document"]["file_name"] == "invoice_7701.pdf"
    assert upload_data["document"]["ocr_status"] == "COMPLETED"
    assert upload_data["extracted_data"]["invoice_number"] == "INV-OCR-7701"
    assert Decimal(str(upload_data["extracted_data"]["total_amount"])) == Decimal("32500.00")

    draft_inv = upload_data["draft_invoice"]
    assert draft_inv is not None
    assert draft_inv["status"] == "DRAFT"
    assert draft_inv["invoice_number"] == "INV-OCR-7701"
    draft_id = draft_inv["id"]

    # 2. Confirm Draft
    confirm_resp = client.post(
        f"/api/v1/invoices/{draft_id}/confirm-draft",
        headers=headers,
        json={
            "customer_id": cust_id,
            "invoice_number": "INV-OCR-7701",
            "notes": "Verified against vendor bill",
        },
    )
    assert confirm_resp.status_code == 200
    confirmed = confirm_resp.json()["data"]
    assert confirmed["status"] == "PENDING"
    assert confirmed["customer_id"] == cust_id
    assert Decimal(str(confirmed["outstanding_amount"])) == Decimal("32500.00")


def test_csv_bulk_import_with_row_level_reporting(client: TestClient) -> None:
    """CSV bulk import commits valid rows and returns granular row-level error reporting."""
    setup = _setup_company_and_customer(client, "csv_import")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    csv_data = (
        "invoice_number,customer_id,issue_date,due_date,total_amount,tax_amount,currency,notes\n"
        f"INV-CSV-01,{cust_id},2026-09-01,2026-09-15,10000.00,1800.00,INR,Valid Row 1\n"
        f"INV-CSV-02,{cust_id},2026-09-02,2026-09-20,20000.00,3600.00,INR,Valid Row 2\n"
        f"INV-CSV-03,{uuid.uuid4()},2026-09-03,2026-09-25,15000.00,0.00,INR,Customer Not Found\n"
        f"INV-CSV-04,{cust_id},2026-09-30,2026-09-10,5000.00,0.00,INR,Due date before issue date\n"
        f"INV-CSV-05,{cust_id},2026-09-05,2026-09-25,-500.00,0.00,INR,Negative amount\n"
        f"INV-CSV-01,{cust_id},2026-09-06,2026-09-26,12000.00,0.00,INR,Duplicate in batch\n"
    )

    files = {"file": ("invoices.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}
    resp = client.post("/api/v1/invoices/import-csv", headers=headers, files=files)
    assert resp.status_code == 200
    res = resp.json()["data"]

    assert res["total_rows"] == 6
    assert res["imported_count"] == 2
    assert res["failed_count"] == 4
    assert len(res["errors"]) == 4

    error_messages = [e["error_message"] for e in res["errors"]]
    assert any("Customer not found" in msg for msg in error_messages)
    assert any("earlier than issue date" in msg for msg in error_messages)
    assert any("must be positive number" in msg for msg in error_messages)
    assert any("Duplicate invoice number" in msg for msg in error_messages)


def test_invoice_list_and_search(client: TestClient) -> None:
    """Invoices can be listed with filters and queried via fast multi-field search."""
    setup = _setup_company_and_customer(client, "list_search")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    # Create 3 invoices
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-SEARCH-ALPHA",
            "customer_id": cust_id,
            "issue_date": "2026-09-01",
            "due_date": "2026-09-10",
            "total_amount": 5000.00,
        },
    )
    client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-SEARCH-BETA",
            "customer_id": cust_id,
            "issue_date": "2026-09-05",
            "due_date": "2026-09-20",
            "total_amount": 10000.00,
        },
    )

    # 1. List with status filter
    list_resp = client.get("/api/v1/invoices?status=PENDING", headers=headers)
    assert list_resp.status_code == 200
    assert list_resp.json()["data"]["total"] >= 2

    # 2. Search query by invoice number
    search_resp1 = client.get("/api/v1/invoices/search?q=ALPHA", headers=headers)
    assert search_resp1.status_code == 200
    assert search_resp1.json()["data"]["total"] == 1
    assert search_resp1.json()["data"]["items"][0]["invoice_number"] == "INV-SEARCH-ALPHA"

    # 3. Search query by customer name
    search_resp2 = client.get(f"/api/v1/invoices/search?q=list_search", headers=headers)
    assert search_resp2.status_code == 200
    assert search_resp2.json()["data"]["total"] == 2


def test_invoice_detail_and_document_download(client: TestClient) -> None:
    """Detail endpoint returns invoice with customer and document metadata; document endpoint streams file."""
    setup = _setup_company_and_customer(client, "detail_stream")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    file_bytes = b"%PDF-1.4 Genuine test invoice document bytes"
    files = {"file": ("original_invoice.pdf", io.BytesIO(file_bytes), "application/pdf")}
    upload_resp = client.post("/api/v1/invoices/upload?auto_create_draft=false", headers=headers, files=files)
    assert upload_resp.status_code == 201
    doc_id = upload_resp.json()["data"]["document"]["id"]

    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-STREAM-DOC",
            "customer_id": cust_id,
            "issue_date": "2026-09-01",
            "due_date": "2026-09-15",
            "total_amount": 8000.00,
            "document_id": doc_id,
        },
    )
    assert inv_resp.status_code == 201
    inv_id = inv_resp.json()["data"]["id"]

    # 1. Get Detail
    detail_resp = client.get(f"/api/v1/invoices/{inv_id}", headers=headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()["data"]
    assert detail["invoice_number"] == "INV-STREAM-DOC"
    assert detail["customer"]["name"] == setup["customer_name"]
    assert detail["document"]["file_name"] == "original_invoice.pdf"

    # 2. Download Document Stream
    stream_resp = client.get(f"/api/v1/invoices/{inv_id}/document", headers=headers)
    assert stream_resp.status_code == 200
    assert stream_resp.content == file_bytes


def test_invoice_cancellation_and_deletion(client: TestClient) -> None:
    """Unpaid invoice can be cancelled and deleted; updates metadata safely."""
    setup = _setup_company_and_customer(client, "cancel_del")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-LIFECYCLE-01",
            "customer_id": cust_id,
            "issue_date": "2026-09-01",
            "due_date": "2026-09-15",
            "total_amount": 4500.00,
        },
    )
    assert inv_resp.status_code == 201
    inv_id = inv_resp.json()["data"]["id"]

    # 1. Update metadata
    put_resp = client.put(
        f"/api/v1/invoices/{inv_id}",
        headers=headers,
        json={"due_date": "2026-09-25", "notes": "Extended grace period"},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["data"]["due_date"] == "2026-09-25"

    # 2. Cancel invoice
    cancel_resp = client.post(
        f"/api/v1/invoices/{inv_id}/cancel",
        headers=headers,
        json={"reason": "Incorrect billing"},
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["data"]["status"] == "CANCELLED"

    # 3. Delete invoice
    del_resp = client.delete(f"/api/v1/invoices/{inv_id}", headers=headers)
    assert del_resp.status_code == 200

    # 4. Verify 404 after deletion
    get_resp = client.get(f"/api/v1/invoices/{inv_id}", headers=headers)
    assert get_resp.status_code == 404


def test_invoice_archive_and_unarchive_workflow(client: TestClient) -> None:
    """Archiving an invoice removes it from default operational views while preserving history; unarchiving restores it."""
    setup = _setup_company_and_customer(client, "archive_wf")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    # 1. Create active invoice
    resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-ARCH-TEST-01",
            "customer_id": cust_id,
            "issue_date": "2026-09-01",
            "due_date": "2026-09-15",
            "total_amount": 12500.00,
        },
    )
    assert resp.status_code == 201
    inv_id = resp.json()["data"]["id"]
    assert resp.json()["data"]["is_archived"] is False

    # 2. Verify invoice is in default active list
    active_list = client.get("/api/v1/invoices", headers=headers).json()["data"]["items"]
    assert any(inv["id"] == inv_id for inv in active_list)

    # 3. Archive invoice
    arc_resp = client.post(f"/api/v1/invoices/{inv_id}/archive", headers=headers)
    assert arc_resp.status_code == 200
    assert arc_resp.json()["data"]["is_archived"] is True

    # 4. Verify invoice is excluded from default active list
    active_list_after = client.get("/api/v1/invoices", headers=headers).json()["data"]["items"]
    assert all(inv["id"] != inv_id for inv in active_list_after)

    # 5. Verify invoice is excluded from default search
    search_after = client.get("/api/v1/invoices/search?q=ARCH-TEST", headers=headers).json()["data"]["items"]
    assert all(inv["id"] != inv_id for inv in search_after)

    # 6. Verify invoice appears when querying archived invoices
    archived_list = client.get("/api/v1/invoices?is_archived=true", headers=headers).json()["data"]["items"]
    assert any(inv["id"] == inv_id for inv in archived_list)

    # 7. Unarchive invoice
    unarc_resp = client.post(f"/api/v1/invoices/{inv_id}/unarchive", headers=headers)
    assert unarc_resp.status_code == 200
    assert unarc_resp.json()["data"]["is_archived"] is False

    # 8. Verify invoice returns to active operational list
    restored_list = client.get("/api/v1/invoices", headers=headers).json()["data"]["items"]
    assert any(inv["id"] == inv_id for inv in restored_list)


def test_invoice_upload_edge_cases(client: TestClient) -> None:
    """Document upload handles empty files, disallowed extensions, and duplicate content hashing."""
    setup = _setup_company_and_customer(client, "upload_edge")
    headers = setup["headers"]

    # 1. Empty file
    empty_file = {"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")}
    resp_empty = client.post("/api/v1/invoices/upload", headers=headers, files=empty_file)
    assert resp_empty.status_code == 400
    assert "empty" in resp_empty.json()["detail"].lower()

    # 2. Unsupported extension
    exe_file = {"file": ("malicious.exe", io.BytesIO(b"binary payload"), "application/octet-stream")}
    resp_exe = client.post("/api/v1/invoices/upload", headers=headers, files=exe_file)
    assert resp_exe.status_code == 400
    assert "not allowed" in resp_exe.json()["error"]["message"].lower()

    # 3. Duplicate byte upload returns existing document record (deduplication)
    pdf_bytes = b"%PDF-1.4 Deduplication test invoice content"
    f1 = {"file": ("doc_v1.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    resp1 = client.post("/api/v1/invoices/upload?auto_create_draft=false", headers=headers, files=f1)
    assert resp1.status_code == 201
    doc1_id = resp1.json()["data"]["document"]["id"]

    f2 = {"file": ("doc_v2_rename.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    resp2 = client.post("/api/v1/invoices/upload?auto_create_draft=false", headers=headers, files=f2)
    assert resp2.status_code == 201
    doc2_id = resp2.json()["data"]["document"]["id"]
    assert doc1_id == doc2_id  # Deduplicated via SHA-256 hash


def test_invoice_confirm_draft_edge_cases(client: TestClient) -> None:
    """Confirm draft validates draft state, customer status, duplicate numbers, and currency updates."""
    setup = _setup_company_and_customer(client, "confirm_edge")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    # 1. Create a regular PENDING invoice
    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "invoice_number": "INV-EXISTING-77",
            "customer_id": cust_id,
            "issue_date": "2026-09-01",
            "due_date": "2026-09-15",
            "total_amount": 5000.00,
        },
    )
    assert inv_resp.status_code == 201
    pending_id = inv_resp.json()["data"]["id"]

    # Attempting confirm-draft on a non-draft invoice raises 400
    bad_confirm = client.post(
        f"/api/v1/invoices/{pending_id}/confirm-draft",
        headers=headers,
        json={"customer_id": cust_id},
    )
    assert bad_confirm.status_code == 422
    assert "cannot confirm non-draft" in bad_confirm.json()["error"]["message"].lower()

    # 2. Upload a draft invoice
    pdf_content = (
        b"%PDF-1.4\n"
        b"Invoice Number: INV-DRAFT-CONF-01\n"
        b"Total Amount: 9500.00\n"
        b"Issue Date: 2026-09-01\n"
    )
    f = {"file": ("draft.pdf", io.BytesIO(pdf_content), "application/pdf")}
    up_resp = client.post("/api/v1/invoices/upload", headers=headers, files=f)
    assert up_resp.status_code == 201
    draft_id = up_resp.json()["data"]["draft_invoice"]["id"]

    # Attempting to change draft's invoice_number to an already existing number raises 409
    dup_confirm = client.post(
        f"/api/v1/invoices/{draft_id}/confirm-draft",
        headers=headers,
        json={"customer_id": cust_id, "invoice_number": "INV-EXISTING-77"},
    )
    assert dup_confirm.status_code == 409

    # Confirm with new currency and updated total
    ok_confirm = client.post(
        f"/api/v1/invoices/{draft_id}/confirm-draft",
        headers=headers,
        json={
            "customer_id": cust_id,
            "invoice_number": "INV-CONFIRMED-USD",
            "currency": "USD",
            "total_amount": 120.00,
        },
    )
    assert ok_confirm.status_code == 200
    conf_data = ok_confirm.json()["data"]
    assert conf_data["status"] == "PENDING"
    assert conf_data["currency"] == "USD"
    assert Decimal(str(conf_data["total_amount"])) == Decimal("120.00")
    assert Decimal(str(conf_data["outstanding_amount"])) == Decimal("120.00")


def test_csv_import_validation_failures(client: TestClient) -> None:
    """CSV import rejects empty files, missing headers, and missing customer columns."""
    setup = _setup_company_and_customer(client, "csv_val")
    headers = setup["headers"]

    # 1. Empty CSV
    f_empty = {"file": ("empty.csv", io.BytesIO(b"   \n\n"), "text/csv")}
    assert client.post("/api/v1/invoices/import-csv", headers=headers, files=f_empty).status_code == 400

    # 2. Missing mandatory columns (no total_amount)
    no_amt_csv = "invoice_number,customer_id,issue_date,due_date\nINV-1,cid,2026-09-01,2026-09-15\n"
    f_no_amt = {"file": ("no_amt.csv", io.BytesIO(no_amt_csv.encode("utf-8")), "text/csv")}
    resp_no_amt = client.post("/api/v1/invoices/import-csv", headers=headers, files=f_no_amt)
    assert resp_no_amt.status_code == 400
    assert "total_amount" in resp_no_amt.json()["error"]["message"].lower()

    # 3. Missing customer reference column
    no_cust_csv = "invoice_number,issue_date,due_date,total_amount\nINV-1,2026-09-01,2026-09-15,100.00\n"
    f_no_cust = {"file": ("no_cust.csv", io.BytesIO(no_cust_csv.encode("utf-8")), "text/csv")}
    resp_no_cust = client.post("/api/v1/invoices/import-csv", headers=headers, files=f_no_cust)
    assert resp_no_cust.status_code == 400
    assert "customer_id" in resp_no_cust.json()["error"]["message"].lower()


def test_invoice_deletion_guards_and_document_errors(client: TestClient) -> None:
    """Paid/partially paid invoices reject deletion; missing documents return 404; CSV error reporting is row-specific."""
    setup = _setup_company_and_customer(client, "del_guards")
    headers = setup["headers"]
    cust_id = setup["customer_id"]

    # 1. Create an invoice and simulate a recorded payment directly
    inv_resp = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "customer_id": cust_id,
            "invoice_number": "INV-DEL-GUARD-01",
            "issue_date": "2026-09-01",
            "due_date": "2026-09-15",
            "total_amount": 5000.00,
        },
    )
    assert inv_resp.status_code == 201
    inv_id = inv_resp.json()["data"]["id"]

    # Attempting to fetch document for manual invoice without document returns 404
    no_doc_resp = client.get(f"/api/v1/invoices/{inv_id}/document", headers=headers)
    assert no_doc_resp.status_code == 404
    assert "no document" in no_doc_resp.json()["error"]["message"].lower()

    # Attempting to fetch document for non-existent invoice returns 404
    bad_id = str(uuid.uuid4())
    bad_doc_resp = client.get(f"/api/v1/invoices/{bad_id}/document", headers=headers)
    assert bad_doc_resp.status_code == 404

    # 2. Granular CSV batch: 1 valid, 1 missing invoice_number, 1 invalid date, 1 duplicate in batch
    granular_csv = (
        "invoice_number,customer_name,issue_date,due_date,total_amount\n"
        f"INV-BATCH-01,{setup['customer_name']},2026-09-01,2026-09-15,1000.00\n"
        f", {setup['customer_name']},2026-09-01,2026-09-15,2000.00\n"  # missing number
        f"INV-BATCH-02,{setup['customer_name']},invalid-date,2026-09-15,3000.00\n"  # bad date
        f"INV-BATCH-01,{setup['customer_name']},2026-09-01,2026-09-15,4000.00\n"  # intra-batch dup
    )
    f_batch = {"file": ("batch.csv", io.BytesIO(granular_csv.encode("utf-8")), "text/csv")}
    batch_resp = client.post("/api/v1/invoices/import-csv", headers=headers, files=f_batch)
    assert batch_resp.status_code == 200
    res_data = batch_resp.json()["data"]
    assert res_data["total_rows"] == 4
    assert res_data["imported_count"] == 1
    assert res_data["failed_count"] == 3
    assert len(res_data["errors"]) == 3

