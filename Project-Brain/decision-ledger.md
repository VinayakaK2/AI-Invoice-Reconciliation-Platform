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

---

## ADR-012: Deterministic Candidate Filtering Pipeline & Fail-Closed Exclusion Taxonomy
- **Date**: 2026-09-08
- **Status**: Accepted / Frozen
- **Context**: Phase 14.3 Candidate Invoice Generation retrieves candidate invoices from a customer's open book. However, before matching algorithms can evaluate pairing, the raw candidate pool must be strictly pruned to eliminate non-viable candidates (e.g. cross-tenant bleed, archived customers, archived invoices, currency mismatches, invalid states, broken balance conservation, outside lookback window, or duplicates). Crucially, filtering must preserve candidates eligible for downstream partial matching (Phase 14.6) and multi-invoice matching (Phase 14.7) without premature truncation.
- **Decision**:
  1. **Strict 11-Gate Fail-Closed Pipeline**: Evaluates candidate invoices across 11 deterministic gates: (1) Tenant match, (2) Customer match, (3) Customer archival status, (4) Invoice archival status, (5) ISO currency match, (6) Operational invoice status (`PENDING` or `PARTIALLY_PAID`), (7) Balance conservation & non-negative balance, (8) Amount policy bounds, (9) Date temporal causality & lookback window, (10) Reference match constraints, and (11) Stream deduplication.
  2. **18-Code Reason Taxonomy**: Every excluded invoice is explicitly tracked with a reason code (`FilterExclusionReason`) and human-readable diagnostic message in `universe.excluded_candidates`.
  3. **Non-Interference with Partial and Multi-Invoice**: Underpayments ($\text{payment} < \text{outstanding}$) and overpayments ($\text{payment} > \text{outstanding}$) are preserved in `retained_candidates` for downstream phases.
  4. **5-Key Deterministic Sorting Prior to Bounding**: Sorted by `retrieval_priority` DESC, `is_exact_amount_match` DESC, `is_reference_match` DESC, `due_date` ASC, and `invoice_id` ASC. Permutation invariance verified across 100 random shuffles.
  5. **Zero Financial Mutation**: Strict read-only in-memory domain evaluation. Session dirty checking confirms `len(db.dirty) == 0`.
- **Consequences**: Rock-solid deterministic pruning substrate with 31 automated tests (348 total platform tests passing) feeding directly into Phase 14.5.

---

## ADR-013: Deterministic Exact Matching (1:1) Engine & Incomplete Universe Defense
- **Date**: 2026-09-10
- **Status**: Accepted / Frozen
- **Context**: The reconciliation engine requires a deterministic, 100% auditable 1:1 matching fact to decide whether an incoming bank statement payment matches exactly one open invoice from the filtered candidate universe. The engine must strictly compare against authoritative outstanding balances (not gross totals), prevent arbitrary tie-breaking when duplicate amount invoices exist, guard against false certainty when candidate universes are bounded/truncated, and maintain absolute zero financial mutation.
- **Decision**:
  1. **Strict Tripartite Matching Status**: The engine produces an objective matching fact: `EXACT_MATCH`, `NO_EXACT_MATCH`, or `AMBIGUOUS_EXACT_MATCH`.
  2. **Authoritative Balance Comparator**: Strict `Decimal` equality between `payment.effective_amount` and `candidate.outstanding_amount` under identical ISO currencies. Never compares against original invoice total when partial payments have already occurred ($P > 0$).
  3. **Zero Floating-Point & Zero FX Conversion**: Rejects binary floats and strictly refuses cross-currency evaluation.
  4. **Strict Ambiguity Preservation (Prohibition of Autonomous Disambiguation)**: In accordance with `business-rules.md` §2.5 line 64, if multiple candidates match the payment amount, the engine unconditionally emits `AMBIGUOUS_EXACT_MATCH` (`MULTIPLE_EXACT_AMOUNT_MATCHES`) with `matched_candidate = None` and records all matching candidates in `competing_candidates`. Autonomous reference tie-breaking is strictly prohibited. Reference match signals and evidence items are preserved on competing hypotheses for human review.
  5. **Incomplete Universe Defense**: If the candidate universe was truncated ($K \le 30$) and a single exact match candidate lacks an explicit invoice number reference match, the engine emits `AMBIGUOUS_EXACT_MATCH` (`TRUNCATED_UNIVERSE_AMBIGUITY`) because unretrieved invoices could share the same balance. The engine also scans `universe.excluded_candidates` for amount collisions under `TRUNCATED_BY_LIMIT`.
  6. **4-Key Deterministic Non-Scoring Comparator**: Hypotheses are sorted by `(not is_reference_match, date_difference_days, invoice_number, invoice_id)` ensuring strict 100-run permutation invariance without premature Phase 14.11 numeric scoring.
  7. **Separation of Concerns**: Matching Fact $\neq$ Scoring $\neq$ Confidence $\neq$ Approval $\neq$ Mutation. Composite heuristic scoring (`match_score`) was eliminated from Phase 14.5 and deferred to Phase 14.11. The engine never modifies database rows (`len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`).
  8. **Multi-Tenant Fail-Closed Security**: Endpoint `POST /api/v1/reconciliation/exact-match/{payment_id}` enforces JWT company context; cross-tenant payments or customer overrides return HTTP 404.
- **Consequences**: Certified deterministic 1:1 matching engine with 32 automated tests (388 total platform tests passing, 0 failures, 85.73s runtime), with B-01 (Premature Scoring) and B-02 (Autonomous Disambiguation) fully remediated, verified, audited, and frozen for Phase 14.5. Downstream Phase 14.6 (Partial Matching) and Phase 14.7 (Multi-Invoice Matching) will build upon this foundation.

---

## ADR-014: Deterministic Partial Payment Matching (1:1) Engine & Ambiguity Preservation
- **Date**: 2026-09-19
- **Status**: Accepted / Frozen
- **Context**: The reconciliation pipeline requires an objective, deterministic capability to evaluate whether an incoming payment represents a partial payment against a single open invoice ($0 < \text{payment.effective\_amount} < \text{candidate.outstanding\_amount}$). The engine must compute hypothetical applied and remaining amounts with exact Decimal precision, strictly decouple matching evaluation from financial mutation, prevent arbitrary candidate selection when multiple open invoices can accept the payment, and guard against incomplete universe truncation risk.
- **Decision**:
  1. **Strict Tripartite Matching Status**: The engine produces an objective matching fact: `PARTIAL_MATCH`, `NO_PARTIAL_MATCH`, or `AMBIGUOUS_PARTIAL_MATCH`.
  2. **Authoritative Balance Comparator**: Evaluates $0 < \text{payment.effective\_amount} < \text{candidate.outstanding\_amount}$ using strict `Decimal` arithmetic. Evaluates against `candidate.outstanding_amount`, never original total amount if prior partial payments exist.
  3. **Zero Financial Mutation**: Strictly in-memory evaluation producing matching hypotheses. No writes to invoices, payments, allocations, or ledgers. Verified via session dirty checking (`len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`).
  4. **Single-Invoice Scope Only**: Partial matching evaluates candidates individually ($1:1$). Multi-invoice combination matching ($\text{payment} = \text{INV}_1 + \text{INV}_2$) is strictly reserved for Phase 14.7.
  5. **Exact Match Non-Interference**: If $\text{payment} == \text{outstanding}$, returns `NO_PARTIAL_MATCH` with reason `EXACT_MATCH_DETECTED` (governed by Phase 14.5). If $\text{payment} > \text{outstanding}$, returns `NO_PARTIAL_MATCH` with reason `OVERPAYMENT_DETECTED` (deferred to Phase 14.7).
  6. **Strict Ambiguity Preservation**: When multiple candidate invoices have outstanding balance greater than the payment (`total_partial_found > 1`), the engine unconditionally emits `AMBIGUOUS_PARTIAL_MATCH` (`MULTIPLE_PARTIAL_CANDIDATES`) with `matched_candidate = None` and populates all hypotheses in `competing_candidates`. Autonomous reference tie-breaking is strictly prohibited.
  7. **Incomplete Universe Defense**: If the candidate universe was truncated ($K \le 30$) and a single partial candidate lacks an explicit reference match, emits `AMBIGUOUS_PARTIAL_MATCH` (`TRUNCATED_UNIVERSE_AMBIGUITY`). Excluded candidates are also scanned for partial eligibility under `TRUNCATED_BY_LIMIT`.
  8. **Deterministic Total Order**: Hypotheses are sorted by `(not is_reference_match, date_difference_days, invoice_number, invoice_id)` ensuring strict 100-run permutation invariance.
  9. **No Premature Scoring**: No composite `match_score` is computed; factual structured evidence signals are emitted only.
- **Consequences**: Certified deterministic 1:1 partial matching engine with 32 automated tests (420 total platform tests passing, 0 failures, 91.04s runtime), verified, audited, and frozen for Phase 14.6. Downstream Phase 14.7 (Multi-Invoice Matching) will build upon this foundation.

---

## ADR-015: Deterministic Multi-Invoice Matching (1:N) Engine & Combinatorial Safety
- **Date**: 2026-09-19
- **Status**: Accepted / Frozen
- **Context**: The reconciliation pipeline requires an objective, deterministic capability to evaluate whether an incoming payment represents an exact settlement for a combination of multiple open invoices ($\sum_{i=1}^k \text{candidate}_i\text{.outstanding\_amount} == \text{payment.effective\_amount}$, $2 \le k \le 4$). The engine must compute subset sums with exact Decimal precision, strictly decouple matching evaluation from financial mutation, prevent arbitrary combination selection when multiple distinct subsets sum to the payment, bound combinatorial search complexity, and guard against incomplete universe truncation risk.
- **Decision**:
  1. **Strict Tripartite Matching Status**: The engine produces an objective matching fact: `MULTI_INVOICE_MATCH`, `NO_MULTI_INVOICE_MATCH`, or `AMBIGUOUS_MULTI_INVOICE_MATCH`.
  2. **Authoritative Subset-Sum Comparator**: Evaluates $\sum_{i=1}^k \text{candidate}_i\text{.outstanding\_amount} == \text{payment.effective\_amount}$ where $2 \le k \le \text{max\_combination\_size}$ (default 4). Uses strict `Decimal` arithmetic, evaluating against `candidate.outstanding_amount`, never original total amounts if prior partial payments exist.
  3. **Zero Financial Mutation**: Strictly in-memory evaluation producing matching hypotheses. No writes to invoices, payments, allocations, or ledgers. Verified via session dirty checking (`len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`).
  4. **Multi-Invoice Scope Only (1:N, $k \ge 2$)**: Multi-invoice matching evaluates combinations of 2 or more candidates. Single-invoice exact matches ($k=1$) are diagnosed as `EXACT_MATCH_DETECTED` (governed by Phase 14.5). Single-invoice partial matches ($k=1$) are diagnosed as `PARTIAL_MATCH_DETECTED` (governed by Phase 14.6). Generalized combination matching with aging/FIFO heuristics belongs to Phase 14.8.
  5. **Combinatorial Complexity Bounding**: Enforces $2 \le k \le \text{max\_combination\_size} \le 10$ (default $k \le 4$) over the pre-filtered candidate universe ($n \le 30$). Total combinations checked is bounded to at most $\sum_{k=2}^4 \binom{30}{k} = 31,900$, executing in < 5ms.
  6. **Strict Ambiguity Preservation**: When multiple distinct combinations of candidate invoices sum to the payment amount (`total_combinations_found > 1`), the engine unconditionally emits `AMBIGUOUS_MULTI_INVOICE_MATCH` (`MULTIPLE_MULTI_INVOICE_MATCHES`) with `matched_combination = None` and populates all hypotheses in `competing_combinations`. Autonomous tie-breaking across combinations or combination sizes is strictly prohibited.
  7. **Incomplete Universe Defense**: If the candidate universe was truncated ($K \le 30$) and not all invoices in a single matching combination have explicit reference matches, emits `AMBIGUOUS_MULTI_INVOICE_MATCH` (`TRUNCATED_UNIVERSE_AMBIGUITY`).
  8. **Deterministic Total Order**: Matching hypotheses are sorted by `(not has_reference_match, -matched_reference_count, combination_size, max_date_difference_days, tuple(invoice_numbers), tuple(invoice_ids))` ensuring strict 100-run permutation invariance.
  9. **No Premature Scoring**: No composite `match_score` is computed; factual structured evidence signals are emitted only (`MULTI_INVOICE_SUM_EXACT`, `INVOICE_NUMBER_MATCH`, `CUSTOMER_NAME_MATCH`, `DATE_PROXIMITY_MATCH`).
- **Consequences**: Certified deterministic 1:N multi-invoice matching engine with 33 automated tests (453 total platform tests passing, 0 failures, 137.07s runtime), verified, audited, and frozen for Phase 14.7. Downstream Phase 14.8 (Combination Matching) will build upon this foundation.

---

## ADR-016: Deterministic Combination Matching Engine with Rule M-3 FIFO Aging Prioritization
- **Date**: 2026-10-07
- **Status**: Accepted / Frozen
- **Context**: The reconciliation pipeline requires an objective, deterministic capability to evaluate combinations of open invoices ($2 \le k \le 4$) against an incoming payment, and to evaluate competing subsets of invoices that sum to the exact same payment amount (canonical Rule M-3 FIFO Aging Heuristic, e.g., ₹35,000 payment against candidates {10k, 25k, 15k, 8k, 12k}). The engine must prioritize the combination settling the oldest outstanding invoices by due date while explicitly flagging it for human review (`REVIEW_REQUIRED`) due to inherent combination ambiguity, allow explicit narration reference matches to override default aging, preserve unresolvable ambiguity when due dates and references are tied, strictly decouple matching evaluation from financial mutation, bound combinatorial search complexity, and guard against incomplete universe truncation risk.
- **Decision**:
  1. **Strict Tripartite Matching Status with Prioritization**: The engine produces an objective matching fact: `UNIQUE_COMBINATION_MATCH`, `PRIORITIZED_COMBINATION_MATCH`, `AMBIGUOUS_COMBINATION_MATCH`, or `NO_COMBINATION_MATCH`.
  2. **Rule M-3 FIFO Aging Heuristic**: When multiple competing combinations equal the payment amount, the subset settling the oldest debt (by minimum invoice `due_date`) receives priority. It is assigned status `PRIORITIZED_COMBINATION_MATCH` (`FIFO_AGING_PRIORITIZED`), flagged `requires_review = True`, and backed by structured evidence signal `FIFO_OLDEST_INVOICE_PRIORITY`.
  3. **Reference Match Precedence**: Explicit narration token matches take priority over the default FIFO aging heuristic. If narration explicitly references invoices in a specific combination, that combination is prioritized with reason code `REFERENCE_PRIORITIZED`.
  4. **Strict Ambiguity Preservation**: When competing combinations have tied due dates and reference counts (or when FIFO aging is disabled via criteria toggle), the engine preserves unresolvable ambiguity (`AMBIGUOUS_COMBINATION_MATCH`, `MULTIPLE_COMBINATIONS_UNRESOLVED`, `prioritized_combination = None`).
  5. **Zero Financial Mutation**: Strictly in-memory evaluation producing matching hypotheses. No writes to invoices, payments, allocations, or ledgers. Verified via session dirty checking (`len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`).
  6. **Combinatorial Complexity Hard-Bounding & Exact Decimal Conservation**: Enforces $2 \le k \le \text{max\_combination\_size} \le 4$ across both domain invariants (`CombinationMatchCriteria`) and presentational schema boundaries (`ge=2, le=4`, HTTP 422 if invalid) over pre-filtered candidate universes ($n \le 30$). `amount_tolerance` is locked to `Decimal("0.00")` (HTTP 422 if $> 0.00$), guaranteeing zero float imprecision or unauthorized financial tolerance loosening. Evaluated in $< 1.0\text{s}$ under $n=30, k=4$ benchmark (31,900 combinations evaluated in $< 150\text{ms}$).
  7. **Incomplete Universe Defense with Truncation Override**: If the candidate universe was truncated ($K \le 30$), unretrieved open invoices could form older or competing subsets. Rule M-3 FIFO aging heuristic is **strictly overridden by truncation ambiguity**, emitting `AMBIGUOUS_COMBINATION_MATCH` (`TRUNCATED_UNIVERSE_AMBIGUITY`, `prioritized_combination = None`, `requires_review = True`), unless 100% of the invoices in a candidate combination possess an explicit narration reference match.
  8. **Deterministic Total Order**: Hypotheses are sorted by `(not has_reference_match, -matched_reference_count, oldest_due_date, average_days_to_due, combination_size, max_date_difference_days, tuple(invoice_numbers), tuple(invoice_ids))` ensuring strict 100-run permutation invariance.
  9. **No Premature Scoring**: No composite `match_score` is computed; factual structured evidence signals are emitted only.
- **Consequences**: Certified deterministic combination matching engine with 32 automated tests (486 total platform tests passing, 0 failures, 108.30s runtime), verified, audited, remediated, and frozen for Phase 14.8. Downstream Phase 14.9 (Evidence Collection) will build upon this foundation.

---

## ADR-017: Deterministic Evidence Collection Engine & Four-Tier Classification Layer
- **Date**: 2026-10-07
- **Status**: Accepted / Verification Pending
- **Context**: The reconciliation workflow requires an objective, bounded, read-only evidence collection layer that converts raw transaction and candidate invoice facts into structured, classified evidence items. The layer must distinguish direct, supporting, missing, and conflicting evidence without making financial decisions, without calculating composite scores (Phase 14.11), without probabilistic calibration (Phase 14.12), and with zero database mutations.
- **Decision**:
  1. **Strict Four-Tier Classification**: Every extracted evidence item is strictly classified into `DIRECT` (high-fidelity explicit identifiers, exact amount equality, account match), `SUPPORTING` (contextual signals, partial amounts, name token overlap, aliases, UTR presence, date causality/proximity), `MISSING` (absent or unprovided signals without fabrication), or `CONFLICTING` (contradictory identifiers belonging to other customers, different invoice numbers explicitly mentioned in narration, non-causal payment dates, currency mismatch).
  2. **Bounded Scope & Separation of Concerns**: Evidence Collection $\neq$ Matching $\neq$ Scoring $\neq$ Confidence $\neq$ Approval $\neq$ Mutation. The evidence layer describes *what evidence exists and what it says*; it never produces a numeric match score or decides payment disposition.
  3. **Zero Financial Mutation**: Read-only domain evaluation. Verified through transaction inspection (`len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`).
  4. **Strict Decimal Precision & Serialized Determinism**: All monetary amounts use Python `Decimal`. `to_deterministic_payload()` separates evaluation metadata (`collected_at`) from the factual evidence payload, guaranteeing bit-for-bit identity across 100-run candidate permutations. Candidate bundles are sorted deterministically by `(invoice_number, str(invoice_id))`.
  5. **Defense-in-Depth Cross-Tenant Candidate Isolation**: Domain engine (`EvidenceCollectionEngine`) and application use case (`EvidenceCollectionUseCase`) enforce strict candidate ownership matching (`cand.company_id == payment.company_id`), failing closed with `DomainError` / `ForbiddenError` if any cross-tenant candidate is supplied.
  6. **Cross-Tenant Fail-Closed Security**: `POST /api/v1/reconciliation/evidence-collection/{payment_id}` enforces JWT company tenant scope; cross-tenant payments or customer context overrides are rejected with HTTP 404.
- **Consequences**: Certified deterministic evidence collection engine with 15 targeted unit and API tests (501 total platform tests passing, 0 failures, 136.63s runtime). Remediated, verified, and pending independent verification. Downstream Phase 14.10 and Phase 14.11 (Scoring Engine) will build upon this foundation.
