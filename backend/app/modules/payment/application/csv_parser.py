"""Bank statement CSV parsing engine.

Supports multi-layout formats (separate credit/debit, single amount + indicator, signed amount),
flexible header matching (HDFC, ICICI, SBI, Axis, HSBC, Barclays, standard formats),
multi-format date parsing, Indian/Western comma numeric normalization, formula injection defense,
and granular row-level error reporting without aborting valid rows.
"""

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import io
import re
from typing import Any, Dict, List, Optional, Tuple

from app.modules.payment.application.ports import BankStatementParser
from app.modules.payment.domain.entities import (
    CSVRowError,
    ParsedBankTransaction,
    TransactionType,
)


class CSVBankStatementParser(BankStatementParser):
    """Robust, multi-format bank statement CSV parser."""

    DATE_FORMATS = [
        "%Y-%m-%d",   # 2026-03-31 (ISO)
        "%d-%m-%Y",   # 31-03-2026 (Indian standard)
        "%d/%m/%Y",   # 31/03/2026 (Standard)
        "%d.%m.%Y",   # 31.03.2026
        "%Y/%m/%d",   # 2026/03/31
        "%d-%b-%Y",   # 31-Mar-2026
        "%d-%B-%Y",   # 31-March-2026
        "%b %d, %Y",  # Mar 31, 2026
        "%m/%d/%Y",   # 03/31/2026 (US fallback)
    ]

    AMOUNT_CLEAN_REGEX = re.compile(r"[^0-9.-]")
    UNSAFE_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

    UTR_PATTERNS = [
        re.compile(r"\b([A-Z]{4}[0-9]{10,16})\b"),
        re.compile(r"\b(?:UPI|IMPS|NEFT|RTGS)[/:_-]([A-Z0-9]{10,22})\b", re.IGNORECASE),
        re.compile(r"\bUTR[:\s-]*([A-Z0-9]{10,22})\b", re.IGNORECASE),
        re.compile(r"\bREF[:\s-]*([A-Z0-9]{8,22})\b", re.IGNORECASE),
    ]

    def parse(
        self,
        content: bytes,
        default_currency: str = "INR",
        bank_account_number: Optional[str] = None,
    ) -> Tuple[List[ParsedBankTransaction], List[CSVRowError]]:
        """Parse raw CSV bytes into validated transactions and granular row errors."""
        if not content or not content.strip():
            return [], [CSVRowError(row_number=1, field="file", message="CSV file is empty.")]

        # 1. Null-byte rejection
        if b"\x00" in content:
            return [], [CSVRowError(row_number=1, field="file", message="File contains illegal null byte characters.")]

        # 2. Decode bytes (BOM-safe utf-8-sig with latin-1 fallback)
        try:
            csv_text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                csv_text = content.decode("latin-1")
            except Exception as ex:
                return [], [CSVRowError(row_number=1, field="file", message=f"Unable to decode text: {str(ex)}")]

        lines = [line for line in csv_text.splitlines() if line.strip()]
        if not lines:
            return [], [CSVRowError(row_number=1, field="file", message="CSV file has no readable data lines.")]

        # 3. Detect Header Row & Delimiter (scanning up to top 20 lines)
        header_row_idx, delimiter = self._detect_header_row(lines)
        if header_row_idx == -1:
            return [], [
                CSVRowError(
                    row_number=1,
                    field="schema",
                    message="Could not detect valid bank statement headers. "
                    "Ensure headers include Date, Narration/Description, and Credit/Debit/Amount.",
                )
            ]

        # 4. Parse CSV text with DictReader starting at detected header
        csv_body = "\n".join(lines[header_row_idx:])
        reader = csv.DictReader(io.StringIO(csv_body), delimiter=delimiter)
        if not reader.fieldnames:
            return [], [CSVRowError(row_number=header_row_idx + 1, field="schema", message="Header row is empty or malformed.")]

        # 5. Build Column Index Mapping
        column_map = self._map_columns(reader.fieldnames)
        missing_mandatory = self._validate_mandatory_columns(column_map)
        if missing_mandatory:
            return [], [
                CSVRowError(
                    row_number=header_row_idx + 1,
                    field="schema",
                    message=f"Missing required columns: {', '.join(missing_mandatory)}.",
                )
            ]

        transactions: List[ParsedBankTransaction] = []
        errors: List[CSVRowError] = []

        # 6. Iterate and validate rows
        for rel_idx, raw_row in enumerate(reader, start=1):
            actual_row_num = header_row_idx + 1 + rel_idx

            # Skip blank rows
            if not any(val and val.strip() for val in raw_row.values() if val is not None):
                continue

            parsed_txn, row_errors = self._parse_single_row(
                raw_row=raw_row,
                row_num=actual_row_num,
                col_map=column_map,
                default_currency=default_currency,
                bank_account_number=bank_account_number,
            )

            if row_errors:
                errors.extend(row_errors)
            elif parsed_txn:
                transactions.append(parsed_txn)

        return transactions, errors

    def _parse_single_row(
        self,
        raw_row: Dict[str, str],
        row_num: int,
        col_map: Dict[str, str],
        default_currency: str,
        bank_account_number: Optional[str],
    ) -> Tuple[Optional[ParsedBankTransaction], List[CSVRowError]]:
        """Validate and construct a ParsedBankTransaction or return errors."""
        row_errors: List[CSVRowError] = []

        # 1. Parse Transaction Date
        date_raw = self._get_col_val(raw_row, col_map, "transaction_date")
        if not date_raw:
            row_errors.append(CSVRowError(row_number=row_num, field="transaction_date", message="Transaction date is required."))
            return None, row_errors

        txn_date = self._parse_date(date_raw)
        if not txn_date:
            row_errors.append(
                CSVRowError(
                    row_number=row_num,
                    field="transaction_date",
                    message=f"Invalid date format: '{date_raw}'. Expected standard date (YYYY-MM-DD or DD-MM-YYYY).",
                )
            )
            return None, row_errors

        # 2. Parse Optional Value Date
        value_date: Optional[date] = None
        val_date_raw = self._get_col_val(raw_row, col_map, "value_date")
        if val_date_raw:
            value_date = self._parse_date(val_date_raw)

        # 3. Parse Narration / Description
        narration = self._get_col_val(raw_row, col_map, "narration")
        if not narration:
            row_errors.append(
                CSVRowError(row_number=row_num, field="narration", message="Transaction narration or particulars is empty.")
            )
            return None, row_errors

        # Sanitize formula injection on narration
        narration = self._sanitize_string(narration)

        # 4. Resolve Amount and Transaction Type (Credit vs Debit)
        amount_res = self._resolve_amount_and_type(raw_row, col_map)
        if not amount_res["success"]:
            row_errors.append(
                CSVRowError(row_number=row_num, field=amount_res.get("field", "amount"), message=amount_res["error"])
            )
            return None, row_errors

        txn_type: TransactionType = amount_res["type"]
        amount: Decimal = amount_res["amount"]

        # 5. Extract / Sanitize Reference Number
        ref_raw = self._get_col_val(raw_row, col_map, "reference_number")
        reference_number = self._sanitize_string(ref_raw.strip()) if ref_raw and ref_raw.strip() else None
        if not reference_number:
            reference_number = self._extract_utr_from_narration(narration)

        # 6. Parse Running Balance (Optional)
        balance_raw = self._get_col_val(raw_row, col_map, "balance")
        balance: Optional[Decimal] = None
        if balance_raw:
            balance = self._parse_decimal(balance_raw)

        # 7. Currency
        curr_raw = self._get_col_val(raw_row, col_map, "currency")
        currency = curr_raw.strip().upper() if curr_raw and curr_raw.strip() else default_currency

        # 8. Counterparty Name (Optional)
        counterparty_raw = self._get_col_val(raw_row, col_map, "counterparty")
        clean_counterparty = self._sanitize_string(counterparty_raw) if counterparty_raw else None

        return (
            ParsedBankTransaction(
                row_number=row_num,
                transaction_date=txn_date,
                amount=amount,
                transaction_type=txn_type,
                narration=narration,
                reference_number=reference_number,
                value_date=value_date,
                balance=balance,
                currency=currency,
                bank_account_number=bank_account_number,
                raw_data={k: v for k, v in raw_row.items() if k is not None},
                counterparty_name=clean_counterparty,
            ),
            [],
        )

    def _resolve_amount_and_type(
        self, raw_row: Dict[str, str], col_map: Dict[str, str]
    ) -> Dict[str, Any]:
        """Resolve monetary amount and direction across Layouts A, B, and C."""
        has_credit_col = "credit" in col_map
        has_debit_col = "debit" in col_map
        has_amount_col = "amount" in col_map
        has_indicator_col = "type_indicator" in col_map

        # Layout A: Separate Credit / Debit Columns
        if has_credit_col and has_debit_col:
            credit_raw = self._get_col_val(raw_row, col_map, "credit")
            debit_raw = self._get_col_val(raw_row, col_map, "debit")

            cr_val = self._parse_decimal(credit_raw) if credit_raw else Decimal("0.00")
            dr_val = self._parse_decimal(debit_raw) if debit_raw else Decimal("0.00")

            if cr_val is None or dr_val is None:
                return {"success": False, "field": "amount", "error": "Invalid numeric characters in credit/debit column."}

            if cr_val > 0 and dr_val > 0:
                return {"success": False, "field": "amount", "error": f"Row has both credit ({cr_val}) and debit ({dr_val})."}

            if cr_val <= 0 and dr_val <= 0:
                return {"success": False, "field": "amount", "error": "Both credit and debit amounts are zero or empty."}

            if cr_val > 0:
                return {"success": True, "amount": cr_val, "type": TransactionType.CREDIT}
            return {"success": True, "amount": dr_val, "type": TransactionType.DEBIT}

        # Layout B: Amount + Dr/Cr Indicator Column
        if has_amount_col and has_indicator_col:
            amt_raw = self._get_col_val(raw_row, col_map, "amount")
            ind_raw = self._get_col_val(raw_row, col_map, "type_indicator").upper().strip()
            amt_val = self._parse_decimal(amt_raw)

            if amt_val is None or amt_val <= 0:
                return {"success": False, "field": "amount", "error": f"Invalid or non-positive amount '{amt_raw}'."}

            if ind_raw in {"CR", "CREDIT", "DEPOSIT", "+", "C"}:
                return {"success": True, "amount": amt_val, "type": TransactionType.CREDIT}
            elif ind_raw in {"DR", "DEBIT", "WITHDRAWAL", "-", "D"}:
                return {"success": True, "amount": amt_val, "type": TransactionType.DEBIT}
            else:
                return {"success": False, "field": "type_indicator", "error": f"Unknown transaction indicator '{ind_raw}'."}

        # Layout C: Signed Amount
        if has_amount_col:
            amt_raw = self._get_col_val(raw_row, col_map, "amount")
            amt_val = self._parse_decimal(amt_raw)
            if amt_val is None or amt_val == 0:
                return {"success": False, "field": "amount", "error": f"Invalid or zero amount '{amt_raw}'."}

            if amt_val > 0:
                return {"success": True, "amount": amt_val, "type": TransactionType.CREDIT}
            return {"success": True, "amount": abs(amt_val), "type": TransactionType.DEBIT}

        return {"success": False, "field": "schema", "error": "Cannot resolve monetary amount layout."}

    def _parse_date(self, date_str: str) -> Optional[date]:
        """Parse date string against recognized date formats."""
        cleaned = re.sub(r"\s+", " ", date_str.strip())
        for fmt in self.DATE_FORMATS:
            try:
                return datetime.strptime(cleaned, fmt).date()
            except ValueError:
                continue
        return None

    def _parse_decimal(self, val_str: str) -> Optional[Decimal]:
        """Convert raw amount string with commas, currency symbols, and spaces to exact 2-decimal Decimal.
        
        Strictly rejects CSV formula injection prefixes (=, @, \\t, \\r) and invalid numeric structures.
        """
        if not val_str or not val_str.strip():
            return None
        raw = val_str.strip()
        # Reject spreadsheet formula injection on numeric amounts
        if raw.startswith(("=", "@", "\t", "\r")):
            return None
        # Disallow alphabetic characters (e.g. command injection, formula names)
        if any(c.isalpha() for c in raw):
            return None

        # Clean spaces, currency symbols, and commas while preserving sign (+/-) and dot
        cleaned = re.sub(r"[^\d.+-]", "", raw)
        if not cleaned or cleaned in ("-", "+", ".", "-.", "+."):
            return None
        # Ensure at most one sign and one decimal point
        if cleaned.count("+") > 1 or cleaned.count("-") > 1:
            return None
        if "+" in cleaned and not cleaned.startswith("+"):
            return None
        if "-" in cleaned and not cleaned.startswith("-"):
            return None
        if cleaned.count(".") > 1:
            return None

        try:
            dec = Decimal(cleaned).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return dec
        except (InvalidOperation, TypeError):
            return None

    def _sanitize_string(self, val: str) -> str:
        """Sanitize text to neutralize CSV formula injection (CWE-1236)."""
        if not val:
            return ""
        val = val.strip()
        if val.startswith(self.UNSAFE_FORMULA_PREFIXES):
            return "'" + val
        return val

    def _extract_utr_from_narration(self, narration: str) -> Optional[str]:
        """Search bank narration for standard transaction reference / UTR tokens."""
        for pattern in self.UTR_PATTERNS:
            match = pattern.search(narration)
            if match:
                cand = match.group(1).strip()
                if cand.isalnum() and len(cand) >= 6:
                    return self._sanitize_string(cand)
        return None

    def _detect_header_row(self, lines: List[str]) -> Tuple[int, str]:
        """Identify which line contains table headers and determine delimiter."""
        delimiters = [",", "\t", ";", "|"]
        for idx, line in enumerate(lines[:20]):
            for delim in delimiters:
                tokens = [t.strip().lower() for t in line.split(delim)]
                if len(tokens) <= 1:
                    continue
                has_date = any("date" in t for t in tokens)
                has_desc = any(any(k in t for k in ["narration", "desc", "particular", "remark", "detail"]) for t in tokens)
                has_amt = any(any(k in t for k in ["credit", "debit", "amount", "deposit", "withdrawal", "balance"]) for t in tokens)
                if (has_date and has_desc) or (has_date and has_amt) or (has_desc and has_amt):
                    return idx, delim
        return -1, ","

    def _map_columns(self, fieldnames: List[str]) -> Dict[str, str]:
        """Map raw header names to canonical field keys."""
        mapping: Dict[str, str] = {}
        for raw_name in fieldnames:
            if raw_name is None:
                continue
            norm = re.sub(r"[^a-z0-9/]", " ", raw_name.lower()).strip()
            norm = re.sub(r"\s+", " ", norm)
            for canon_key, patterns in self._header_patterns().items():
                if canon_key not in mapping:
                    matched = False
                    for p in patterns:
                        if p in ("cr", "dr"):
                            # 'cr' and 'dr' must not match inside 'dr/cr', 'cr/dr', or 'd/c'
                            if p in norm and "dr/cr" not in norm and "cr/dr" not in norm and "d/c" not in norm:
                                matched = True
                                break
                        elif p in norm:
                            matched = True
                            break
                    if matched:
                        mapping[canon_key] = raw_name
                        break
        return mapping

    def _header_patterns(self) -> Dict[str, List[str]]:
        """Canonical header matching taxonomy.
        
        Evaluates type_indicator BEFORE credit and debit to prevent 'Dr/Cr' columns
        from colliding with 'cr' substring matching.
        """
        return {
            "type_indicator": ["dr/cr", "cr/dr", "txn type", "transaction type", "type", "d/c"],
            "transaction_date": ["txn date", "transaction date", "trans date", "booking date", "posting date", "date"],
            "value_date": ["value date", "val date", "effective date"],
            "narration": ["narration", "particular", "description", "remark", "detail", "memo"],
            "reference_number": ["ref no", "reference no", "reference", "utr", "chq", "cheque", "journal", "txn id"],
            "credit": ["credit", "deposit", "cr amount", "cr"],
            "debit": ["debit", "withdrawal", "dr amount", "dr"],
            "amount": ["txn amount", "transaction amount", "net amount", "amount"],
            "balance": ["running bal", "closing bal", "balance", "bal"],
            "currency": ["currency", "ccy", "curr"],
            "counterparty": ["counterparty", "beneficiary", "payee", "party name"],
        }

    def _validate_mandatory_columns(self, col_map: Dict[str, str]) -> List[str]:
        """Verify that essential columns exist in the header mapping."""
        missing = []
        if "transaction_date" not in col_map:
            missing.append("Transaction Date")
        if "narration" not in col_map:
            missing.append("Narration / Description")
        has_amount = ("credit" in col_map and "debit" in col_map) or ("amount" in col_map)
        if not has_amount:
            missing.append("Amount (Credit/Debit or Amount column)")
        return missing

    def _get_col_val(self, row: Dict[str, str], col_map: Dict[str, str], key: str) -> str:
        """Safely extract cell value for a mapped canonical key."""
        col_name = col_map.get(key)
        if not col_name:
            return ""
        val = row.get(col_name, "")
        return val.strip() if val else ""
