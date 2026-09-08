# Phase 13 — Payment & Bank Statement Ingestion: Phase History & Forensic Freeze Certification

**Phase:** Phase 13 — Payment & Bank Statement Ingestion  
**Status:** VERIFIED, AUDITED & PERMANENTLY FROZEN  
**Verification Date:** 2026-09-06  
**Test Suite:** 157 tests passed, 0 failures, 1 warning (93% total platform coverage across 3,590 statements)  
**Migration Chain:** `0001 -> 0002 -> 0003 -> 0004_phase_11_archive -> 0005_phase_12`

---

## 1. Executive Summary & Phase Objective

The objective of Phase 12 is to establish the authoritative ingestion foundation for incoming bank statements and payment records. Later phases (Phase 14 Reconciliation Engine & Phase 15 Review Queue) consume these normalized payments to match against outstanding invoices.

### Core Domain Separation
```text
Raw Bank Transaction != Payment != Reconciliation Decision
```
1. **Raw Bank Transaction** (`BankTransaction`): The unaltered source truth reported by the bank. Ingests both `CREDIT` (inflow) and `DEBIT` (outflow) records, preserving raw rows, balance, and account metadata for cashflow tracking and audit compliance.
2. **Payment** (`Payment`): Normalized incoming receivable candidate created **exclusively from incoming CREDIT transactions** (or manual offline receipt entry).
3. **Reconciliation Decision**: Strictly deferred to Phase 14/15. Phase 12 contains zero invoice matching, zero candidate ranking, zero confidence scoring, and zero invoice allocation.

---

## 2. Implemented Capabilities & Architecture

### 2.1 Domain Layer (`backend/app/modules/payment/domain/`)
- **Entities & Invariants**:
  - `ImportBatch`: Batch lifecycle entity tracking `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`. Row accounting invariant verifies `total_rows == imported_count + failed_count + duplicate_count + skipped_debits`.
  - `BankTransaction`: Immutable raw transaction storing `amount: Money`, `transaction_type: TransactionType`, `balance: Optional[Money]`, `raw_row: dict`, `counterparty_name: Optional[str]`, and deterministic `deduplication_hash`.
  - `Payment`: Aggregate root representing incoming receivables with exact balance conservation:
    $$\text{amount} = \text{allocated\_amount} + \text{unallocated\_amount}$$
    $$\text{amount} > 0, \quad \text{allocated\_amount} \ge 0, \quad \text{unallocated\_amount} \ge 0$$
  - `TransactionFingerprint`: Deterministic SHA-256 fingerprinting:
    - Primary: `REF|{company_id}|{account_identifier}|{norm_ref}` (with generic placeholder rejection).
    - Fallback: `FALLBACK|{company_id}|{account}|{date}|{amount}|{currency}|{type}|{norm_bal}|{narration}` (with running balance disambiguation).

### 2.2 Application Layer (`backend/app/modules/payment/application/`)
- **Multi-Layout CSV Parsing Engine (`CSVBankStatementParser`)**:
  - Automatically identifies layout styles: Layout A (separate Credit/Debit columns), Layout B (Amount + Dr/Cr indicator), Layout C (Signed Amount).
  - Skips up to 20 preface metadata rows (account number, statement period).
  - Multi-format date parsing (`YYYY-MM-DD`, `DD-MM-YYYY`, `DD/MM/YYYY`, `DD.MM.YYYY`, `DD-Mon-YYYY`, `MM/DD/YYYY`).
  - Cleans Indian (`1,25,000.00`, `₹`) and Western (`125,000.00`, `$`) formatted numbers into exact `Decimal`.
  - Strict numeric validation in `_parse_decimal()`: Rejects spreadsheet formula injection prefixes (`=`, `@`, `\t`, `\r`) on numeric fields.
  - Fallback UTR/NEFT/UPI/IMPS extraction from narration via regex.
  - Formula injection defense (CWE-1236): Prepends single quote `'` to text fields starting with `=`, `+`, `-`, `@`, `\t`, `\r`.
  - Emits granular `CSVRowError` items without aborting valid rows.
- **3 Deduplication Gates**:
  1. **Gate 1 (File Content SHA-256)**: Rejects identical full-file re-imports with HTTP 409 Conflict. Allows retrying files from `FAILED` batches.
  2. **Gate 2 (Intra-Batch Memory Tracking)**: Rejects duplicate lines within the same CSV.
  3. **Gate 3 (Database Constraint & Idempotency)**: Skips existing transactions idempotently when `skip_duplicates=True`.
- **Atomic Savepoint Isolation**:
  - Ingestion wraps row persistence in `with db.begin_nested():` savepoints, preventing PostgreSQL/SQLite session abortion on individual invalid rows.

### 2.3 Infrastructure Layer (`backend/app/modules/payment/infrastructure/`)
- **SQLAlchemy 2.0 ORM Models**:
  - `ImportBatchModel` (`import_batches`): Tenant-scoped foreign key to `companies.id ON DELETE CASCADE`, unique constraint `uq_import_batches_company_hash`.
  - `BankTransactionModel` (`bank_transactions`): Tenant-scoped foreign key, unique constraint `uq_bank_transactions_company_dedup`, check constraint `amount > 0`.
  - `PaymentModel` (`payments`): Foreign key to `companies.id`, check constraints `chk_payment_amount_positive`, `chk_payment_allocated_non_negative`, `chk_payment_unallocated_non_negative`, and `chk_payment_balance`.
- **Alembic Migration (`0005_phase_12_payments.py`)**:
  - Strictly append-only.
  - `revision = "0005_phase_12"`
  - `down_revision = "0004_phase_11_archive"`
- **Tenant-Scoped Repositories**:
  - `ImportBatchRepository`, `BankTransactionRepository`, `PaymentRepository` enforce `company_id` filter on every query and mutation.

### 2.4 Presentation Layer (`backend/app/modules/payment/presentation/`)
- **REST Endpoints (`/api/v1/payments`)**:
  - `POST /upload-statement`: Multipart file upload (10MB limit, `.csv`/`.txt` whitelist).
  - `GET /batches`: Paginated import batch history.
  - `GET /batches/{batch_id}`: Batch detail with associated transactions (fail-closed 404).
  - `GET /transactions`: Filterable raw bank transactions (both credit and debit).
  - `GET /`: Filterable normalized payments (`status`, date range, amount range, search).
  - `POST /`: Manual offline payment receipt creation with reference conflict check.
  - `GET /{payment_id}`: Payment detail with masked bank account (`••••••••1234`).
  - `POST /{payment_id}/ignore`: Accountant workflow to ignore non-invoice payments.
  - `POST /{payment_id}/unignore`: Restore ignored payment to `UNRECONCILED`.

---

## 3. Forensic Audit Findings & Remediations

An exhaustive forensic audit conducted across 8 specialized dimensions uncovered 4 material technical defects, which were surgically resolved:

| Defect # | Severity | Root Cause | Surgical Remediation | Status |
|---|---|---|---|---|
| **Defect 1** | **CRITICAL** | Substring `"cr"` in `_header_patterns` matched `"Dr/Cr"` in Layout B headers, causing Layout B to misclassify all debits as credits under Layout C. | Prioritized `type_indicator` before `credit`/`debit`, and isolated `"cr"`/`"dr"` from matching inside `"dr/cr"` or `"cr/dr"`. | **RESOLVED** |
| **Defect 2** | **HIGH** | `_parse_decimal` stripped non-numeric characters from formulas (`@SUM(1+1)` parsed to `11.00`). | Validated numeric syntax; strictly rejected formula prefixes (`=`, `@`, etc.) on amount cells. | **RESOLVED** |
| **Defect 3** | **HIGH** | Gate 1 blocked retrying failed uploads because `get_by_hash()` didn't inspect batch status. Concurrent batch creation caused unhandled 500. | Permitted re-upload when prior batch status is `FAILED`. Wrapped batch creation in `try...except IntegrityError` to return HTTP 409 Conflict. | **RESOLVED** |
| **Defect 4** | **MEDIUM** | Primary fingerprint omitted `account_identifier` and placeholder checks. Fallback fingerprint omitted balance. `clean_counterparty` was omitted from transaction entity. | Scoped primary hash by account, rejected generic placeholders (`NA`, `0`, `NONE`), included statement balance in fallback hash, and propagated `counterparty_name`. | **RESOLVED** |

---

## 4. The 10 Special Forensic Verification Tests

All 10 special forensic tests are implemented and verified in `backend/tests/integration/test_payment_forensic_special.py`:

| # | Special Test Requirement | Verification Result | Proven Evidence |
|---|---|---|---|
| **1** | Signed Amount (`+10000.00`, `-5000.00`) | ✅ **PASSED** | Layout C converts `+10000.00` to Payment; records `-5000.00` as DEBIT in `bank_transactions` and skips from receivable payments. |
| **2** | Formula-like Narration vs Amount Field | ✅ **PASSED** | Text fields prefixed with `'` (`'=SUM`, `'+CMD`, `'@user`, `'-flag`); formula in amount rejected as invalid numeric. |
| **3** | Exact Duplicate File Upload | ✅ **PASSED** | Re-uploading identical file returns HTTP 409 Conflict with standard error envelope. |
| **4** | Failed Import Batch Retry | ✅ **PASSED** | Failed batch due to malformed headers does not block subsequent successful upload. |
| **5** | Concurrent Duplicate Import | ✅ **PASSED** | Database unique constraint collision in worker race returns 409 Conflict instead of unhandled 500. |
| **6** | Fallback Collision Disambiguation | ✅ **PASSED** | Two same-day identical amount transactions disambiguated via statement running balance. |
| **7** | Cross-Tenant Bank Transaction Isolation | ✅ **PASSED** | Company B listing `/payments/transactions` sees 0 records of Company A. |
| **8** | Allocation Mutation Endpoint Unavailable | ✅ **PASSED** | `POST /payments/{id}/allocate` and `PUT /payments/{id}` return HTTP 404 / 405. |
| **9** | Zero Invoice Mutation | ✅ **PASSED** | Existing database invoices remain 100% immutable in status and balances during statement ingestion. |
| **10** | Alembic Migration 0005 Lifecycle | ✅ **PASSED** | Automated programmatic upgrade to head, downgrade to `0004_phase_11_archive`, and re-upgrade to head verified. |

---

## 5. Documented Limitations & Edge Cases

1. **Fallback Deduplication Heuristic**:
   When bank statements lack unique reference numbers (UTR/cheque numbers) AND running balance columns, identical transactions on the same calendar day for the same amount and identical generic narration (e.g. two separate ₹500 cash deposits on the same date) will share a fallback hash. The first transaction is imported, and subsequent occurrences within the same batch or across batches are treated as duplicates. Where running balances are present in the statement, running balance disambiguation prevents this collision.
2. **Credit vs Debit Ingestion**:
   Only incoming CREDIT transactions represent incoming receivables and instantiate `Payment` records. DEBIT transactions are retained in `bank_transactions` for cashflow inspection and statement balance auditability.

---

## 6. Verification Evidence

```text
======================= 157 passed, 1 warning in 33.45s =======================
Coverage: 93% across 3,590 statements (268 missed)
```

**Phase 13 is officially AUDITED, VERIFIED, and PERMANENTLY FROZEN.**
