"""Integration tests for Payment & Bank Statement Ingestion REST API."""

from decimal import Decimal
import io
from fastapi.testclient import TestClient


def test_statement_upload_endpoint_success(client: TestClient, registered_owner: dict):
    """Test full multipart/form-data bank statement CSV upload workflow."""
    headers = registered_owner["headers"]

    csv_content = (
        "Date,Narration,Ref No,Deposit,Withdrawal,Balance\n"
        "2026-03-01,Transfer from Acme Alpha,UTR112233,25000.00,,25000.00\n"
        "2026-03-02,Bank SMS Charges,CHG101,,25.00,24975.00\n"
        "2026-03-03,Transfer from Acme Beta,UTR445566,15000.00,,39975.00\n"
    ).encode("utf-8")

    files = {"file": ("statement_march.csv", io.BytesIO(csv_content), "text/csv")}
    data = {"bank_account_number": "9876543210", "default_currency": "INR"}

    response = client.post(
        "/api/v1/payments/upload-statement",
        headers=headers,
        files=files,
        data=data,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    res_data = body["data"]
    assert res_data["total_rows"] == 3
    assert res_data["imported_payments_count"] == 2  # Credits only
    assert res_data["skipped_debits_count"] == 1    # Debit skipped from receivable payments
    assert res_data["failed_rows_count"] == 0
    assert len(res_data["imported_payment_ids"]) == 2


def test_list_payments_and_filters(client: TestClient, registered_owner: dict):
    """Test payment listing, search, and status filtering."""
    headers = registered_owner["headers"]

    csv_content = (
        "Date,Narration,Ref No,Deposit,Withdrawal\n"
        "2026-03-10,Direct Payment Zeta,REF-ZETA,12000.00,\n"
        "2026-03-11,Direct Payment Theta,REF-THETA,8500.00,\n"
    ).encode("utf-8")

    client.post(
        "/api/v1/payments/upload-statement",
        headers=headers,
        files={"file": ("stmt.csv", io.BytesIO(csv_content), "text/csv")},
    )

    # 1. List all payments
    resp = client.get("/api/v1/payments", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 2
    assert len(data["items"]) == 2

    # 2. Search by narration / reference
    search_resp = client.get("/api/v1/payments?search=ZETA", headers=headers)
    assert search_resp.status_code == 200
    search_data = search_resp.json()["data"]
    assert search_data["total"] == 1
    assert search_data["items"][0]["reference_number"] == "REF-ZETA"

    # 3. Filter by status
    status_resp = client.get("/api/v1/payments?status=UNRECONCILED", headers=headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["data"]["total"] == 2


def test_get_payment_detail(client: TestClient, registered_owner: dict):
    """Test fetching single payment details with masked bank account."""
    headers = registered_owner["headers"]

    csv_content = (
        "Date,Narration,Ref No,Deposit,Withdrawal\n"
        "2026-03-15,Payment Detail Test,REF-DETAIL-1,4500.00,\n"
    ).encode("utf-8")

    upload_resp = client.post(
        "/api/v1/payments/upload-statement",
        headers=headers,
        files={"file": ("detail.csv", io.BytesIO(csv_content), "text/csv")},
        data={"bank_account_number": "1234567890"},
    )
    payment_id = upload_resp.json()["data"]["imported_payment_ids"][0]

    resp = client.get(f"/api/v1/payments/{payment_id}", headers=headers)
    assert resp.status_code == 200
    p = resp.json()["data"]
    assert p["id"] == payment_id
    assert Decimal(str(p["amount"])) == Decimal("4500.00")
    assert p["masked_bank_account"] == "••••••••7890"
    assert p["status"] == "UNRECONCILED"


def test_manual_payment_creation_and_conflict(client: TestClient, registered_owner: dict):
    """Test manual payment receipt creation and reference deduplication."""
    headers = registered_owner["headers"]

    payload = {
        "transaction_date": "2026-03-20",
        "amount": 3000.00,
        "currency": "INR",
        "narration": "Direct cash receipt from customer",
        "reference_number": "MANUAL-REF-100",
        "bank_account_number": "555544443333",
        "payer_raw_name": "John Doe",
    }

    resp = client.post("/api/v1/payments", headers=headers, json=payload)
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert Decimal(str(data["amount"])) == Decimal("3000.00")
    assert data["reference_number"] == "MANUAL-REF-100"
    assert data["source"] == "MANUAL"
    assert data["masked_bank_account"] == "••••••••3333"

    # Second creation with same reference -> 409 Conflict
    conflict_resp = client.post("/api/v1/payments", headers=headers, json=payload)
    assert conflict_resp.status_code == 409


def test_ignore_and_unignore_workflow(client: TestClient, registered_owner: dict):
    """Test accountant ignore and unignore API endpoints."""
    headers = registered_owner["headers"]

    # Create payment
    payload = {
        "transaction_date": "2026-03-22",
        "amount": 500.00,
        "narration": "Non-sales interest receipt",
    }
    create_resp = client.post("/api/v1/payments", headers=headers, json=payload)
    payment_id = create_resp.json()["data"]["id"]

    # 1. Ignore payment
    ignore_payload = {"reason": "Non-invoice bank interest receipt"}
    ignore_resp = client.post(
        f"/api/v1/payments/{payment_id}/ignore",
        headers=headers,
        json=ignore_payload,
    )
    assert ignore_resp.status_code == 200
    assert ignore_resp.json()["data"]["status"] == "IGNORED"

    # 2. Unignore payment
    unignore_resp = client.post(
        f"/api/v1/payments/{payment_id}/unignore",
        headers=headers,
    )
    assert unignore_resp.status_code == 200
    assert unignore_resp.json()["data"]["status"] == "UNRECONCILED"


def test_batch_list_and_detail(client: TestClient, registered_owner: dict):
    """Test querying batch summary list and batch transaction detail."""
    headers = registered_owner["headers"]

    csv_content = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,Credit Txn,1000.00,\n"
        "2026-03-02,Debit Txn,,200.00\n"
    ).encode("utf-8")

    upload_resp = client.post(
        "/api/v1/payments/upload-statement",
        headers=headers,
        files={"file": ("batch_test.csv", io.BytesIO(csv_content), "text/csv")},
    )
    batch_id = upload_resp.json()["data"]["batch_id"]

    # List batches
    batches_resp = client.get("/api/v1/payments/batches", headers=headers)
    assert batches_resp.status_code == 200
    assert batches_resp.json()["data"]["total"] >= 1

    # Get batch detail
    batch_detail_resp = client.get(f"/api/v1/payments/batches/{batch_id}", headers=headers)
    assert batch_detail_resp.status_code == 200
    detail_data = batch_detail_resp.json()["data"]
    assert detail_data["batch"]["id"] == batch_id
    assert len(detail_data["transactions"]) == 2


def test_list_raw_bank_transactions_filter(client: TestClient, registered_owner: dict):
    """Test listing raw bank transactions filtered by credit or debit."""
    headers = registered_owner["headers"]

    csv_content = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,Credit 1,1000.00,\n"
        "2026-03-02,Debit 1,,250.00\n"
        "2026-03-03,Debit 2,,350.00\n"
    ).encode("utf-8")

    client.post(
        "/api/v1/payments/upload-statement",
        headers=headers,
        files={"file": ("raw_txns.csv", io.BytesIO(csv_content), "text/csv")},
    )

    # Query only debits
    debit_resp = client.get("/api/v1/payments/transactions?txn_type=DEBIT", headers=headers)
    assert debit_resp.status_code == 200
    debit_data = debit_resp.json()["data"]
    assert debit_data["total"] == 2
    assert all(item["transaction_type"] == "DEBIT" for item in debit_data["items"])
