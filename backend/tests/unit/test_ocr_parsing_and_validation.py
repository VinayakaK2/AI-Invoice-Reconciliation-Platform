"""Unit tests for Phase 12 OCR Parsing, Normalization, and Deterministic Financial Validation.

Verifies:
- Exact Decimal parsing with Indian and Western numbering schemes
- Comma structure validation and malformed formatting rejection
- Formula injection defenses (CWE-1236)
- Textual and numeric date normalization with ambiguity detection
- Temporal consistency invariant (due_date >= issue_date)
- Multi-signal identifier isolation (GSTIN, PAN, VAT, PO, UTR, Invoice Number)
- Mathematical balance validation (subtotal + tax - discount + round_off ≈ total)
- Weighted confidence scoring and ambiguity triggers
"""

from datetime import date
from decimal import Decimal
import pytest
from app.modules.invoice.domain.extraction_rules import (
    evaluate_extraction_confidence_and_ambiguity,
    extract_identifiers,
    parse_date_string,
    parse_monetary_amount,
    validate_invoice_mathematics,
    validate_temporal_consistency,
)


# -----------------------------------------------------------------------------
# 1. Monetary Parsing & Normalization
# -----------------------------------------------------------------------------

def test_parse_monetary_amount_indian_numbering() -> None:
    """Correctly parses Indian numbering with lakhs and crores (1,50,000.00)."""
    res1 = parse_monetary_amount("₹ 1,50,000.00")
    assert res1.is_valid is True
    assert res1.amount == Decimal("150000.00")
    assert res1.currency == "INR"

    res2 = parse_monetary_amount("12,34,567.89", default_currency="INR")
    assert res2.is_valid is True
    assert res2.amount == Decimal("1234567.89")

    res3 = parse_monetary_amount("Rs. 50,000/-")
    assert res3.is_valid is True
    assert res3.amount == Decimal("50000.00")


def test_parse_monetary_amount_western_numbering() -> None:
    """Correctly parses Western numbering ($ 150,000.00 and 1,234,567.89)."""
    res1 = parse_monetary_amount("$ 150,000.00")
    assert res1.is_valid is True
    assert res1.amount == Decimal("150000.00")
    assert res1.currency == "USD"

    res2 = parse_monetary_amount("€ 1,234,567.89")
    assert res2.is_valid is True
    assert res2.amount == Decimal("1234567.89")
    assert res2.currency == "EUR"


def test_parse_monetary_amount_plain_numeric() -> None:
    """Parses unformatted numeric strings without commas."""
    res = parse_monetary_amount("45000.5", default_currency="INR")
    assert res.is_valid is True
    assert res.amount == Decimal("45000.50")


def test_parse_monetary_amount_malformed_commas_rejected() -> None:
    """Rejects malformed comma groupings (e.g. 1,5000.00 or 1,2,3)."""
    res = parse_monetary_amount("1,5000.00")
    assert res.is_valid is False
    assert "malformed comma grouping" in res.error.lower()


def test_parse_monetary_amount_formula_injection_defended() -> None:
    """Neutralizes formula injection characters (=, @, tab, newline)."""
    res = parse_monetary_amount("=SUM(A1:A10)")
    assert res.is_valid is False
    assert "formula injection" in res.error.lower()


def test_parse_monetary_amount_labeled_prefix_handled() -> None:
    """Gracefully strips label prefix before colon."""
    res = parse_monetary_amount("Total Amount: INR 75,000.00")
    assert res.is_valid is True
    assert res.amount == Decimal("75000.00")
    assert res.currency == "INR"


# -----------------------------------------------------------------------------
# 2. Date Normalization & Temporal Consistency
# -----------------------------------------------------------------------------

def test_parse_date_iso_format() -> None:
    """Parses canonical ISO format (YYYY-MM-DD)."""
    res = parse_date_string("2026-09-15")
    assert res.parsed_date == date(2026, 9, 15)
    assert res.is_ambiguous is False


def test_parse_date_textual_format() -> None:
    """Parses English textual date representations."""
    res1 = parse_date_string("15 Jan 2026")
    assert res1.parsed_date == date(2026, 1, 15)
    assert res1.is_ambiguous is False

    res2 = parse_date_string("March 22, 2026")
    assert res2.parsed_date == date(2026, 3, 22)
    assert res2.is_ambiguous is False


def test_parse_date_unambiguous_day_greater_than_twelve() -> None:
    """Parses 25/08/2026 unambiguously as day-first."""
    res = parse_date_string("25/08/2026")
    assert res.parsed_date == date(2026, 8, 25)
    assert res.is_ambiguous is False


def test_parse_date_ambiguous_both_lte_twelve_flagged() -> None:
    """Flags ambiguous date 04/05/2026 and defaults to DD-MM-YYYY."""
    res = parse_date_string("04/05/2026", prefer_day_first=True)
    assert res.parsed_date == date(2026, 5, 4)
    assert res.is_ambiguous is True
    assert "ambiguous" in res.warning.lower()


def test_validate_temporal_consistency() -> None:
    """Asserts due_date >= issue_date invariant."""
    valid, warnings = validate_temporal_consistency(date(2026, 9, 1), date(2026, 9, 15))
    assert valid is True
    assert len(warnings) == 0

    # Due date earlier than issue date
    invalid, inv_warnings = validate_temporal_consistency(date(2026, 9, 15), date(2026, 9, 1))
    assert invalid is False
    assert any("temporal invariant violated" in w.lower() for w in inv_warnings)


# -----------------------------------------------------------------------------
# 3. Identifier Extraction & Disambiguation
# -----------------------------------------------------------------------------

def test_extract_identifiers_gstin_and_pan() -> None:
    """Identifies Indian GSTIN (15 chars) and extracts constituent PAN."""
    text = (
        "TAX INVOICE\n"
        "Invoice Number: INV-2026-8801\n"
        "Vendor GSTIN: 29ABCDE1234F1Z5\n"
        "PO #: PO-99221\n"
        "Total: 50000.00\n"
    )
    ids = extract_identifiers(text)
    assert ids["invoice_number"] == "INV-2026-8801"
    assert ids["tax_id"] == "29ABCDE1234F1Z5"
    assert ids["pan"] == "ABCDE1234F"
    assert ids["po_number"] == "PO-99221"


def test_extract_identifiers_invoice_number_not_colliding_with_tax_or_po() -> None:
    """Ensures invoice number does not falsely capture Tax ID or PO."""
    text = (
        "BILL OF SUPPLY\n"
        "Purchase Order: PO-554433\n"
        "Invoice No: INV-ALPHA-99\n"
        "UTR: CMS123456789012\n"
    )
    ids = extract_identifiers(text)
    assert ids["invoice_number"] == "INV-ALPHA-99"
    assert ids["po_number"] == "PO-554433"
    assert ids["payment_ref"] == "CMS123456789012"


def test_extract_identifiers_disallowed_stopwords_rejected() -> None:
    """Disallows generic stopwords (e.g. INVOICE, AMOUNT, TOTAL) as invoice numbers."""
    text = "INVOICE NUMBER: TOTAL\nTotal: 1000.00"
    ids = extract_identifiers(text)
    assert ids["invoice_number"] is None


# -----------------------------------------------------------------------------
# 4. Deterministic Mathematical Validation
# -----------------------------------------------------------------------------

def test_validate_invoice_mathematics_exact_match() -> None:
    """Exact subtotal + tax = total passes validation without discrepancy."""
    res = validate_invoice_mathematics(
        total_amount=Decimal("118.00"),
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("18.00"),
    )
    assert res.is_valid is True
    assert res.discrepancy == Decimal("0.00")


def test_validate_invoice_mathematics_with_discount_and_roundoff() -> None:
    """Subtotal + tax - discount + round_off = total passes."""
    res = validate_invoice_mathematics(
        total_amount=Decimal("117.00"),
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("18.00"),
        discount_amount=Decimal("1.00"),
        round_off=Decimal("0.00"),
    )
    assert res.is_valid is True
    assert res.discrepancy == Decimal("0.00")


def test_validate_invoice_mathematics_minor_variance_accepted() -> None:
    """Minor discrepancy within default tolerance (0.05) is accepted with note."""
    res = validate_invoice_mathematics(
        total_amount=Decimal("100.02"),
        subtotal=Decimal("80.00"),
        tax_amount=Decimal("20.00"),
    )
    assert res.is_valid is True
    assert res.discrepancy == Decimal("0.02")
    assert any("minor rounding variance" in n.lower() for n in res.notes)


def test_validate_invoice_mathematics_large_discrepancy_rejected() -> None:
    """Discrepancy exceeding tolerance fails validation; Rule 20 prevents silent adjustment."""
    res = validate_invoice_mathematics(
        total_amount=Decimal("100.00"),
        subtotal=Decimal("70.00"),
        tax_amount=Decimal("15.00"),
    )
    assert res.is_valid is False
    assert res.discrepancy == Decimal("15.00")
    assert any("mismatch" in n.lower() for n in res.notes)


# -----------------------------------------------------------------------------
# 5. Extraction Confidence Scoring & Ambiguity
# -----------------------------------------------------------------------------

def test_evaluate_confidence_clean_extraction() -> None:
    """Complete, mathematically sound extraction achieves high confidence and non-ambiguous status."""
    math_res = validate_invoice_mathematics(Decimal("118.00"), Decimal("100.00"), Decimal("18.00"))
    confidence, is_ambiguous, notes = evaluate_extraction_confidence_and_ambiguity(
        invoice_number="INV-2026-01",
        total_amount=Decimal("118.00"),
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 30),
        customer_name="Acme Corp",
        tax_id="29ABCDE1234F1Z5",
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("18.00"),
        math_result=math_res,
        date_ambiguous=False,
        temporal_valid=True,
    )
    assert confidence >= Decimal("0.85")
    assert is_ambiguous is False


def test_evaluate_confidence_missing_invoice_number_triggers_ambiguity() -> None:
    """Missing invoice number flags extraction as ambiguous for accountant review."""
    math_res = validate_invoice_mathematics(Decimal("100.00"), Decimal("100.00"), None)
    confidence, is_ambiguous, notes = evaluate_extraction_confidence_and_ambiguity(
        invoice_number=None,
        total_amount=Decimal("100.00"),
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        customer_name="Acme Corp",
        tax_id=None,
        subtotal=Decimal("100.00"),
        tax_amount=None,
        math_result=math_res,
        date_ambiguous=False,
        temporal_valid=True,
    )
    assert is_ambiguous is True
    assert any("missing invoice number" in n.lower() for n in notes)


def test_parse_monetary_amount_edge_cases() -> None:
    """Covers empty, alphabetical, and negative monetary inputs."""
    # Empty
    empty_res = parse_monetary_amount("")
    assert empty_res.is_valid is False
    assert "empty" in empty_res.error.lower()

    # Alphabetical in number
    alpha_res = parse_monetary_amount("12A34.50")
    assert alpha_res.is_valid is False
    assert "alphabetical" in alpha_res.error.lower()


def test_parse_date_string_invalid_calendar_dates() -> None:
    """Covers invalid calendar dates such as 31 February and malformed numeric formats."""
    # Textual month with impossible day
    inv_text = parse_date_string("31 Feb 2026")
    assert inv_text.parsed_date is None
    assert inv_text.is_ambiguous is True
    assert "invalid calendar date" in inv_text.warning.lower()

    # ISO format with impossible day
    inv_iso = parse_date_string("2026-02-31")
    assert inv_iso.parsed_date is None
    assert inv_iso.is_ambiguous is True
    assert "invalid iso date" in inv_iso.warning.lower()

    # Ambiguous date with prefer_day_first=False
    amb_mdy = parse_date_string("04/05/2026", prefer_day_first=False)
    assert amb_mdy.parsed_date == date(2026, 4, 5)
    assert amb_mdy.is_ambiguous is True
    assert "mm-dd-yyyy" in amb_mdy.warning.lower()

