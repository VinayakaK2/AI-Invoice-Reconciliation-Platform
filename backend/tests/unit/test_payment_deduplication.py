"""Unit tests for Bank Statement & Payment Deduplication Gates."""

from datetime import date
from decimal import Decimal
import pytest
from uuid import UUID, uuid4

from app.modules.payment.application.use_cases import ImportBankStatementCSVUseCase
from app.modules.payment.domain.entities import (
    BankTransaction,
    ImportBatch,
    Payment,
    TransactionFingerprint,
    TransactionType,
)
from app.modules.payment.infrastructure.repositories import (
    BankTransactionRepository,
    ImportBatchRepository,
    PaymentRepository,
)
from app.shared.domain.money import Money
from app.shared.exceptions import ConflictError


def test_gate_1_file_content_hash_duplicate_detection(db_session):
    """Assert uploading identical statement file content raises ConflictError."""
    company_id = uuid4()
    use_case = ImportBankStatementCSVUseCase(db=db_session)

    csv_data = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,Payment from Customer A,5000.00,\n"
    ).encode("utf-8")

    # First upload -> Success
    res1 = use_case.execute(
        company_id=company_id,
        file_content=csv_data,
        file_name="statement_march.csv",
    )
    assert res1.imported_payments_count == 1

    # Second upload of same content -> Gate 1 triggers ConflictError
    with pytest.raises(ConflictError, match="has already been imported"):
        use_case.execute(
            company_id=company_id,
            file_content=csv_data,
            file_name="statement_march_copy.csv",
        )


def test_gate_2_intra_batch_duplicate_detection(db_session):
    """Assert repeated rows with same reference within a single CSV file are caught."""
    company_id = uuid4()
    use_case = ImportBankStatementCSVUseCase(db=db_session)

    csv_data = (
        "Date,Narration,Ref No,Credit,Debit\n"
        "2026-03-01,Customer Transfer,REF-999,2500.00,\n"
        "2026-03-01,Customer Transfer Duplicate,REF-999,2500.00,\n"
        "2026-03-02,Different Customer,REF-888,1000.00,\n"
    ).encode("utf-8")

    res = use_case.execute(
        company_id=company_id,
        file_content=csv_data,
        file_name="intra_batch_test.csv",
    )

    # First row imported, second row skipped as duplicate, third row imported
    assert res.imported_payments_count == 2
    assert res.skipped_duplicates_count == 1
    assert any("Duplicate transaction within statement batch" in err.message for err in res.errors)


def test_gate_3_database_duplicate_skip_across_files(db_session):
    """Assert overlapping statement transactions across different files are skipped idempotently."""
    company_id = uuid4()
    use_case = ImportBankStatementCSVUseCase(db=db_session)

    # File 1 contains txn 1 and 2
    csv_file_1 = (
        "Date,Narration,Ref No,Credit,Debit\n"
        "2026-03-01,First Transaction,TXN-001,1000.00,\n"
        "2026-03-02,Second Transaction,TXN-002,2000.00,\n"
    ).encode("utf-8")

    res1 = use_case.execute(
        company_id=company_id,
        file_content=csv_file_1,
        file_name="week1.csv",
    )
    assert res1.imported_payments_count == 2

    # File 2 overlaps on TXN-002, adds TXN-003
    csv_file_2 = (
        "Date,Narration,Ref No,Credit,Debit\n"
        "2026-03-02,Second Transaction Overlap,TXN-002,2000.00,\n"
        "2026-03-03,Third Transaction,TXN-003,3000.00,\n"
    ).encode("utf-8")

    res2 = use_case.execute(
        company_id=company_id,
        file_content=csv_file_2,
        file_name="week2.csv",
        skip_duplicates=True,
    )

    assert res2.imported_payments_count == 1  # TXN-003 imported
    assert res2.skipped_duplicates_count == 1  # TXN-002 skipped


def test_cross_company_deduplication_isolation(db_session):
    """Assert Company A and Company B can ingest identical transaction references without collision."""
    company_a = uuid4()
    company_b = uuid4()
    use_case = ImportBankStatementCSVUseCase(db=db_session)

    csv_data = (
        "Date,Narration,Ref No,Credit,Debit\n"
        "2026-03-01,Shared Reference Number,SHARED-REF-100,5000.00,\n"
    ).encode("utf-8")

    res_a = use_case.execute(
        company_id=company_a,
        file_content=csv_data,
        file_name="company_a_statement.csv",
    )
    assert res_a.imported_payments_count == 1

    # Company B uploads identical reference -> Should succeed completely
    res_b = use_case.execute(
        company_id=company_b,
        file_content=csv_data,
        file_name="company_b_statement.csv",
    )
    assert res_b.imported_payments_count == 1


def test_savepoint_rollback_on_single_row_failure(db_session):
    """Assert savepoint isolates failing row so valid rows still commit."""
    company_id = uuid4()
    use_case = ImportBankStatementCSVUseCase(db=db_session)

    # Row 2 contains zero amount which violates domain invariant
    csv_data = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,Valid Payment 1,1500.00,\n"
        "2026-03-02,Zero Payment,0.00,\n"
        "2026-03-03,Valid Payment 2,2500.00,\n"
    ).encode("utf-8")

    res = use_case.execute(
        company_id=company_id,
        file_content=csv_data,
        file_name="savepoint_test.csv",
    )

    assert res.imported_payments_count == 2
    assert res.failed_rows_count >= 1
    assert len(res.imported_payment_ids) == 2
