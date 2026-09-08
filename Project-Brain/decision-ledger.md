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

---

## ADR-010: Counterparty Payer Identification Architecture & Deterministic Rule Waterfall
- **Date**: 2026-09-07
- **Status**: Accepted / Frozen
- **Context**: The reconciliation engine must accurately identify the customer context of incoming bank statement payments before candidate invoices can be fetched or matched. Bank statement narrations vary widely: direct accounts, UPI VPAs, abbreviations, corporate suffix variations, and shared accounts. The system must disambiguate counterparties with 100% auditable deterministic evidence, zero LLM guesswork, and zero mutation of financial state.
- **Decision**:
  1. **Strict Counterparty Scope**: Phase 13.1 implements only Counterparty Disambiguation / Customer Identification. Candidate invoice generation, combination searches, subset-sum matching, and payment allocations are strictly deferred to subsequent sub-phases.
  2. **Stateless & Recomputable**: Evaluation runs on-demand against active customer lookup contexts via ports and adapters without requiring premature reconciliation database tables or migrations.
  3. **Deterministic Rule Waterfall & Scoring**:
     - Direct Coordinate Matches (`EXACT_BANK_ACCOUNT`, `EXACT_UPI_VPA`, `EXACT_VIRTUAL_ACCOUNT`) yield weight 100.0.
     - Extracted Narration Coordinates yield weight 95.0.
     - Exact Registered Alias Matches yield weight 85.0.
     - Normalized Legal Name Matches (with standard corporate suffix stripping) yield weight 75.0 while strictly preventing false collapses (e.g., `Industries` != `Industrials`).
     - Clean Payer Name Exact Matches yield weight 70.0.
     - Reference / Tax ID Matches yield weight 50.0 (excluding placeholder tokens such as `NA`, `NONE`, `PENDING`).
     - Narration Token Overlap yields weight 20.0 to 45.0 (only as fallback when exact name/coordinate does not match).
  4. **Conflict & Ambiguity Detection**:
     - Conflicting direct coordinates across different candidates trigger `CONFLICTING`.
     - Direct coordinate on Customer A vs explicit name/alias on Customer B triggers `CONFLICTING` (Conflict 2: hardened for all scores $\ge 70.0$).
     - Shared bank accounts (two subsidiaries with the same account coordinate) trigger `AMBIGUOUS`.
     - Candidate score deltas $\Delta < 15.00$ or top score $< 70.00$ trigger `AMBIGUOUS`.
     - No evidence or score $< 30.00$ triggers `UNKNOWN`.
     - Non-positive payments trigger `NOT_ELIGIBLE`.
  5. **Archived Customer Exclusion**: Archived customers are strictly excluded from candidate consideration.
  6. **Zero Financial State Mutation Guarantees**: Customer identification does not mutate payment or invoice balances; session dirty checking confirms 0 modified, 0 new, and 0 deleted rows (**Financial State Mutation Risk: 0%**). Crucially, **Customer Identification Correctness Risk** remains non-zero: deterministic heuristics can still make incorrect counterparty interpretations when data is incomplete or ambiguous, mandating human review gates.
  7. **Multi-Tenancy & Data Protection**: `company_id` is enforced strictly from `current_user` in JWT with fail-closed HTTP 404 on IDOR attempts. Banking coordinates and UPI VPAs are masked (`********5544` and `jo******@icici`) in API responses.
  8. **Scalability Classification & Boundaries**: Categorized as **Category B: ACCEPTABLE WITH DOCUMENTED LIMITATION**. Certified for catalogs up to $C \le 1,000$ active customers per tenant where $O(P \times C)$ in-memory evaluation remains $\le 135\text{ ms}$. Future query and pre-filtering architectures for larger catalogs must be empirically proven in Phase 13.2+ based on concrete invoice matching requirements rather than assumed prematurely.
  9. **Documented Semantic Debt (Tax ID vs Reference Conflation)**: Matching a payment reference to a customer tax ID is temporarily grouped under `REFERENCE_MATCH` (weight 50.0). Phase 13.2+ must not build higher-order matching logic on this conflated assumption and must separate strong enterprise tax coordinates (e.g. GSTIN) from generic reference substrings.
- **Consequences**: Deterministic, explainable, and reproducible counterparty identification with 49 automated reconciliation tests (206 total platform tests passing, 93% platform coverage), establishing an audited, bounded substrate for Phase 13.2+ candidate invoice matching.

## ADR-011: Candidate Invoice Generation & Bounded Universe Architecture
- **Date**: 2026-09-07
- **Status**: Accepted / Implemented
- **Context**: Given an incoming payment and its Phase 13.1 counterparty customer identification result (or an explicit user override), the reconciliation engine must retrieve, rank, and prune open invoices belonging to that customer. Enterprise customers can accumulate hundreds of unpaid invoices. Unbounded subset-sum matching on hundreds of invoices causes combinatorial explosion ($O(2^N)$). Furthermore, candidate generation must not mutate financial balances, must not rely on LLM guessing, and must strictly isolate currency boundaries.
- **Decision**:
  1. **Strict Candidate Scope**: Phase 13.2 implements only Candidate Invoice Generation. Final invoice matching (1:1, 1:N, N:1), combination subset searches, and payment allocation are strictly deferred to Phase 13.3+.
  2. **Retrieval Priority vs Reconciliation Confidence Separation**: The `retrieval_priority` (0.0 to 100.0) computed in Phase 13.2 is an ordering heuristic solely for candidate presentation and universe bounding ($K \le 30$). It is explicitly **not** a reconciliation match confidence score.
  3. **Zero Financial State Mutation**: Evaluation is strictly read-only. Database session assertions confirm 0 modified, 0 new, and 0 deleted rows (Financial State Mutation Risk: 0.00%).
  4. **Dedicated Application Port (`InvoiceLookupPort`)**: Clean Architecture boundary prevents leaking ORM dependencies into the domain. Implemented via `SQLAlchemyInvoiceLookupAdapter` leveraging existing Phase 11 composite indexes (`idx_invoices_company_customer`, `idx_invoices_company_status`, `idx_invoices_company_archived`). Zero new database migrations required.
  5. **Deterministic Evidence Taxonomy**:
     - `INVOICE_NUMBER_MATCH` (+40.0, Strong): Clean alphanumeric regex token match against payment narration or payment reference.
     - `EXACT_AMOUNT_MATCH` (+35.0, Strong): Exact `Decimal` equality between payment amount and invoice outstanding balance.
     - `EXACT_ORIGINAL_AMOUNT_MATCH` (+25.0, Medium): Payment amount equals gross total on a previously partially paid invoice.
     - `PARTIAL_AMOUNT_COMPATIBLE` (+15.0, Medium): Payment amount is strictly less than invoice outstanding balance.
     - `DATE_RELEVANCE` (Up to +20.0): Temporal causality (`issue_date <= payment_date`, +10.0) plus due-date proximity ($\le 7$ days: +10.0, $\le 30$ days: +5.0, $> 30$ days: +2.0).
  6. **5-Key Deterministic Sorting**:
     Candidates are sorted by: (1) `retrieval_priority` DESC (with micro-aging tie-breaker $\min(0.99, \text{days\_overdue}/1000)$ favoring older unpaid debt FIFO), (2) `is_exact_amount_match` DESC, (3) `is_reference_match` DESC, (4) `due_date` ASC, (5) `invoice_id` ASC.
  7. **Strict Currency Invariants**:
     Invoices with mismatched currencies are excluded. If a customer has open invoices in another currency, `CURRENCY_MISMATCH` is returned with explicit truncation diagnostics.
  8. **Bounded Universe & Truncation Metadata**:
     Candidate lists are clamped to limit ($K=30$ default, max 100). Truncation metadata records `total_eligible_invoices`, `truncated: bool`, `candidate_limit: int`, and `truncation_reason`.
  9. **Fail-Closed IDOR Security**:
     `POST /api/v1/reconciliation/candidates/{payment_id}` enforces JWT company context; cross-tenant payment requests fail-closed with HTTP 404.
- **Consequences**: 25 new tests added (74 total reconciliation tests, 231 total platform tests passing, 93% platform coverage). Delivers a deterministic, bounded, and audited candidate pool ready for Phase 13.3 combinatorial matching.

