# Architectural Decision Ledger (ADR) — AI Invoice Reconciliation Platform

This ledger documents the frozen architectural decisions made for the platform.

---

## ADR-001: Architecture Style — Modular Monolith over Microservices
- **Date**: 2026-09-05
- **Status**: Accepted / Frozen
- **Context**: The product is an MVP moving toward commercial SaaS. Team and operational overhead must be minimized while preserving clean boundaries.
- **Decision**: Build as a single deployable application structured into strictly decoupled modules (`auth`, `company`, `customer`, `invoice`, `payment`, `reconciliation`, `review`, `dashboard`, `audit`). Inter-module communication is mediated through application/domain service interfaces, never by raw cross-module database foreign-key mutations.
- **Consequences**: Fast development and zero distributed transaction complexity now, with clean extraction paths if individual modules require independent scaling in the future.

---

## ADR-002: Deterministic Reconciliation vs LLM Boundary
- **Date**: 2026-09-05
- **Status**: Accepted / Frozen
- **Context**: Financial reconciliation requires 100% auditable, reproducible, and mathematically exact decisions.
- **Decision**: Matching algorithms, candidate generation, evidence collection, and confidence scoring are 100% deterministic rules/algorithms written in Python. LLMs are strictly isolated behind provider adapters and may only be used to parse unstructured narration/emails and to generate natural-language explanations of already-computed evidence objects.
- **Consequences**: Complete protection against LLM hallucination and financial state corruption. System remains operable even if external LLM APIs fail.

---

## ADR-003: Monetary Precision & Zero Float Rule
- **Date**: 2026-09-05
- **Status**: Accepted / Frozen
- **Context**: IEEE-754 floating point arithmetic causes silent precision errors (e.g. `0.1 + 0.2 != 0.3`).
- **Decision**: All financial amounts in Python domain entities and value objects must use `decimal.Decimal` with explicit rounding. In PostgreSQL, all monetary columns are defined as `NUMERIC(14, 2)`.
- **Consequences**: Exact balance calculations, zero round-off leakage.

---

## ADR-004: Multi-Tenant Scoping at Repository Layer
- **Date**: 2026-09-05
- **Status**: Accepted / Frozen
- **Context**: Cross-tenant data leakage in a financial SaaS is a fatal compliance failure.
- **Decision**: Every business table includes `company_id`. The authenticated `company_id` is derived strictly from server-side validated JWT credentials. Every repository query explicitly includes `company_id` in `WHERE` clauses. Client-provided `company_id` is never trusted.
- **Consequences**: Defense in depth against IDOR and cross-tenant access.

---

## ADR-005: Technology Stack Choices
- **Date**: 2026-09-05
- **Status**: Accepted / Frozen
- **Backend**: Python 3.11, FastAPI, SQLAlchemy 2.0 (async/sync), Alembic, Pydantic v2.
- **Database**: PostgreSQL 16 (authoritative transactional datastore).
- **Cache**: Redis (non-authoritative session, rate limiting, and temporary job state).
- **Storage**: S3-compatible object storage for invoice PDFs.
- **Frontend**: Next.js 14+ App Router, TypeScript, Tailwind CSS.

---

## ADR-006: Multi-Tenant JWT Claims & Authentication Hierarchy
- **Date**: 2026-09-05
- **Status**: Accepted / Frozen
- **Context**: Authentication tokens must securely bind user identity to their authenticated company workspace and permissions without trusting client-supplied parameters.
- **Decision**: JWT access tokens embed authoritative claims: `sub` (user UUID), `company_id` (tenant UUID), `role` (`OWNER` or `ACCOUNTANT`), and `email`. The FastAPI dependency `get_current_user` extracts and cryptographically validates the token, then re-verifies the user's active state in the database within that specific company. A separate `require_role(role)` dependency enforces RBAC (e.g., only `OWNER` can invite team members). Refresh tokens have extended 7-day validity and are typed (`type: "refresh"`). Password reset tokens are stored as SHA-256 hashes with 1-hour expiry.
- **Consequences**: Cryptographically robust tenant containment, zero client trust, immediate revocation upon account deactivation, and clean RBAC enforcement across all downstream business modules.

---

## ADR-007: Customer Identity, Aliases, and Payment Identifier Storage
- **Date**: 2026-09-06
- **Status**: Accepted / Frozen
- **Context**: In financial reconciliation, bank statement narrations rarely contain the exact legal entity name matching accounting records. They frequently contain abbreviated trade names ("Acme Ltd" vs "Acme Corp"), bank account numbers, virtual accounts, or UPI Virtual Payment Addresses (VPAs). The customer identity layer must serve downstream reconciliation by resolving these multi-signal tokens to a single canonical customer counterparty without morphing into a full-blown CRM.
- **Decision**:
  1. Maintain a clean, minimalist `customers` table scoped by `(company_id, name)` as unique master data.
  2. Implement a dedicated `customer_aliases` table scoped by `(company_id, alias_name)` with foreign key cascade to `customers`.
  3. Implement a dedicated `customer_payment_identifiers` table scoped by `(company_id, identifier_type, identifier_value)` storing explicit payment coordinates (`BANK_ACCOUNT`, `VIRTUAL_ACCOUNT`, `UPI_VPA`).
  4. Provide a high-performance multi-signal search (`search_customers`) that combines canonical name, tax ID (GSTIN), email, phone, aliases, and payment identifiers via indexed SQL queries.
  5. Prohibit hard deletion when historical records are present; support non-destructive archiving.
- **Consequences**: The downstream reconciliation engine (Phase 14) can perform instant O(1) or indexed deterministic payer resolution directly against known bank account numbers, virtual accounts, UPI VPAs, and trade names, with zero external vector databases or heavyweight CRM dependencies.

---

## ADR-008: Invoice Lifecycle, Balance Invariants, Storage, and OCR Provider Abstraction
- **Date**: 2026-09-06
- **Status**: Accepted / Frozen
- **Context**: Invoices serve as the authoritative financial counterpart against which bank statement payments are matched. They must uphold strict balance conservation, support multiple ingestion modalities (manual, PDF upload with OCR extraction, bulk CSV), and isolate external file storage and OCR vendors behind clean ports. Invoices must also strictly avoid ERP/accounting bloat (general ledger, payroll, tax filing).
- **Decision**:
  1. **Lifecycle State Machine**: Implement strict states: `DRAFT`, `PENDING`, `PARTIALLY_PAID`, `PAID`, and `CANCELLED`. Invoices created from document uploads begin as `DRAFT` and require accountant confirmation before becoming operational `PENDING` invoices.
  2. **Mathematical Balance Invariants**: Total amounts must be positive (`> 0`). At all times, the domain and database check constraints enforce `paid_amount + outstanding_amount == total_amount`. The `paid_amount` starts at 0 and cannot decrease below 0.
  3. **Storage Port**: Isolate file persistence behind `StorageService` (ABC). The default `LocalStorageService` stores files in tenant-scoped paths (`storage/invoices/<company_id>/`), computes SHA-256 hashes for deduplication and integrity, and defends against directory traversal attacks.
  4. **OCR Provider Boundary**: Isolate OCR parsing behind `InvoiceOCRProvider` (ABC). OCR extractions are treated as untrusted external suggestions, requiring user confirmation before committing to an active invoice. The default `HeuristicInvoiceOCRProvider` performs deterministic regex and token extraction with ambiguity scoring.
  5. **Bulk Ingestion Resilience**: Bulk CSV imports execute row-level validation and atomic persistence per valid row, returning a structured summary of successes and granular row-by-row error details.
  6. **Non-Destructive Archiving**: Distinguish clearly between deletion, cancellation, and archiving. Deletion is strictly guarded (prohibited once payments have been recorded). Cancellation transitions an invoice to `CANCELLED` (halting future allocations). Archiving (`is_archived: bool = False`) removes closed or inactive invoices from operational default views while preserving complete balance, document, and allocation history for compliance, audits, and reporting. Invoices can be unarchived at any time.
  7. **Migration Immutability**: Base invoice tables, check constraints, and operational indexes are established in immutable migration `0003_phase_11_invoices.py`, while the non-destructive archiving column and composite index are applied via append-only migration `0004_phase_11_invoice_archive.py`. This guarantees deterministic execution across fresh databases and pre-existing Phase 11 instances.
- **Consequences**: Uncompromised financial integrity, zero floating-point errors, pluggable storage and OCR providers, resilient human-in-the-loop validation, complete audit preservation via archiving, immutable migration history, and clean data ready for Phase 12 & Phase 14.

---

## ADR-009: Raw Bank Transaction vs Payment Candidate Segregation & 3-Gate Deduplication
- **Date**: 2026-09-06
- **Status**: Accepted / Frozen
- **Context**: In accounts receivable reconciliation, bank statements contain both Credits (inflows/deposits) and Debits (outflows/fees/refunds), formatted across disparate layouts (HDFC, ICICI, SBI, Axis, HSBC). Naively treating all rows as receivable payments or coupling statement ingestion with invoice matching leads to corrupted ledger states and fragile imports.
- **Decision**:
  1. **Strict Tripartite Domain Boundary**:
     - `Raw Bank Transaction` (`bank_transactions` table): Immutable source truth recording all statement line items (both `CREDIT` and `DEBIT`), raw row JSON, running balance, and account metadata.
     - `Payment` (`payments` table): Normalized incoming receivable candidate created strictly from `CREDIT` transactions (or manual offline entry). Debits are recorded in `bank_transactions` for audit and cashflow completeness, but strictly excluded from `payments`.
     - `Reconciliation Decision`: Strictly deferred to Phase 14/15.
  2. **Mathematical Balance Invariants**:
     - At all times: `allocated_amount + unallocated_amount == amount`.
     - Non-negativity constraints: `amount > 0`, `allocated_amount >= 0`, `unallocated_amount >= 0`.
  3. **3-Gate Deduplication Engine**:
     - *Gate 1 (File Level)*: Content SHA-256 hash check against `import_batches` prevents accidental duplicate statement imports (HTTP 409 Conflict). Failed imports (`status=FAILED`) can be cleanly re-uploaded and retried.
     - *Gate 2 (Intra-Batch)*: Memory-tracked composite hash skips repeated identical rows within the same CSV file.
     - *Gate 3 (Database Level)*: Idempotency check via deterministic fingerprint (`TransactionFingerprint`) and DB constraints skips already-imported transactions across overlapping statement periods. Primary fingerprints are scoped by bank account and ignore generic placeholder references (`NA`, `0`, `NONE`). Fallback fingerprints disambiguate same-day transactions using statement running balances.
  4. **Savepoint Row Isolation**:
     - Ingestion persists rows using nested SQLAlchemy savepoints (`with db.begin_nested():`) so a single constraint failure rolls back only the failing row without aborting the outer transaction or discarding valid rows.
  5. **Security & Input Sanitization**:
     - Strict 10MB limit and `.csv`/`.txt` extension whitelist.
     - Spreadsheet Formula Injection Defense (CWE-1236): Prepend `'` to text fields beginning with `=`, `+`, `-`, `@`, `\t`, `\r`. Numeric fields strictly validate numeric syntax and reject formula prefixes.
     - Banking coordinates (bank accounts, UPI IDs) are masked (`••••••••1234`) in API responses and sanitized in structured logging.
  6. **Append-Only Migration**:
     - Tables `import_batches`, `bank_transactions`, and `payments` are established via append-only migration `0005_phase_12_payments.py` chained from `0004_phase_11_archive`.
- **Consequences**: Rock-solid financial invariant guarantees, zero credit/debit pollution, partial import resilience, automated deduplication across overlapping files, complete multi-tenant IDOR protection, verified against 10 special forensic tests (157 total tests passed, 93% platform coverage), and a clean ingestion substrate for Phase 14 Reconciliation Engine.
