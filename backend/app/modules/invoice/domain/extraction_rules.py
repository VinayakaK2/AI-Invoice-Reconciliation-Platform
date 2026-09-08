"""app/modules/invoice/domain/extraction_rules.py

Deterministic extraction, normalization, and financial validation rules.
Implements exact Decimal monetary parsing, Indian and Western number grouping,
canonical date normalization, multi-signal identifier separation, and balance consistency.
"""

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

# -----------------------------------------------------------------------------
# 1. Regex Definitions & Mappings
# -----------------------------------------------------------------------------

# Indian numbering scheme (e.g., 1,50,000.00 or 12,34,567.89 or 50,000.00)
INDIAN_NUMBER_REGEX = re.compile(
    r"^(?:[0-9]{1,2}(?:,[0-9]{2})+|[0-9]{1,3}),[0-9]{3}(?:\.[0-9]{1,2})?$"
)

# Western numbering scheme (e.g., 150,000.00 or 1,234,567.89)
WESTERN_NUMBER_REGEX = re.compile(
    r"^[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?$"
)

# Plain numeric string (e.g., 150000.00 or 150000)
PLAIN_NUMBER_REGEX = re.compile(
    r"^[0-9]+(?:\.[0-9]{1,2})?$"
)

# Currency symbol and code lookup map
CURRENCY_MAP: Dict[str, str] = {
    "₹": "INR",
    "rs": "INR",
    "rs.": "INR",
    "inr": "INR",
    "$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
}

# Month names lookup for date parsing
MONTH_LOOKUP: Dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}

# Indian GSTIN: 2 state digits + 10-char PAN + 1 entity + 1 'Z' + 1 checksum
GSTIN_REGEX = re.compile(
    r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b"
)

# Indian PAN: 5 alpha + 4 numeric + 1 alpha
PAN_REGEX = re.compile(
    r"\b([A-Z]{5}[0-9]{4}[A-Z]{1})\b"
)

# European VAT identifier (e.g. GB123456789, DE123456789)
VAT_REGEX = re.compile(
    r"\b((?:GB|DE|FR|IT|ES)[0-9A-Z]{8,12})\b"
)

# Purchase Order (PO) Pattern
PO_NUMBER_REGEX = re.compile(
    r"(?i)\b(?:P\.?O\.?\s*(?:No\.?|Num\.?|Number|#)?|Purchase\s*Order\s*(?:No\.?|Num\.?|#)?)\s*[:#]?\s*([A-Za-z0-9\-_/]{3,30})\b"
)

# Payment UTR / Reference Pattern
PAYMENT_REF_REGEX = re.compile(
    r"(?i)\b(?:UTR\s*(?:No\.?|#)?|Payment\s*Ref\s*(?:No\.?|#)?|Transaction\s*(?:ID|Ref|No\.?))\s*[:#]?\s*([A-Za-z0-9]{8,22})\b"
)

# Labeled Invoice Number Pattern
INVOICE_NUMBER_LABELED_REGEX = re.compile(
    r"(?i)\b(?:invoice\s*(?:number|num\.?|no\.?|#)?|tax\s*invoice\s*(?:number|num\.?|no\.?|#)?|bill\s*(?:number|num\.?|no\.?|#)?|inv)\s*[:#]\s*([A-Za-z0-9\-_/]{3,35})\b"
)

# Stopwords disallowed as invoice numbers
DISALLOWED_INVOICE_STOPWORDS: Set[str] = {
    "NUMBER", "DATE", "AMOUNT", "TOTAL", "CLIENT", "CUSTOMER",
    "DRAFT", "ORIGINAL", "DUPLICATE", "TRIPLICATE", "TAX", "INVOICE",
    "CASH", "MEMO", "BILL", "PAGE", "COPY", "SUBTOTAL", "BALANCE",
}

# -----------------------------------------------------------------------------
# 2. Data Transfer Models
# -----------------------------------------------------------------------------

class MonetaryNormalizationResult(BaseModel):
    """Result of monetary parsing and validation."""
    amount: Optional[Decimal] = None
    currency: str = "INR"
    is_valid: bool = False
    error: Optional[str] = None


class DateNormalizationResult(BaseModel):
    """Result of date parsing and ambiguity detection."""
    parsed_date: Optional[date] = None
    is_ambiguous: bool = False
    warning: Optional[str] = None


class MathValidationResult(BaseModel):
    """Result of deterministic mathematical consistency validation."""
    is_valid: bool
    calculated_total: Optional[Decimal] = None
    extracted_total: Optional[Decimal] = None
    discrepancy: Decimal = Decimal("0.00")
    notes: List[str] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# 3. Monetary Parsing
# -----------------------------------------------------------------------------

def parse_monetary_amount(
    raw_str: Optional[str],
    default_currency: str = "INR",
) -> MonetaryNormalizationResult:
    """Parse raw text into an exact Decimal quantized to 2 decimal places.

    Enforces Indian and Western comma grouping validations, strips formatting
    and currency symbols, and neutralizes formula injection attempts.
    """
    if not raw_str or not raw_str.strip():
        return MonetaryNormalizationResult(
            amount=None, currency=default_currency, is_valid=False, error="Empty monetary string"
        )

    raw_clean = raw_str.strip()

    # Formula injection defense (CWE-1236)
    if raw_clean.startswith(("=", "@", "\t", "\r")):
        return MonetaryNormalizationResult(
            amount=None, currency=default_currency, is_valid=False, error="Formula injection detected in monetary string"
        )

    cleaned = raw_clean
    if ":" in cleaned:
        cleaned = cleaned.split(":")[-1].strip()

    # Detect currency prefix or suffix
    detected_currency = default_currency
    for symbol, iso in CURRENCY_MAP.items():
        if symbol in cleaned.lower():
            detected_currency = iso
            break

    # Strip currency tokens, Indian "/-" suffix, and extra symbols
    num_str = re.sub(r"(?i)(?:₹|\$|€|£|INR|USD|EUR|GBP|RS\.?|/-)", "", cleaned).strip()

    # Reject unexpected alphabetical characters inside the numeric portion
    if any(c.isalpha() for c in num_str):
        return MonetaryNormalizationResult(
            amount=None,
            currency=detected_currency,
            is_valid=False,
            error=f"Invalid alphabetical characters in amount: '{raw_str}'",
        )

    # Validate comma structure if present
    if "," in num_str:
        if not (INDIAN_NUMBER_REGEX.match(num_str) or WESTERN_NUMBER_REGEX.match(num_str)):
            return MonetaryNormalizationResult(
                amount=None,
                currency=detected_currency,
                is_valid=False,
                error=f"Malformed comma grouping in monetary amount: '{num_str}'",
            )

    # Strip commas for exact Decimal construction
    plain_num = num_str.replace(",", "")

    try:
        dec = Decimal(plain_num)
        # Quantize strictly to 2 decimal places using half-up rounding
        quantized = dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if quantized < Decimal("0.00"):
            return MonetaryNormalizationResult(
                amount=None,
                currency=detected_currency,
                is_valid=False,
                error=f"Negative monetary amount forbidden: {quantized}",
            )
        return MonetaryNormalizationResult(
            amount=quantized,
            currency=detected_currency,
            is_valid=True,
            error=None,
        )
    except (InvalidOperation, TypeError) as ex:
        return MonetaryNormalizationResult(
            amount=None,
            currency=detected_currency,
            is_valid=False,
            error=f"Decimal conversion failure: {str(ex)}",
        )


# -----------------------------------------------------------------------------
# 4. Date Normalization & Temporal Consistency
# -----------------------------------------------------------------------------

def parse_date_string(
    date_str: Optional[str],
    prefer_day_first: bool = True,
) -> DateNormalizationResult:
    """Normalize raw date strings into canonical datetime.date instances.

    Supports textual month representations ('15 Jan 2024') and numeric structures.
    For numeric dates where day <= 12 and month <= 12, defaults to prefer_day_first (DD/MM/YYYY)
    and flags the date as ambiguous for review.
    """
    if not date_str or not date_str.strip():
        return DateNormalizationResult(parsed_date=None, is_ambiguous=False, warning="Empty date string")

    clean = re.sub(r"\s+", " ", date_str.strip())

    # 1. Textual Day-Month-Year: '15 Jan 2024', '15th January 2024', '15-Mar-2026'
    m_text1 = re.search(
        r"\b(?P<day>\d{1,2})(?:st|nd|rd|th)?[\s-]+(?P<month>[A-Za-z]{3,9})[\s,]+(?P<year>\d{4})\b",
        clean,
        re.IGNORECASE,
    )
    if m_text1:
        d = int(m_text1.group("day"))
        m_str = m_text1.group("month").lower()
        y = int(m_text1.group("year"))
        if m_str in MONTH_LOOKUP:
            try:
                return DateNormalizationResult(
                    parsed_date=date(y, MONTH_LOOKUP[m_str], d), is_ambiguous=False, warning=None
                )
            except ValueError as ex:
                return DateNormalizationResult(parsed_date=None, is_ambiguous=True, warning=f"Invalid calendar date: {ex}")

    # 2. Textual Month-Day-Year: 'Jan 15, 2024', 'January 15 2024'
    m_text2 = re.search(
        r"\b(?P<month>[A-Za-z]{3,9})[\s]+(?P<day>\d{1,2})(?:st|nd|rd|th)?[\s,]+(?P<year>\d{4})\b",
        clean,
        re.IGNORECASE,
    )
    if m_text2:
        d = int(m_text2.group("day"))
        m_str = m_text2.group("month").lower()
        y = int(m_text2.group("year"))
        if m_str in MONTH_LOOKUP:
            try:
                return DateNormalizationResult(
                    parsed_date=date(y, MONTH_LOOKUP[m_str], d), is_ambiguous=False, warning=None
                )
            except ValueError as ex:
                return DateNormalizationResult(parsed_date=None, is_ambiguous=True, warning=f"Invalid calendar date: {ex}")

    # 3. Numeric Date Formats (e.g., 2024-05-15, 15-05-2024, 15/05/2024)
    m_num = re.search(r"\b(?P<first>\d{1,4})[-/.](?P<second>\d{1,2})[-/.](?P<third>\d{2,4})\b", clean)
    if m_num:
        p1, p2, p3 = m_num.group("first"), m_num.group("second"), m_num.group("third")

        # ISO format: YYYY-MM-DD
        if len(p1) == 4:
            y, m, d = int(p1), int(p2), int(p3)
            try:
                return DateNormalizationResult(parsed_date=date(y, m, d), is_ambiguous=False, warning=None)
            except ValueError as ex:
                return DateNormalizationResult(parsed_date=None, is_ambiguous=True, warning=f"Invalid ISO date: {ex}")

        # Non-ISO: DD-MM-YYYY vs MM-DD-YYYY
        if len(p3) in (2, 4):
            y = int(p3) if len(p3) == 4 else (2000 + int(p3))
            v1, v2 = int(p1), int(p2)

            # Sub-case: Unambiguous because v1 > 12 -> must be DD-MM-YYYY
            if v1 > 12 and v2 <= 12:
                try:
                    return DateNormalizationResult(parsed_date=date(y, v2, v1), is_ambiguous=False, warning=None)
                except ValueError as ex:
                    return DateNormalizationResult(parsed_date=None, is_ambiguous=True, warning=f"Invalid date: {ex}")

            # Sub-case: Unambiguous because v2 > 12 -> must be MM-DD-YYYY
            if v2 > 12 and v1 <= 12:
                try:
                    return DateNormalizationResult(parsed_date=date(y, v1, v2), is_ambiguous=False, warning=None)
                except ValueError as ex:
                    return DateNormalizationResult(parsed_date=None, is_ambiguous=True, warning=f"Invalid date: {ex}")

            # Sub-case: Ambiguous (both <= 12, e.g. 04/05/2024)
            if v1 <= 12 and v2 <= 12:
                if prefer_day_first:
                    chosen = date(y, v2, v1)
                    msg = f"Ambiguous date '{clean}': interpreted as DD-MM-YYYY ({chosen.isoformat()})"
                else:
                    chosen = date(y, v1, v2)
                    msg = f"Ambiguous date '{clean}': interpreted as MM-DD-YYYY ({chosen.isoformat()})"
                return DateNormalizationResult(parsed_date=chosen, is_ambiguous=True, warning=msg)

    return DateNormalizationResult(
        parsed_date=None, is_ambiguous=True, warning=f"Unrecognized date structure: '{date_str}'"
    )


def validate_temporal_consistency(
    issue_date: Optional[date],
    due_date: Optional[date],
) -> Tuple[bool, List[str]]:
    """Assert temporal order invariant: due_date >= issue_date."""
    warnings: List[str] = []
    if issue_date and due_date:
        if due_date < issue_date:
            warnings.append(
                f"Temporal invariant violated: due_date ({due_date}) is earlier than issue_date ({issue_date})."
            )
            return False, warnings
    elif issue_date and not due_date:
        warnings.append("Due date absent; defaulting due date to issue date.")
    return True, warnings


# -----------------------------------------------------------------------------
# 5. Identifier Extraction & Disambiguation
# -----------------------------------------------------------------------------

def extract_identifiers(raw_text: str) -> Dict[str, Any]:
    """Extract, categorize, and cross-exclude identifiers from text."""
    results: Dict[str, Any] = {
        "invoice_number": None,
        "tax_id": None,
        "pan": None,
        "po_number": None,
        "payment_ref": None,
        "notes": [],
    }

    if not raw_text:
        return results

    # 1. Tax Identifiers
    gstin_match = GSTIN_REGEX.search(raw_text)
    if gstin_match:
        gstin_val = gstin_match.group(1).upper()
        results["tax_id"] = gstin_val
        results["pan"] = gstin_val[2:12]

    pan_match = PAN_REGEX.search(raw_text)
    if pan_match and not results["pan"]:
        results["pan"] = pan_match.group(1).upper()

    vat_match = VAT_REGEX.search(raw_text)
    if vat_match and not results["tax_id"]:
        results["tax_id"] = vat_match.group(1).upper()

    # 2. Purchase Order Number
    po_match = PO_NUMBER_REGEX.search(raw_text)
    if po_match:
        cand_po = po_match.group(1).strip()
        if cand_po.upper() not in DISALLOWED_INVOICE_STOPWORDS:
            results["po_number"] = cand_po

    # 3. Payment Reference / UTR
    pay_match = PAYMENT_REF_REGEX.search(raw_text)
    if pay_match:
        results["payment_ref"] = pay_match.group(1).strip()

    # 4. Invoice Number with Cross-Exclusion
    inv_match = INVOICE_NUMBER_LABELED_REGEX.search(raw_text)
    candidate_inv = None
    if inv_match:
        cand = inv_match.group(1).strip()
        cand_upper = cand.upper()

        # Invariant: Invoice Number MUST NOT collide with Tax ID, PAN, PO, or stopword
        is_tax_id = (results["tax_id"] and cand_upper == results["tax_id"]) or (
            results["pan"] and cand_upper == results["pan"]
        )
        is_po = results["po_number"] and cand_upper == results["po_number"].upper()
        is_stopword = cand_upper in DISALLOWED_INVOICE_STOPWORDS or len(cand) < 3

        if not (is_tax_id or is_po or is_stopword):
            candidate_inv = cand
        else:
            results["notes"].append(
                f"Rejected candidate invoice number '{cand}': collided with Tax ID, PO, or stopword."
            )

    # Fallback to standard prefix patterns like 'INV-12345'
    if not candidate_inv:
        fallback_match = re.search(r"\b(INV[/-][A-Za-z0-9\-_/]{3,25})\b", raw_text, re.IGNORECASE)
        if fallback_match:
            cand = fallback_match.group(1).strip()
            if cand.upper() not in DISALLOWED_INVOICE_STOPWORDS:
                candidate_inv = cand

    results["invoice_number"] = candidate_inv
    if not candidate_inv:
        results["notes"].append("Invoice number could not be detected with high confidence.")

    return results


# -----------------------------------------------------------------------------
# 6. Deterministic Mathematical Validation
# -----------------------------------------------------------------------------

def validate_invoice_mathematics(
    total_amount: Optional[Decimal],
    subtotal: Optional[Decimal],
    tax_amount: Optional[Decimal],
    discount_amount: Optional[Decimal] = None,
    round_off: Optional[Decimal] = None,
    tolerance: Decimal = Decimal("0.05"),
) -> MathValidationResult:
    """Deterministically validate subtotal + tax - discount + round_off ≈ total.

    Enforces Rule 20: NEVER silently correct or adjust discrepancies.
    Flags differences exceeding tolerance as validation errors.
    """
    notes: List[str] = []

    if total_amount is None:
        return MathValidationResult(
            is_valid=False,
            calculated_total=None,
            extracted_total=None,
            discrepancy=Decimal("0.00"),
            notes=["Total amount is missing."],
        )

    if subtotal is None and tax_amount is None:
        # Subtotal and tax not detected; cannot cross-verify arithmetic
        notes.append("Subtotal and tax amount absent; mathematical cross-check skipped.")
        return MathValidationResult(
            is_valid=True,
            calculated_total=total_amount,
            extracted_total=total_amount,
            discrepancy=Decimal("0.00"),
            notes=notes,
        )

    calc_subtotal = subtotal if subtotal is not None else Decimal("0.00")
    calc_tax = tax_amount if tax_amount is not None else Decimal("0.00")
    calc_discount = discount_amount if discount_amount is not None else Decimal("0.00")
    calc_round = round_off if round_off is not None else Decimal("0.00")

    expected_total = calc_subtotal + calc_tax - calc_discount + calc_round
    discrepancy = abs(expected_total - total_amount)

    # If round-off was explicitly provided, allow up to 1.00 tolerance (CGST Act Sec 170)
    effective_tolerance = Decimal("1.00") if round_off is not None else tolerance

    if discrepancy <= effective_tolerance:
        if discrepancy > Decimal("0.00"):
            notes.append(f"Minor rounding variance of {discrepancy} accepted within tolerance {effective_tolerance}.")
        return MathValidationResult(
            is_valid=True,
            calculated_total=expected_total,
            extracted_total=total_amount,
            discrepancy=discrepancy,
            notes=notes,
        )

    # Discrepancy violation
    notes.append(
        f"Mathematical validation mismatch: Subtotal ({calc_subtotal}) + Tax ({calc_tax}) - Discount ({calc_discount}) "
        f"= {expected_total}, but Extracted Total is {total_amount}. Discrepancy: {discrepancy} > {effective_tolerance}."
    )
    return MathValidationResult(
        is_valid=False,
        calculated_total=expected_total,
        extracted_total=total_amount,
        discrepancy=discrepancy,
        notes=notes,
    )


# -----------------------------------------------------------------------------
# 7. Confidence Scoring & Ambiguity Evaluation
# -----------------------------------------------------------------------------

def evaluate_extraction_confidence_and_ambiguity(
    invoice_number: Optional[str],
    total_amount: Optional[Decimal],
    issue_date: Optional[date],
    due_date: Optional[date],
    customer_name: Optional[str],
    tax_id: Optional[str],
    subtotal: Optional[Decimal],
    tax_amount: Optional[Decimal],
    math_result: MathValidationResult,
    date_ambiguous: bool,
    temporal_valid: bool,
) -> Tuple[Decimal, bool, List[str]]:
    """Compute weighted extraction confidence score and ambiguity status."""
    score = Decimal("0.00")
    notes = list(math_result.notes)

    # 1. Invoice Number (0.25)
    if invoice_number:
        score += Decimal("0.25")
    else:
        notes.append("Critical: Missing invoice number.")

    # 2. Total Amount (0.25)
    if total_amount and total_amount > Decimal("0.00"):
        score += Decimal("0.25")
    else:
        notes.append("Critical: Missing or non-positive total amount.")

    # 3. Issue Date (0.15)
    if issue_date:
        if date_ambiguous:
            score += Decimal("0.08")
            notes.append("Warning: Issue date required format disambiguation.")
        else:
            score += Decimal("0.15")
    else:
        notes.append("Critical: Missing issue date.")

    # 4. Customer Identity / Tax ID (0.15)
    if tax_id:
        score += Decimal("0.15")
    elif customer_name:
        score += Decimal("0.10")
    else:
        notes.append("Warning: Both Customer Name and Tax ID are absent.")

    # 5. Mathematical Consistency (0.10)
    if math_result.is_valid and subtotal is not None:
        score += Decimal("0.10")

    # 6. Due Date Validity (0.10)
    if due_date and temporal_valid:
        score += Decimal("0.10")
    elif not temporal_valid:
        notes.append("Critical: Due date violates temporal invariant (due_date < issue_date).")

    final_confidence = min(Decimal("1.00"), score)

    # Ambiguity triggers: confidence < 0.85, missing essentials, math failure, or temporal error
    is_ambiguous = (
        final_confidence < Decimal("0.85")
        or invoice_number is None
        or total_amount is None
        or issue_date is None
        or (customer_name is None and tax_id is None)
        or not math_result.is_valid
        or not temporal_valid
    )

    return final_confidence, is_ambiguous, notes
