# Roadmap Tracking — AI Invoice Reconciliation Platform

Living status of all phases defined in `phases.md`.

---

## Authoritative Phase Roadmap
See [`docs/roadmap/phase-definitions.md`](file:///h:/AI%20Invoice%20Reconciliation%20Platform/docs/roadmap/phase-definitions.md) for full phase definitions.

### Phase 8 — Backend Architecture & Project Foundation [VERIFIED & FROZEN]
- [x] Python 3.11 environment setup (`backend/.venv`)
- [x] Pyproject / dependency specification (`pyproject.toml`, `requirements.txt`)
- [x] Clean modular monolith directory structure (`backend/app/modules/...`)
- [x] Core configuration with Pydantic v2 Settings (`app/config.py`)
- [x] Production security gate preventing default JWT secrets in production
- [x] Structured JSON logging with sensitive credential redaction (`app/core/logging.py`)
- [x] Database engine, connection pooling & session management (`app/core/database.py`)
- [x] Alembic migration configuration (`alembic.ini`, `alembic/env.py`)
- [x] Exception taxonomy & HTTP error handling (`app/shared/exceptions.py`, `app/shared/result.py`)
- [x] Money value object with exact Decimal precision, hashability, & float rejection (`app/shared/domain/money.py`)
- [x] Health checks (`/health`, `/health/ready`, `/health/live` with graceful DB degradation)
- [x] API v1 router aggregation for all 9 modules (`app/api/v1/router.py`)
- [x] 32 automated unit & integration tests passing with 89% coverage
- [x] Phase 8 Verification & Audit completed (`docs/phase-history/phase-08-foundation.md`)

### Phase 9 — Authentication & Company Workspace [VERIFIED & FROZEN]
- [x] User and Company domain entities (`app/modules/auth/domain/`, `app/modules/company/domain/`)
- [x] Role-Based Access Control (`OWNER`, `ACCOUNTANT`)
- [x] SQLAlchemy 2.0 ORM models for `companies`, `users`, `user_invitations`, `password_reset_tokens`
- [x] Tenant-scoped repositories with strict isolation checks (`UserRepository`, `CompanyRepository`, etc.)
- [x] Initial Alembic migration script (`0001_phase_09_auth_and_company.py`)
- [x] Registration use case with atomic Company & Owner creation (`POST /api/v1/auth/register`)
- [x] Login & token lifecycle with bcrypt and JWT access/refresh tokens (`POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`)
- [x] Current user and company profile lookup (`GET /api/v1/auth/me`, `GET /api/v1/company/current`)
- [x] Password reset request & confirm workflow with SHA-256 token hashing
- [x] Owner-only user invitation and acceptance workflow (`POST /api/v1/company/users/invite`, `POST /api/v1/auth/invitations/accept`)
- [x] 57 automated unit, integration, and multi-tenant security tests passing with 92% coverage
- [x] Phase 9 Verification & Audit completed (`docs/phase-history/phase-09-authentication-and-company-workspace.md`)

### Phase 10 — Customer Management [VERIFIED & FROZEN]
- [x] Customer, CustomerAlias, CustomerPaymentIdentifier domain entities (`app/modules/customer/domain/`)
- [x] Identifier types (`BANK_ACCOUNT`, `VIRTUAL_ACCOUNT`, `UPI_VPA`) for reconciliation matching
- [x] SQLAlchemy 2.0 ORM models with composite unique constraints (`uq_customers_company_name`, `uq_customer_aliases_company_alias`, `uq_customer_identifiers_company_type_val`)
- [x] Tenant-scoped repositories (`CustomerRepository`, `CustomerAliasRepository`, `CustomerPaymentIdentifierRepository`)
- [x] Multi-signal customer search (`search_customers`) matching name, tax ID (GSTIN), email, phone, aliases, and payment coordinates
- [x] Application use cases for CRUD, archiving, alias management, payment identifier management, and multi-signal search
- [x] REST API endpoints (`/api/v1/customers`, `/search`, `/{id}`, `/archive`, `/unarchive`, `/aliases`, `/identifiers`)
- [x] Alembic migration script (`0002_phase_10_customers.py`)
- [x] 75 automated unit, integration, and IDOR tenant security tests passing with 94% coverage
- [x] Phase 10 Verification & Audit completed (`docs/phase-history/phase-10-customer-management.md`)

### Phase 11 — Invoice Management [VERIFIED & FROZEN]
- [x] Invoice, InvoiceDocument domain entities, enums (`InvoiceStatus`, `InvoiceSource`, `OCRStatus`), and balance conservation invariants (`app/modules/invoice/domain/`)
- [x] Non-destructive invoice archiving & unarchiving lifecycle preserving audit history while excluding from operational defaults (`is_archived: bool`)
- [x] Abstract `StorageService` port and secure `LocalStorageService` with SHA-256 byte hashing and directory containment (`app/modules/invoice/application/ports.py`)
- [x] Abstract `InvoiceOCRProvider` port and rule-based heuristic extraction provider (`HeuristicInvoiceOCRProvider`)
- [x] SQLAlchemy 2.0 ORM models for `invoices` and `invoice_documents` with composite uniqueness, check constraints, and `is_archived` indexing (`app/modules/invoice/infrastructure/models.py`)
- [x] Tenant-scoped repositories (`InvoiceRepository`, `InvoiceDocumentRepository`)
- [x] Alembic migration scripts: `0003_phase_11_invoices.py` (base tables) and `0004_phase_11_invoice_archive.py` (append-only archive column & index)
- [x] Application use cases for manual creation, document upload with draft creation, draft confirmation, CSV bulk import, archive, unarchive, filters, search, cancellation, deletion, and document streaming
- [x] REST API endpoints under `/api/v1/invoices` conforming to standard JSON data/error envelope
- [x] 105 automated unit, integration, and IDOR tenant security tests passing with 94% overall platform coverage (100% domain entities, 100% models, 100% schemas)
- [x] Phase 11 Forensic Freeze Gate completed (`docs/phase-history/phase-11-invoice-management.md`)

### Phase 12 — Payment & Bank Statement Management [VERIFIED & FROZEN]
- [x] Strict domain boundary enforced: Raw Bank Transaction (credit/debit) != Payment (credit only) != Reconciliation Decision
- [x] Domain entities: `ImportBatch`, `BankTransaction`, `Payment`, `TransactionFingerprint` (`app/modules/payment/domain/`)
- [x] Exact balance conservation invariant: `allocated_amount + unallocated_amount == amount`
- [x] Multi-layout CSV parser supporting Layout A (Credit/Debit), Layout B (Amount + Dr/Cr), Layout C (Signed Amount) (`CSVBankStatementParser`)
- [x] Multi-format date parsing, Indian/Western comma handling, UTR extraction, and CWE-1236 formula injection defense
- [x] 3 Deduplication Gates: File SHA-256 hash conflict, Intra-batch duplicate detection, Database idempotency
- [x] Savepoint row isolation (`with db.begin_nested():`) preventing session abortion on single row errors
- [x] SQLAlchemy 2.0 ORM models for `import_batches`, `bank_transactions`, and `payments` with check constraints and indices
- [x] Tenant-scoped repositories (`ImportBatchRepository`, `BankTransactionRepository`, `PaymentRepository`)
- [x] Alembic migration script (`0005_phase_12_payments.py`) with `down_revision = "0004_phase_11_archive"`
- [x] Application use cases for statement upload, batch tracking, raw transactions list, payments list/filter, manual payment, ignore/unignore
- [x] REST API endpoints under `/api/v1/payments` with masked bank accounts (`••••••••1234`) and fail-closed 404
- [x] 147 automated unit, integration, and IDOR tenant security tests passing with 93% total platform coverage
- [x] Independent Security & Code Review Audit passed with 100% compliance (`docs/phase-history/phase-12-payment-ingestion.md`)

---

## Disambiguation Rules:
1. **Reconciliation is Phase 14**, NOT Phase 8. Phase 8 is strictly the Engineering Foundation.
2. **`match_score`**: Deterministic heuristic evidence score (0–100), not a statistical probability.
3. **`AUTO_ELIGIBLE`**: High evidence score qualification for expedited human review. NOT `AUTO_APPLIED`.
4. **Combinatorial Search Bounds**: Initial engineering policies subject to empirical benchmark validation.

---

## Up Next:
- **Phase 13**: Advanced OCR & Multi-Vendor Processing (Cloud provider adapters, table extraction).
- **Phase 14**: Reconciliation Engine (Deterministic matching, scoring, evidence generation).
- **Phase 15**: Review Center (Interactive human review, approval/rejection workflows).
