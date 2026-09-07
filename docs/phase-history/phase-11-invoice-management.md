# Phase 11 History — Invoice Management

**Phase:** Phase 11  
**Status:** COMPLETE & FROZEN  
**Date:** 2026-09-06  

---

## 1. Objective
Establish the authoritative **Invoice Management** capability for the AI Invoice Reconciliation Platform. Invoices serve as the financial source documents and ground truth for unpaid balances in the reconciliation lifecycle. This phase delivers manual entry, PDF document upload with an isolated OCR extraction provider boundary, draft invoice confirmation, and bulk CSV ingestion with row-level error reporting, comprehensive lifecycle state transitions (`DRAFT`, `PENDING`, `PARTIALLY_PAID`, `PAID`, `CANCELLED`), balance conservation guarantees, tenant-scoped storage, and strict multi-tenant isolation.

This module intentionally avoids accounting/ERP bloat (general ledger, payroll, inventory, tax filing) and focuses strictly on maintaining reliable outstanding invoice balances for downstream reconciliation (Phase 14).

---

## 2. Scope & Implementation Delivered

### 2.1 Domain Layer (`app/modules/invoice/domain/`)
1. **Entities & Value Objects**:
   - `InvoiceStatus`: Lifecycle state machine (`DRAFT`, `PENDING`, `PARTIALLY_PAID`, `PAID`, `CANCELLED`).
   - `InvoiceSource`: Source ingestion provenance (`MANUAL`, `PDF_UPLOAD`, `CSV_IMPORT`, `ACCOUNTING_SYNC`).
   - `OCRStatus`: Document processing states (`PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`).
   - `InvoiceDocument`: Source file entity capturing `storage_path`, `file_name`, `mime_type`, `file_size_bytes`, `file_hash` (SHA-256), `ocr_status`, and `ocr_extracted_data`.
    - `Invoice`: Aggregate root managing financial invariants, balance conservation, and lifecycle transitions:
      - `publish_draft(invoice_number, customer_id, issue_date, due_date, total_amount)`
      - `record_payment(payment_amount)`
      - `cancel(reason)`
      - `archive()` & `unarchive()`
      - `update_metadata(notes, due_date)`
2. **Financial Invariants & Balance Conservation**:
   - Total amount must be strictly positive (`Money > 0`).
   - `paid_amount` initialized to zero and cannot be negative.
   - `paid_amount + outstanding_amount == total_amount` enforced at all times.
   - Due date cannot precede issue date.
   - Currencies must match on all financial mutations.
   - Closed or inactive invoices can be non-destructively archived (`is_archived: bool = True`) to isolate them from default operational queries while maintaining complete financial audit history.

### 2.2 Application Ports & Infrastructure Services (`app/modules/invoice/application/ports.py`)
1. **`StorageService` Boundary & `LocalStorageService`**:
   - Abstract port isolating file storage from disk/cloud mechanics.
   - Secure local storage implementation:
     - Tenant-isolated folder hierarchy (`storage/invoices/<company_id>/`).
     - SHA-256 file content hashing for integrity and byte-level duplicate prevention.
     - Path traversal safeguards preventing directory escapes.
2. **`InvoiceOCRProvider` Boundary & `HeuristicInvoiceOCRProvider`**:
   - Abstract port separating OCR parsing from core domain logic.
   - Deterministic rule-based heuristic implementation parsing invoice numbers, issue/due dates, monetary amounts, customer names, and tax IDs (GSTIN).
   - Ambiguity detection and confidence scoring; OCR output is strictly treated as untrusted external data and requires accountant confirmation. Real multi-engine computer vision OCR is isolated to downstream Phase 13.

### 2.3 Persistence & Database Architecture (`app/modules/invoice/infrastructure/`)
1. **SQLAlchemy 2.0 ORM Models**:
   - `InvoiceDocumentModel`: Table `invoice_documents` with tenant foreign key, `file_hash`, and indexed lookup.
   - `InvoiceModel`: Table `invoices` with composite unique constraint `uq_invoices_company_number (company_id, invoice_number)`, check constraints for balance conservation, indexed `is_archived` status, and foreign keys to `companies`, `customers`, and `invoice_documents`.
2. **Tenant-Scoped Repositories**:
   - `InvoiceRepository`: Tenant-scoped CRUD, listing with filters (status, customer, date range, outstanding balances, `is_archived`), and multi-signal search. Defaults to active records (`is_archived=False`).
   - `InvoiceDocumentRepository`: Tenant-scoped document persistence, retrieval, and SHA-256 duplicate lookup.
3. **Database Migrations**:
   - Alembic revision `0003_phase_11_invoices.py`: Registers base `invoice_documents` and `invoices` tables, foreign keys, check constraints (`total > 0`, `paid >= 0`, `outstanding >= 0`, `paid + outstanding = total`), unique constraint `uq_invoices_company_number`, and operational indexes (`status`, `customer`, `dates`).
   - Alembic revision `0004_phase_11_invoice_archive.py`: Adds `is_archived` column (`Boolean`, `server_default=false`, `nullable=False`) and composite index `idx_invoices_company_archived` on `(company_id, is_archived)` in an append-only migration, preserving migration immutability across fresh and pre-existing databases.

### 2.4 Application Use Cases (`app/modules/invoice/application/use_cases.py`)
1. `CreateInvoiceUseCase`: Manual operational invoice creation directly to `PENDING`.
2. `UploadInvoiceDocumentUseCase`: Validates file size and type, saves file to tenant storage, executes OCR extraction, and creates a `DRAFT` invoice candidate.
3. `ConfirmDraftInvoiceUseCase`: Human-in-the-loop review promoting `DRAFT` to `PENDING`, converting currencies safely across all money fields.
4. `ImportInvoicesCSVUseCase`: High-throughput ingestion of CSV rows with row-level validation, nested savepoint isolation, and atomic error reporting.
5. `ArchiveInvoiceUseCase`: Non-destructive archiving of non-draft invoices.
6. `UnarchiveInvoiceUseCase`: Restores archived invoices to operational queries.
7. `GetInvoiceDetailUseCase`: Detailed view joining customer and document metadata.
8. `ListInvoicesUseCase`: Filterable, paginated listing of invoices with operational vs archived partitioning (`is_archived=False` by default).
9. `SearchInvoicesUseCase`: Multi-field search querying invoice number, notes, and customer name.
10. `UpdateInvoiceUseCase`: Modifies non-authoritative metadata on unpaid/unfinalized invoices.
11. `CancelInvoiceUseCase`: Safely transitions unpaid invoices to `CANCELLED`.
12. `DeleteInvoiceUseCase`: Prevents deletion of paid/partially paid invoices (`paid_amount > 0`).
13. `GetInvoiceDocumentFileUseCase`: Authenticated file streaming with tenant access validation.

### 2.5 Presentation Layer & REST Endpoints (`app/modules/invoice/presentation/`)
- `POST /api/v1/invoices` (201 Created) — Create manual invoice.
- `GET /api/v1/invoices` (200 OK) — Paginated invoice listing with filters (`is_archived=False` by default).
- `GET /api/v1/invoices/search?q={query}` (200 OK) — Multi-signal invoice search.
- `POST /api/v1/invoices/upload` (201 Created) — Upload PDF/image document with OCR extraction.
- `POST /api/v1/invoices/{invoice_id}/confirm-draft` (200 OK) — Confirm draft invoice.
- `POST /api/v1/invoices/{invoice_id}/archive` (200 OK) — Non-destructive archive.
- `POST /api/v1/invoices/{invoice_id}/unarchive` (200 OK) — Restore from archive.
- `POST /api/v1/invoices/import-csv` (200 OK) — Bulk CSV import with row-level error reporting.
- `GET /api/v1/invoices/{invoice_id}` (200 OK) — Invoice detail.
- `PUT /api/v1/invoices/{invoice_id}` (200 OK) — Update metadata.
- `POST /api/v1/invoices/{invoice_id}/cancel` (200 OK) — Cancel invoice.
- `DELETE /api/v1/invoices/{invoice_id}` (200 OK) — Delete draft or unpaid invoice.
- `GET /api/v1/invoices/{invoice_id}/document` (200 OK) — Secure file stream.
- `GET /api/v1/invoices/status` (200 OK) — Module health probe.

---

## 3. Verification & Test Evidence

### 3.1 Automated Test Execution
Full test suite executed with zero failures:
```text
pytest tests -v --cov=app --cov-report=term-missing
====================== 105 passed, 1 warning in 30.86s =======================
```

### 3.2 Coverage Breakdown
- `app/modules/invoice/domain/entities.py`: **100%** (133/133 statements)
- `app/modules/invoice/infrastructure/models.py`: **100%** (44/44 statements)
- `app/modules/invoice/presentation/schemas.py`: **100%** (87/87 statements)
- `app/modules/invoice/presentation/router.py`: **98%** (107/109 statements)
- `app/modules/invoice/application/ports.py`: **92%** (141/154 statements)
- `app/modules/invoice/application/use_cases.py`: **89%** (310/350 statements)
- `app/modules/invoice/infrastructure/repositories.py`: **86%** (102/118 statements)
- **Overall Platform Coverage:** **94%** (2362/2526 statements)

### 3.3 Test Categories
- **Unit Tests (`tests/unit/test_invoice_unit.py`) — 15 Tests**:
  - Invariant validation: zero total rejection, negative total rejection, negative paid rejection, negative outstanding rejection, due date bounds.
  - Mathematical balance conservation (`paid + outstanding = total`).
  - Draft publishing and payment allocation state transitions.
  - Currency mismatch protection, overpayment prevention, and non-positive allocation rejection.
  - Cancellation safeguards on unpaid, partially paid, and paid invoices.
  - Archive and unarchive lifecycle transitions.
  - Local storage path traversal, file size limits (>10MB), and SHA-256 byte hashing.
  - Heuristic OCR parsing, USD/EUR currency extraction, DD-MM-YYYY dates, and ambiguity confidence scoring.
- **Integration Tests (`tests/integration/test_invoice_integration.py`) — 11 Tests**:
  - Manual creation end-to-end.
  - Intra-company duplicate invoice number rejection (409 Conflict).
  - Non-existent or archived customer validation.
  - PDF document upload, OCR extraction, and draft confirmation.
  - Archive and unarchive workflow (verifying operational exclusion).
  - File upload edge cases (empty files, invalid extensions, duplicate file deduplication).
  - Confirm draft edge cases (non-draft 422, duplicate invoice number conflict 409).
  - Bulk CSV import with row-level validation and error reporting.
  - CSV validation failures (empty CSV, missing headers, missing customer reference).
  - Deletion guards (paid/partially paid rejection) and document retrieval errors (missing document 404, non-existent invoice 404).
  - Listing filters, pagination, and multi-field search.
  - Invoice detail and secure document streaming.
- **Security & Multi-Tenant Isolation (`tests/integration/test_invoice_tenant_security.py`) — 2 Tests**:
  - Strict tenant scoping on all queries, commands, archive, and unarchive operations.
  - Cross-tenant IDOR attack rejection (404 Not Found).
  - Unauthenticated access rejection (401 Unauthorized).
  - Cross-company invoice number coexistence permitted.

---

## 4. Forensic Freeze Gate Findings & Verification

1. **Migration History**:
   - `0003_phase_11_invoices.py` was restored to its immutable original state (creating base tables without `is_archived`).
   - `0004_phase_11_invoice_archive.py` was created to add `is_archived` and its index cleanly.
   - Tested Alembic upgrade and downgrade chain from `0001 -> 0002 -> 0003 -> 0004 -> 0003 -> base -> head`: verified 100% deterministic on both pre-existing and fresh databases.
2. **Archive Semantics & State-Transition Matrix**:
   - `DRAFT`: cannot be archived (`DomainError`). Must be confirmed or deleted.
   - `PENDING`, `PARTIALLY_PAID`, `PAID`, `CANCELLED`: can be archived (`is_archived = True`) and unarchived (`is_archived = False`).
   - Archiving preserves exact balances, documents, and audit logs. It removes records from default operational views (`is_archived=False`), ensuring closed invoices do not pollute daily reconciliation candidate searches.
3. **Delete vs Cancel vs Archive Boundary**:
   - `DELETE`: restricted to unallocated `DRAFT`, `PENDING`, or `CANCELLED` invoices where `paid_amount == 0`. Invoices with recorded payments can NEVER be deleted, protecting financial audit integrity.
   - `CANCEL`: marks unpaid `PENDING` invoices as `CANCELLED`, preserving historical invoice numbers and audit trails. Invoices with recorded payments can NEVER be cancelled.
   - `ARCHIVE`: non-destructive visibility toggle (`is_archived = True`) for inactive/closed invoices.
4. **Payment Boundary**:
   - `Invoice.record_payment()` is strictly a domain invariant operation (checks status, positive amount, currency match, overpayment bound, balance conservation). It contains zero matching, candidate generation, or bank processing logic.
5. **Concurrency & Stale State**:
   - `InvoiceModel` maintains `updated_at`. `version_id` does not exist; mathematical over-allocation is prevented at the database level by check constraint `chk_invoice_outstanding_non_negative`.
6. **OCR Boundary**:
   - `HeuristicInvoiceOCRProvider` is a deterministic text/regex parser. Untrusted OCR extractions ALWAYS produce `DRAFT` invoices and cannot bypass human verification.
7. **CSV Transaction Semantics**:
   - Implemented with row-by-row validation and nested transaction savepoints (`with self.db.begin_nested():`). A failing row reports a granular error without corrupting the session or aborting subsequent valid rows.

---

## 5. Architectural Alignment & Downstream Handover
- **Phase 9 (Auth & Workspace)**: Integrated via `get_current_user` and `company_id` scoping.
- **Phase 10 (Customer Management)**: Validates customer existence and active status before invoice attachment.
- **Phase 12 (Payment Ingestion)**: Invoices are ready to receive candidate matches and deterministic payment allocations.
- **Phase 14 (Reconciliation Engine)**: Invoices provide authoritative outstanding balances, customer IDs, and strict state transitions for candidate generation and payment allocation.
