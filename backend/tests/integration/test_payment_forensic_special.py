"""Forensic Special Verification Tests for Phase 12 (Payment & Bank Statement Ingestion).

Verifies the 10 mandatory forensic audit criteria:
1. Signed amount (+10000.00, -5000.00) in Layout C
2. Formula-like narration sanitized with quote while formula in amount rejected
3. Exact duplicate file returns HTTP 409 Conflict via API
4. Failed import retry permitted without false conflict lock
5. Concurrent duplicate import raises ConflictError rather than unhandled 500
6. Fallback collision disambiguation with statement running balance
7. Cross-tenant raw bank transaction listing isolation
8. Allocation mutation endpoints strictly unavailable (404/405)
9. Zero invoice mutation during bank statement ingestion
10. Alembic migration 0005 upgrade/downgrade/re-upgrade lifecycle
"""

from datetime import date
from decimal import Decimal
import io
from pathlib import Path
import tempfile
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.invoice.infrastructure.models import InvoiceModel
from app.modules.payment.application.csv_parser import CSVBankStatementParser
from app.modules.payment.application.use_cases import ImportBankStatementCSVUseCase
from app.modules.payment.domain.entities import (
    ImportBatchStatus,
    TransactionFingerprint,
)
from app.modules.payment.infrastructure.models import ImportBatchModel
from app.modules.payment.infrastructure.repositories import ImportBatchRepository
from app.shared.exceptions import ConflictError, ValidationError


# ---------------------------------------------------------------------------
# Test 1: Signed Amount (+10000, -5000)
# ---------------------------------------------------------------------------
def test_special_01_signed_amount_explicit_signs(client: TestClient, registered_owner: dict):
    """Verify Layout C correctly classifies explicit '+10000.00' as CREDIT and '-5000.00' as DEBIT."""
    headers = registered_owner["headers"]

    csv_content = (
        "Date,Narration,Amount\n"
        "2026-03-01,Customer Wire Inflow,+10000.00\n"
        "2026-03-02,Vendor Payout Outflow,-5000.00\n"
        "2026-03-03,Unsigned Inflow,2500.00\n"
    ).encode("utf-8")

    files = {"file": ("signed_statement.csv", io.BytesIO(csv_content), "text/csv")}
    resp = client.post("/api/v1/payments/upload-statement", headers=headers, files=files)
    assert resp.status_code == 201
    data = resp.json()["data"]

    assert data["total_rows"] == 3
    assert data["imported_payments_count"] == 2  # +10000.00 and 2500.00
    assert data["skipped_debits_count"] == 1     # -5000.00 skipped from receivable payments
    assert data["failed_rows_count"] == 0

    # Query raw transactions to verify -5000.00 was recorded as DEBIT
    txns_resp = client.get("/api/v1/payments/transactions?txn_type=DEBIT", headers=headers)
    assert txns_resp.status_code == 200
    items = txns_resp.json()["data"]["items"]
    assert len(items) == 1
    assert Decimal(str(items[0]["amount"])) == Decimal("5000.00")
    assert items[0]["transaction_type"] == "DEBIT"


# ---------------------------------------------------------------------------
# Test 2: Formula-like Narration vs Amount Field
# ---------------------------------------------------------------------------
def test_special_02_formula_injection_narration_sanitized_and_amount_rejected(
    client: TestClient, registered_owner: dict
):
    """Verify formula injection in text fields is neutralized with `'` while formula in amount is rejected."""
    headers = registered_owner["headers"]

    # 1. Narration with formula characters is neutralized
    csv_valid_formulas = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,=SUM(A1:B1) transfer,1000.00,\n"
        "2026-03-02,+CMD|'/C calc'!A0,2000.00,\n"
        "2026-03-03,@mention payload,3000.00,\n"
        "2026-03-04,-flagged note,4000.00,\n"
    ).encode("utf-8")

    files = {"file": ("formula_test.csv", io.BytesIO(csv_valid_formulas), "text/csv")}
    resp = client.post("/api/v1/payments/upload-statement", headers=headers, files=files)
    assert resp.status_code == 201

    # Verify narrations in database start with single quote "'"
    payments_resp = client.get("/api/v1/payments?page_size=10", headers=headers)
    assert payments_resp.status_code == 200
    items = payments_resp.json()["data"]["items"]
    narrations = [item["narration"] for item in items]
    assert any(n.startswith("'=") for n in narrations)
    assert any(n.startswith("'+") for n in narrations)
    assert any(n.startswith("'@") for n in narrations)
    assert any(n.startswith("'-") for n in narrations)

    # 2. Formula in numeric amount field must be rejected as invalid numeric
    parser = CSVBankStatementParser()
    bad_amount_csv = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,Attempted formula in amount,@SUM(1+1),\n"
        "2026-03-02,Attempted cmd in amount,=1000,\n"
    ).encode("utf-8")
    txns, errors = parser.parse(bad_amount_csv)
    assert len(txns) == 0
    assert len(errors) == 2
    assert all("Invalid numeric" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Test 3: Exact Duplicate File Upload -> HTTP 409
# ---------------------------------------------------------------------------
def test_special_03_exact_duplicate_file_endpoint_conflict_409(
    client: TestClient, registered_owner: dict
):
    """Verify re-uploading the exact same CSV content returns HTTP 409 Conflict."""
    headers = registered_owner["headers"]

    csv_content = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,Customer Payment Alpha,5000.00,\n"
    ).encode("utf-8")

    files1 = {"file": ("statement_dup.csv", io.BytesIO(csv_content), "text/csv")}
    resp1 = client.post("/api/v1/payments/upload-statement", headers=headers, files=files1)
    assert resp1.status_code == 201

    files2 = {"file": ("statement_dup.csv", io.BytesIO(csv_content), "text/csv")}
    resp2 = client.post("/api/v1/payments/upload-statement", headers=headers, files=files2)
    assert resp2.status_code == 409
    body = resp2.json()
    assert body["success"] is False
    assert body["error"]["code"] == "CONFLICT"
    assert "already been imported" in body["error"]["message"]


# ---------------------------------------------------------------------------
# Test 4: Failed Import Batch Retry Succeeded
# ---------------------------------------------------------------------------
def test_special_04_failed_import_retry_succeeds(db_session: Session):
    """Verify that if a statement import fails schema validation, the failed batch does not block re-upload."""
    company_id = uuid4()
    use_case = ImportBankStatementCSVUseCase(db=db_session)

    # Broken CSV missing required columns -> schema validation error
    broken_csv = "Date,User,Status\n2026-03-01,Alice,Active\n".encode("utf-8")

    with pytest.raises(ValidationError):
        use_case.execute(
            company_id=company_id,
            file_content=broken_csv,
            file_name="broken_file.csv",
        )

    # Verify batch was saved as FAILED
    batch_repo = ImportBatchRepository(db_session)
    batches, total = batch_repo.list_by_company(company_id=company_id)
    assert total == 1
    assert batches[0].status == ImportBatchStatus.FAILED

    # Valid CSV content for retry
    valid_csv = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,Retry payment,1200.00,\n"
    ).encode("utf-8")

    # Re-uploading valid file succeeds cleanly
    result = use_case.execute(
        company_id=company_id,
        file_content=valid_csv,
        file_name="broken_file.csv",
    )
    assert result.imported_payments_count == 1


# ---------------------------------------------------------------------------
# Test 5: Concurrent Duplicate Batch Creation Returns Conflict Not 500
# ---------------------------------------------------------------------------
def test_special_05_concurrent_duplicate_import_raises_conflict_not_500(db_session: Session):
    """Verify that concurrent duplicate batch creation triggers ConflictError instead of unhandled 500."""
    company_id = uuid4()
    use_case = ImportBankStatementCSVUseCase(db=db_session)

    csv_data = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,Concurrent payment,1500.00,\n"
    ).encode("utf-8")

    # Pre-seed existing batch with identical file hash
    from hashlib import sha256
    file_hash = sha256(csv_data).hexdigest()

    existing_batch = ImportBatchModel(
        id=uuid4(),
        company_id=company_id,
        file_name="concurrent.csv",
        file_hash=file_hash,
        status="PROCESSING",
    )
    db_session.add(existing_batch)
    db_session.commit()

    # Second concurrent worker attempts import with same hash -> ConflictError (HTTP 409)
    with pytest.raises(ConflictError, match="already been imported"):
        use_case.execute(
            company_id=company_id,
            file_content=csv_data,
            file_name="concurrent.csv",
        )


# ---------------------------------------------------------------------------
# Test 6: Fallback Collision Disambiguation with Running Balance
# ---------------------------------------------------------------------------
def test_special_06_fallback_collision_disambiguation_with_running_balance():
    """Verify distinct cash transactions on same date with identical amount are disambiguated by balance."""
    company_id = uuid4()
    txn_date = date(2026, 3, 1)
    amount = Decimal("500.00")
    currency = "INR"
    txn_type = "CREDIT"
    narration = "CASH DEPOSIT"

    # Transaction 1 with closing balance 10,000.00
    hash_1 = TransactionFingerprint.compute_fallback(
        company_id=company_id,
        account_identifier="ACC123",
        txn_date=txn_date,
        amount=amount,
        currency=currency,
        txn_type=txn_type,
        narration=narration,
        balance=Decimal("10000.00"),
    )

    # Transaction 2 with closing balance 10,500.00
    hash_2 = TransactionFingerprint.compute_fallback(
        company_id=company_id,
        account_identifier="ACC123",
        txn_date=txn_date,
        amount=amount,
        currency=currency,
        txn_type=txn_type,
        narration=narration,
        balance=Decimal("10500.00"),
    )

    assert hash_1 != hash_2, "Fallback hashes with different running balances must not collide"


# ---------------------------------------------------------------------------
# Test 7: Cross-Tenant Raw Bank Transaction Listing Isolation
# ---------------------------------------------------------------------------
def test_special_07_cross_tenant_bank_transactions_isolation(
    client: TestClient, registered_owner: dict, second_company_owner: dict
):
    """Verify Company B cannot view Company A's raw statement transactions."""
    headers_a = registered_owner["headers"]
    headers_b = second_company_owner["headers"]

    csv_content = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,Company A Secret Transaction,50000.00,\n"
        "2026-03-02,Company A Secret Debit,,10000.00\n"
    ).encode("utf-8")

    files = {"file": ("company_a_txns.csv", io.BytesIO(csv_content), "text/csv")}
    upload_resp = client.post("/api/v1/payments/upload-statement", headers=headers_a, files=files)
    assert upload_resp.status_code == 201

    # Company A sees both transactions
    resp_a = client.get("/api/v1/payments/transactions", headers=headers_a)
    assert resp_a.status_code == 200
    assert resp_a.json()["data"]["total"] == 2

    # Company B queries transactions -> sees 0
    resp_b = client.get("/api/v1/payments/transactions", headers=headers_b)
    assert resp_b.status_code == 200
    assert resp_b.json()["data"]["total"] == 0
    assert len(resp_b.json()["data"]["items"]) == 0


# ---------------------------------------------------------------------------
# Test 8: Allocation Mutation Endpoints Unavailable
# ---------------------------------------------------------------------------
def test_special_08_allocation_mutation_endpoint_unavailable(
    client: TestClient, registered_owner: dict
):
    """Assert payment allocation mutation endpoints do not exist in Phase 12 (fail with 404/405)."""
    headers = registered_owner["headers"]
    dummy_id = uuid4()

    # Attempt allocation mutation
    resp_alloc = client.post(
        f"/api/v1/payments/{dummy_id}/allocate",
        headers=headers,
        json={"invoice_id": str(uuid4()), "amount": 100.00},
    )
    assert resp_alloc.status_code in (404, 405), "Allocation endpoint must not be exposed"

    # Attempt PUT update on payment
    resp_put = client.put(
        f"/api/v1/payments/{dummy_id}",
        headers=headers,
        json={"allocated_amount": 100.00},
    )
    assert resp_put.status_code in (404, 405), "Direct payment mutation must not be exposed"


# ---------------------------------------------------------------------------
# Test 9: Zero Invoice Mutation During Payment Ingestion
# ---------------------------------------------------------------------------
def test_special_09_zero_invoice_mutation_during_statement_import(
    client: TestClient, registered_owner: dict, db_session: Session
):
    """Verify existing invoices in the database remain completely untouched during payment ingestion."""
    headers = registered_owner["headers"]
    company_id = UUID(registered_owner["company"]["id"])

    # 1. Directly seed an outstanding invoice in database
    invoice_id = uuid4()
    invoice = InvoiceModel(
        id=invoice_id,
        company_id=company_id,
        customer_id=None,
        invoice_number="INV-2026-TEST",
        issue_date=date(2026, 3, 1),
        due_date=date(2026, 3, 31),
        currency="INR",
        tax_amount=Decimal("1800.00"),
        total_amount=Decimal("11800.00"),
        paid_amount=Decimal("0.00"),
        outstanding_amount=Decimal("11800.00"),
        status="SENT",
        notes="Pre-existing invoice",
    )
    db_session.add(invoice)
    db_session.commit()

    # 2. Ingest statement with exact payment amount referencing the invoice
    csv_content = (
        "Date,Narration,Ref No,Credit,Debit\n"
        "2026-03-05,Payment for INV-2026-TEST,UTR998877,11800.00,\n"
    ).encode("utf-8")

    files = {"file": ("statement_invoice_test.csv", io.BytesIO(csv_content), "text/csv")}
    resp = client.post("/api/v1/payments/upload-statement", headers=headers, files=files)
    assert resp.status_code == 201

    # 3. Query the invoice again from DB and assert 100% immutability
    db_session.expire_all()
    fresh_invoice = db_session.query(InvoiceModel).filter(InvoiceModel.id == invoice_id).first()
    assert fresh_invoice is not None
    assert fresh_invoice.status == "SENT", "Invoice status must remain unchanged in Phase 12"
    assert fresh_invoice.paid_amount == Decimal("0.00"), "Invoice paid_amount must not be mutated"
    assert fresh_invoice.outstanding_amount == Decimal("11800.00"), "Invoice outstanding_amount must remain intact"


# ---------------------------------------------------------------------------
# Test 10: Alembic Migration 0005 Lifecycle (Upgrade -> Downgrade -> Upgrade)
# ---------------------------------------------------------------------------
def test_special_10_migration_0005_lifecycle():
    """Verify migration 0005 executes cleanly, downgrades to 0004, and re-upgrades to head."""
    project_root = Path(__file__).resolve().parent.parent.parent
    alembic_ini = project_root / "alembic.ini"
    assert alembic_ini.exists(), "alembic.ini must exist"

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        tmp_db_path = tmp_db.name

    try:
        sqlite_url = f"sqlite:///{Path(tmp_db_path).as_posix()}"
        alembic_cfg = Config(str(alembic_ini))
        alembic_cfg.set_main_option("sqlalchemy.url", sqlite_url)
        alembic_cfg.set_main_option("script_location", str(project_root / "alembic"))

        # Step 1: Upgrade to head (includes 0005)
        command.upgrade(alembic_cfg, "head")

        engine = create_engine(sqlite_url)
        with engine.connect() as conn:
            from sqlalchemy import inspect
            inspector = inspect(conn)
            tables = inspector.get_table_names()
            assert "import_batches" in tables
            assert "bank_transactions" in tables
            assert "payments" in tables

        # Step 2: Downgrade to 0004_phase_11_invoice_archive
        command.downgrade(alembic_cfg, "0004_phase_11_archive")
        with engine.connect() as conn:
            from sqlalchemy import inspect
            inspector = inspect(conn)
            tables_down = inspector.get_table_names()
            assert "import_batches" not in tables_down
            assert "bank_transactions" not in tables_down
            assert "payments" not in tables_down
            assert "invoices" in tables_down

        # Step 3: Re-upgrade to head
        command.upgrade(alembic_cfg, "head")
        with engine.connect() as conn:
            from sqlalchemy import inspect
            inspector = inspect(conn)
            tables_reup = inspector.get_table_names()
            assert "import_batches" in tables_reup
            assert "bank_transactions" in tables_reup
            assert "payments" in tables_reup

    finally:
        try:
            Path(tmp_db_path).unlink(missing_ok=True)
        except Exception:
            pass
