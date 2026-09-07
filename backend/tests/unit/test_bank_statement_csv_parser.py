"""Unit tests for Bank Statement CSV Parsing Engine."""

from datetime import date
from decimal import Decimal
import pytest

from app.modules.payment.application.csv_parser import CSVBankStatementParser
from app.modules.payment.domain.entities import TransactionType


@pytest.fixture
def parser():
    return CSVBankStatementParser()


def test_parse_layout_a_separate_credit_debit_columns(parser):
    """Test standard bank format with distinct Credit and Debit columns."""
    csv_data = (
        "Txn Date,Narration,Chq/Ref No,Deposit,Withdrawal,Balance\n"
        "2026-03-01,NEFT Received from Acme Corp,AXIS123456,50000.00,,50000.00\n"
        "2026-03-02,Bank Account Service Fee,CHG9988,,150.00,49850.00\n"
        "2026-03-03,IMPS from Beta Retail,IMPS5566,12500.50,,62350.50\n"
    ).encode("utf-8")

    txns, errors = parser.parse(csv_data)
    assert len(errors) == 0
    assert len(txns) == 3

    assert txns[0].transaction_date == date(2026, 3, 1)
    assert txns[0].amount == Decimal("50000.00")
    assert txns[0].transaction_type == TransactionType.CREDIT
    assert txns[0].reference_number == "AXIS123456"

    assert txns[1].transaction_date == date(2026, 3, 2)
    assert txns[1].amount == Decimal("150.00")
    assert txns[1].transaction_type == TransactionType.DEBIT

    assert txns[2].transaction_date == date(2026, 3, 3)
    assert txns[2].amount == Decimal("12500.50")
    assert txns[2].transaction_type == TransactionType.CREDIT


def test_parse_layout_b_amount_with_cr_dr_indicator(parser):
    """Test format with single amount column and CR/DR type indicator."""
    csv_data = (
        "Date,Description,Ref No,Amount,Type\n"
        "15-03-2026,UPI Payment from Customer,UPI998877,2500.00,CR\n"
        "16-03-2026,Office Supplies Vendor,VENDOR11,800.00,DR\n"
    ).encode("utf-8")

    txns, errors = parser.parse(csv_data)
    assert len(errors) == 0
    assert len(txns) == 2

    assert txns[0].amount == Decimal("2500.00")
    assert txns[0].transaction_type == TransactionType.CREDIT

    assert txns[1].amount == Decimal("800.00")
    assert txns[1].transaction_type == TransactionType.DEBIT


def test_parse_layout_c_signed_amount(parser):
    """Test format with signed amounts (positive=Credit, negative=Debit)."""
    csv_data = (
        "Date,Particulars,Reference,Amount\n"
        "2026/03/10,Invoice settlement INV-101,REF101,3400.00\n"
        "2026/03/11,Software subscription,REF102,-450.00\n"
    ).encode("utf-8")

    txns, errors = parser.parse(csv_data)
    assert len(errors) == 0
    assert len(txns) == 2

    assert txns[0].amount == Decimal("3400.00")
    assert txns[0].transaction_type == TransactionType.CREDIT

    assert txns[1].amount == Decimal("450.00")
    assert txns[1].transaction_type == TransactionType.DEBIT


def test_parse_with_preface_rows(parser):
    """Test skipping bank export preface header lines before the actual table."""
    csv_data = (
        "Account Statement for Account 1234567890\n"
        "Customer Name: Zenith Enterprises\n"
        "Statement Period: 01-Mar-2026 to 31-Mar-2026\n"
        "Date,Description,Credit,Debit\n"
        "2026-03-05,Direct deposit from client,18000.00,\n"
    ).encode("utf-8")

    txns, errors = parser.parse(csv_data)
    assert len(errors) == 0
    assert len(txns) == 1
    assert txns[0].amount == Decimal("18000.00")
    assert txns[0].transaction_type == TransactionType.CREDIT


def test_parse_indian_and_western_comma_amounts(parser):
    """Test parsing amounts formatted with commas and currency symbols."""
    csv_data = (
        "Date,Narration,Credit,Debit\n"
        '2026-03-01,Transfer 1,"1,25,000.00",\n'
        '2026-03-02,Transfer 2,"₹ 50,000.50",\n'
        '2026-03-03,Transfer 3,"$10,500.25",\n'
    ).encode("utf-8")

    txns, errors = parser.parse(csv_data)
    assert len(errors) == 0
    assert len(txns) == 3
    assert txns[0].amount == Decimal("125000.00")
    assert txns[1].amount == Decimal("50000.50")
    assert txns[2].amount == Decimal("10500.25")


def test_parse_multi_date_formats(parser):
    """Test recognition of diverse date formats across Indian and global banks."""
    csv_data = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,Txn 1,100.00,\n"
        "02/03/2026,Txn 2,200.00,\n"
        "03-03-2026,Txn 3,300.00,\n"
        "04.03.2026,Txn 4,400.00,\n"
        "05-Mar-2026,Txn 5,500.00,\n"
    ).encode("utf-8")

    txns, errors = parser.parse(csv_data)
    assert len(errors) == 0
    assert len(txns) == 5
    assert txns[0].transaction_date == date(2026, 3, 1)
    assert txns[1].transaction_date == date(2026, 3, 2)
    assert txns[2].transaction_date == date(2026, 3, 3)
    assert txns[3].transaction_date == date(2026, 3, 4)
    assert txns[4].transaction_date == date(2026, 3, 5)


def test_utr_extraction_from_narration(parser):
    """Test extracting UTR reference number from narration if reference column is empty."""
    csv_data = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-10,NEFT/HDFC1234567890/From Global Inc,15000.00,\n"
        "2026-03-11,UPI-987654321012-Payee,2000.00,\n"
        "2026-03-12,IMPS:445566778899-Transfer,3500.00,\n"
    ).encode("utf-8")

    txns, errors = parser.parse(csv_data)
    assert len(errors) == 0
    assert len(txns) == 3
    assert txns[0].reference_number == "HDFC1234567890"
    assert txns[1].reference_number == "987654321012"
    assert txns[2].reference_number == "445566778899"


def test_formula_injection_defense_cwe_1236(parser):
    """Test neutralizing spreadsheet formula injection in narrations and references."""
    csv_data = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,=cmd|'/C calc'!A0,1000.00,\n"
        "2026-03-02,@SUM(1+1),2000.00,\n"
        "2026-03-03,+cmd|'/C calc'!A0,3000.00,\n"
    ).encode("utf-8")

    txns, errors = parser.parse(csv_data)
    assert len(errors) == 0
    assert len(txns) == 3
    assert txns[0].narration.startswith("'=")
    assert txns[1].narration.startswith("'@")
    assert txns[2].narration.startswith("'+")


def test_granular_error_reporting_partial_success(parser):
    """Test that bad rows emit CSVRowError without failing valid rows."""
    csv_data = (
        "Date,Narration,Credit,Debit\n"
        "2026-03-01,Valid Row 1,1000.00,\n"
        "invalid-date,Bad Date Row,2000.00,\n"
        "2026-03-03,,3000.00,\n"  # Missing narration
        "2026-03-04,Invalid Amount,not_a_number,\n"
        "2026-03-05,Valid Row 2,5000.00,\n"
    ).encode("utf-8")

    txns, errors = parser.parse(csv_data)
    assert len(txns) == 2
    assert txns[0].narration == "Valid Row 1"
    assert txns[1].narration == "Valid Row 2"

    assert len(errors) == 3
    assert any("Invalid date" in err.message for err in errors)
    assert any("narration or particulars is empty" in err.message for err in errors)
    assert any("Invalid numeric" in err.message for err in errors)


def test_empty_file_and_null_byte(parser):
    """Test rejection of empty files and illegal null bytes."""
    _, errors_empty = parser.parse(b"")
    assert len(errors_empty) == 1
    assert "empty" in errors_empty[0].message

    _, errors_null = parser.parse(b"Date,Narration,Credit\x00,Debit\n")
    assert len(errors_null) == 1
    assert "null byte" in errors_null[0].message


def test_missing_mandatory_columns(parser):
    """Test schema rejection when essential columns are missing."""
    # Date and Narration present, but missing Credit/Debit/Amount
    csv_data = "Date,Narration,User\n2026-03-01,Transfer,John\n".encode("utf-8")
    txns, errors = parser.parse(csv_data)
    assert len(txns) == 0
    assert len(errors) == 1
    assert errors[0].field == "schema"
    assert "Missing required columns" in errors[0].message

    # Unrecognized headers completely
    unrecognized = "Date,User\n2026-03-01,John\n".encode("utf-8")
    _, errors2 = parser.parse(unrecognized)
    assert len(errors2) == 1
    assert "Could not detect valid bank statement headers" in errors2[0].message
