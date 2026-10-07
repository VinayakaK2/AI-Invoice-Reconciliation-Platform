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

### Phase 12 — OCR & Document Processing [VERIFIED & FROZEN]
- [x] Pure-Python magic byte detection (`%PDF-`, PNG `\x89PNG`, JPEG `\xff\xd8\xff`) and polyglot script/executable defense (`MZ`, `ELF`, shebang, `<script>`) (`app/modules/invoice/domain/file_security.py`)
- [x] Pre-flight Content-Length check and chunked 64KB bounded streaming reader with early abort (`app/modules/invoice/presentation/upload_utils.py`)
- [x] Deterministic financial parser with exact Decimal(14, 2) half-up rounding, Indian (lakhs/crores) and Western numbering, formula injection defense (CWE-1236) (`app/modules/invoice/domain/extraction_rules.py`)
- [x] Canonical date parsing, temporal order check (`due_date >= issue_date`), and ambiguity detection (`DD/MM/YYYY` vs `MM/DD/YYYY`)
- [x] Multi-signal identifier isolation: GSTIN, PAN extraction, PO number, and UTR separation preventing collision with invoice numbers
- [x] Deterministic mathematical cross-validation (`subtotal + tax - discount + round_off ≈ total`) with zero silent corrections (Rule 20)
- [x] Pluggable OCR provider boundary: Heuristic, Mock/Test, Cloud Vision Adapter, Multimodal AI Adapter, and Fallback Composite provider (`app/modules/invoice/application/ports.py`)
- [x] 6-stage document processing pipeline (`app/modules/invoice/application/pipeline.py`)
- [x] Expanded document state machine (`UPLOADED`, `PROCESSING`, `EXTRACTED`, `VALIDATED`, `VALIDATION_REQUIRED`, `FAILED`, `MANUALLY_CORRECTED`)
- [x] Database migration (`0006_phase_12_ocr_documents.py`) adding `updated_at` and `idx_invoice_docs_company_status` index
- [x] First-class document management REST endpoints under `/api/v1/invoices/documents` (list, detail, stream, retry, correct, promote, delete)
- [x] Document streaming security headers (`X-Content-Type-Options: nosniff`, CSP `default-src 'none'`, `no-store`)
- [x] Safe document deletion with payment protection (refuses deletion if linked invoice has recorded payments)
- [x] 62 automated unit and integration tests passing with 90% invoice module coverage (293 total platform tests passing, 100% on pipeline)
- [x] Phase 12 Verification & Audit completed (`docs/phase-history/phase-12-ocr-document-processing.md`)

### Phase 13 — Payment & Bank Statement Management [VERIFIED & FROZEN]
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

### Phase 14.1 — Reconciliation Engine: Payment Intake [VERIFIED & FROZEN]
- [x] Domain entities: `PaymentIntakeStatus` (ELIGIBLE, ALREADY_PROCESSED, INELIGIBLE, INVALID), `PaymentIntakeReasonCode`, `PaymentIntakeResult`, `PaymentIntakeRuleEngine` (`payment_intake.py`)
- [x] Enriched `PaymentIntakeContext` with sub-balances (`allocated_amount`, `unallocated_amount`), status, transaction type, and `effective_amount` property
- [x] Pure deterministic evaluation waterfall: gross amount check (>0), ISO 4217 currency validation, credit/debit transaction type, balance conservation check (`allocated + unallocated == amount`), temporal anomaly check, and accountant-ignored status
- [x] Downstream integration: `PayerIdentificationRuleEngine` and `GenerateCandidateInvoicesUseCase` delegate intake validation and evaluate against `effective_amount` (unallocated balance)
- [x] Application ports and use cases: `PaymentLookupPort.list_eligible_intake_contexts`, `IntakePaymentUseCase`, `BatchIntakePaymentUseCase`
- [x] Infrastructure adapter: `SQLAlchemyPaymentLookupAdapter` with eager bank transaction join, metadata fallbacks, and sub-balance retrieval
- [x] Presentation layer: `POST /api/v1/reconciliation/intake/{payment_id}`, `POST /api/v1/reconciliation/intake-batch`, sensitive bank coordinate masking
- [x] Rule 4 Zero-Mutation Guarantee verified: SQLAlchemy dirty checking proves 0 dirty, 0 new, 0 deleted objects, and byte-for-byte pre/post database invariance
- [x] Fail-closed IDOR security verified: cross-tenant access returns 404 Not Found (never revealing existence)
- [x] 24 new automated tests across 3 test files passing (`test_payment_intake_rules.py`, `test_payment_intake_api.py`, `test_payment_intake_tenant_security.py`)
- [x] Phase 14.1 verification and audit certification completed (`docs/phase-history/phase-14-1-payment-intake.md`)

### Phase 14.2 — Reconciliation Engine: Customer Identification Foundation [VERIFIED & FROZEN]
- [x] Domain entities: `IdentificationStatus`, `EvidenceType`, `SignalStrength`, `EvidenceSignal`, `CustomerMatchCandidate`, `CustomerIdentificationResult` (`app/modules/reconciliation/domain/`)
- [x] Banking stopwords dictionary, payment aggregator handles, and corporate suffixes (`stopwords.py`)
- [x] String normalizer with NFKD decomposition, diacritics stripping, and ReDoS-safe VPA/account extraction (`normalizer.py`)
- [x] Deterministic rule waterfall: Direct coordinates (100.0), extracted narration coordinates (95.0), exact aliases (85.0), normalized legal name (75.0), clean payer name (70.0), reference match (50.0), narration token overlap (20.0-45.0) (`rules.py`)
- [x] Conflict detection (different coordinates or coordinate vs explicit name) yielding `CONFLICTING`
- [x] Ambiguity detection (score delta < 15.0 or shared accounts) yielding `AMBIGUOUS`
- [x] Strict archived customer exclusion from matching catalog
- [x] Application ports and use cases: `CustomerLookupPort`, `PaymentLookupPort`, `IdentifyPaymentCustomerUseCase`, `BatchIdentifyPaymentCustomersUseCase`
- [x] SQLAlchemy lookup adapters with tenant scoping (`SQLAlchemyCustomerLookupAdapter`, `SQLAlchemyPaymentLookupAdapter`)
- [x] Zero financial mutation guarantee: session dirty checking verifies 0 modified, 0 new, 0 deleted rows (0.00% financial mutation risk)
- [x] REST API endpoints: `POST /api/v1/reconciliation/identify/{id}`, `POST /api/v1/reconciliation/identify-batch`, `GET /status`
- [x] Sensitive coordinate masking in API responses (`********5544` and `jo******@icici`)
- [x] 49 automated tests across 7 test files passing (206 total platform tests, 93% platform coverage)
- [x] Phase 13.1 forensic freeze gate and audit certification completed (`docs/phase-history/phase-13-1-customer-identification.md`)

### Phase 14.3 — Reconciliation Engine: Candidate Invoice Generation [IMPLEMENTED & VERIFIED]
- [x] Domain entities: `CandidateInvoice`, `CandidateInvoiceEvidenceSignal`, `CandidateInvoiceUniverse`, `InvoiceEvidenceType` (`candidate_invoices.py`)
- [x] Deterministic evidence taxonomy: `INVOICE_NUMBER_MATCH` (+40.0), `EXACT_AMOUNT_MATCH` (+35.0), `EXACT_ORIGINAL_AMOUNT_MATCH` (+25.0), `PARTIAL_AMOUNT_COMPATIBLE` (+15.0), `DATE_RELEVANCE` (up to +20.0 combining causality and due-date proximity)
- [x] Micro-aging tie-breaker and 5-key deterministic sorting: `retrieval_priority` DESC, `is_exact_amount_match` DESC, `is_reference_match` DESC, `due_date` ASC (FIFO), `invoice_id` ASC
- [x] Candidate bounding & truncation audit metadata: `total_eligible_invoices`, `truncated: bool`, `candidate_limit: int` (default 30, max 100), `truncation_reason`
- [x] Strict currency isolation & mismatch diagnostics: `currency_mismatches_detected`, `status_code="CURRENCY_MISMATCH"`
- [x] Application layer: `InvoiceCandidateContext`, `InvoiceLookupPort`, `GenerateCandidateInvoicesUseCase`
- [x] Infrastructure adapter: `SQLAlchemyInvoiceLookupAdapter` leveraging existing Phase 11 composite indexes (`idx_invoices_company_customer`, `idx_invoices_company_status`, `idx_invoices_company_archived`) with zero new database migrations
- [x] Presentation layer: `POST /api/v1/reconciliation/candidates/{payment_id}` with optional `override_customer_id` and `limit`, fail-closed 404 IDOR protection
- [x] Absolute zero financial state mutation verified across 100% of test runs
- [x] 25 automated tests across 4 test files passing: `test_candidate_invoice_entities.py` (7), `test_candidate_invoice_rules.py` (11), `test_candidate_invoice_api.py` (6), `test_candidate_invoice_performance.py` (1)
- [x] Full platform regression suite: 231 tests passing (100%), 0 failures, 93% total platform statement coverage across 4,418 statements
- [x] Phase 14.3 verification and phase history completed (`docs/phase-history/phase-14-3-candidate-invoice-generation.md`)

### Phase 14.4 — Reconciliation Engine: Candidate Filtering [VERIFIED & FROZEN]
- [x] Domain entities: `FilterExclusionReason` (18-code taxonomy), `CandidateFilterCriteria`, `ExcludedCandidateInvoice`, `FilteredCandidateUniverse`, `CandidateFilterRuleEngine` (`candidate_filters.py`)
- [x] 11-Gate fail-closed waterfall pipeline: Tenant check, customer check, customer archival, invoice archival, currency check, invoice status, balance conservation & non-negative check, amount policy bounds, date/temporal causality & lookback window, reference match constraints, and stream deduplication
- [x] 5-Key deterministic comparator prior to bounding: `retrieval_priority` DESC, `is_exact_amount_match` DESC, `is_reference_match` DESC, `due_date` ASC (FIFO), `invoice_id` ASC
- [x] Permutation invariance verified: identical retained set and ranks 1..K across 100 randomized shuffles
- [x] Fast candidate pruning adapter: `CandidateFilterRuleEngine.filter_contexts` for in-memory and database pre-filtering
- [x] Application layer: `FilterCandidateInvoicesUseCase` in `app/modules/reconciliation/application/use_cases.py`
- [x] Presentation layer: `POST /api/v1/reconciliation/candidates/{payment_id}/filtered` with criteria parameters, Bearer auth, and fail-closed 404 IDOR defense
- [x] Zero financial state mutation guarantee verified: 0 dirty, 0 new, 0 deleted SQLAlchemy objects (`len(db.dirty) == 0`) across repeated calls
- [x] 31 automated tests across 4 test files passing: `test_candidate_filtering_rules.py` (13), `test_candidate_filtering_api.py` (4), `test_candidate_filtering_tenant_security.py` (4), `test_candidate_filtering_adversarial.py` (10)
- [x] Full platform regression suite: 348 tests passing (100%), 0 failures, 93% total platform statement coverage across 5,737 statements
- [x] Phase 14.4 forensic verification and phase history completed (`docs/phase-history/phase-14-4-candidate-filtering.md`)

### Phase 14.5 — Reconciliation Engine: Exact Matching (1:1) [VERIFIED & FROZEN]
- [x] Domain entities: `ExactMatchStatus` (EXACT_MATCH, NO_EXACT_MATCH, AMBIGUOUS_EXACT_MATCH), `ExactMatchReasonCode` (14-code taxonomy), `ExactMatchEvidenceType`, `ExactMatchEvidenceSignal`, `ExactMatchCriteria`, `ExactMatchHypothesis`, `ExactMatchResult` (`exact_matching.py`)
- [x] Authoritative balance comparison invariant: strictly compares `payment.effective_amount == candidate.outstanding_amount` with identical ISO currency validation
- [x] 4-Key deterministic non-scoring comparator sorting: `(not is_reference_match, date_difference_days, invoice_number, invoice_id)` with verified 100-run permutation invariance
- [x] Ambiguity preservation (Prohibition of autonomous tie-breaking): In accordance with `business-rules.md` §2.5 line 64, equal amount candidates unconditionally emit `AMBIGUOUS_EXACT_MATCH` with `matched_candidate = None` and `competing_candidates` populated. Reference match signals are retained for human review.
- [x] Incomplete universe truncation awareness: detects `TRUNCATED_UNIVERSE_AMBIGUITY` when candidate universe is truncated and single exact match lacks explicit narration reference match
- [x] Truncated exclusion collision detection: checks Phase 14.4 `universe.excluded_candidates` for amount collisions under `TRUNCATED_BY_LIMIT`
- [x] Application layer: `ExactMatchUseCase` (single payment evaluation via Phase 14.4 candidate universe with fail-closed IDOR protection) and `BatchExactMatchUseCase` (batch up to 100 payments within tenant boundary)
- [x] Presentation layer: `POST /api/v1/reconciliation/exact-match/{payment_id}` and `POST /api/v1/reconciliation/exact-match-batch` with JWT auth, tenant scoping, and full response envelope
- [x] Zero financial state mutation guarantee verified: 0 dirty, 0 new, 0 deleted SQLAlchemy objects (`len(db.dirty) == 0`) across single, batch, and repeated runs
- [x] 32 automated tests across 4 test files passing: `test_exact_matching_rules.py` (18), `test_exact_matching_api.py` (6), `test_exact_matching_tenant_security.py` (4), `test_exact_matching_adversarial.py` (4)
- [x] Full platform regression suite: 388 tests passing (100%), 0 failures, 85.73s runtime, 0 warnings
- [x] Phase 14.5 forensic verification, B-01/B-02 remediation, and phase history completed (`docs/phase-history/phase-14-5-exact-matching.md`)

---

### Phase 14.6 — Reconciliation Engine: Partial Payment Matching (1:1) [FROZEN]
- [x] Domain layer: `PartialMatchRuleEngine`, `PartialMatchHypothesis`, `PartialMatchResult`, `PartialMatchCriteria`, `PartialMatchEvidenceSignal`, `PartialMatchStatus`, `PartialMatchReasonCode`
- [x] Strict Decimal financial arithmetic: evaluates $0 < \text{payment.effective\_amount} < \text{candidate.outstanding\_amount}$ using authoritative outstanding amounts
- [x] Strict Ambiguity Preservation: multiple candidates with balance > payment emit `AMBIGUOUS_PARTIAL_MATCH` (`MULTIPLE_PARTIAL_CANDIDATES`) without arbitrary tie-breaking
- [x] Single-invoice scope: evaluates candidates individually; multi-invoice combination matching strictly deferred to Phase 14.7
- [x] Exact match non-interference: exact amount payments emit `NO_PARTIAL_MATCH` with reason `EXACT_MATCH_DETECTED` (governed by Phase 14.5)
- [x] Truncation ambiguity defense: detects when candidate universe truncation creates ambiguity risk from unretrieved candidates
- [x] Application layer: `PartialMatchUseCase` and `BatchPartialMatchUseCase` with fail-closed IDOR security
- [x] Presentation layer: `POST /api/v1/reconciliation/partial-match/{payment_id}` and `POST /api/v1/reconciliation/partial-match-batch`
- [x] Zero financial state mutation guarantee: verified `len(db.dirty) == 0` across single, batch, and repeated runs
- [x] 32 automated tests across 4 test files passing: `test_partial_matching_rules.py` (18), `test_partial_matching_api.py` (6), `test_partial_matching_tenant_security.py` (4), `test_partial_matching_adversarial.py` (4)
- [x] Full platform regression suite: 420 tests passing (100%), 0 failures, 91.04s runtime
- [x] Phase 14.6 phase history and freeze certification completed (`docs/phase-history/phase-14-6-partial-matching.md`)

### Phase 14.7 — Reconciliation Engine: Multi-Invoice Matching (1:N) [FROZEN]
- [x] Domain layer: `MultiInvoiceMatchRuleEngine`, `MultiInvoiceMatchHypothesis`, `MultiInvoiceMatchResult`, `MultiInvoiceMatchCriteria`, `MultiInvoiceMatchEvidenceSignal`, `MultiInvoiceMatchStatus`, `MultiInvoiceMatchReasonCode`
- [x] Strict Decimal financial arithmetic: evaluates $\sum_{i=1}^k \text{candidate}_i\text{.outstanding\_amount} == \text{payment.effective\_amount}$ ($2 \le k \le 4$) using authoritative outstanding amounts
- [x] Strict Ambiguity Preservation: multiple distinct subsets/combinations summing to payment emit `AMBIGUOUS_MULTI_INVOICE_MATCH` (`MULTIPLE_MULTI_INVOICE_MATCHES`) without arbitrary tie-breaking
- [x] Multi-invoice scope (1:N): evaluates combinations of $k \ge 2$; single-invoice exact ($k=1$) diagnosed as `EXACT_MATCH_DETECTED` (Phase 14.5), single-invoice partial ($k=1$) diagnosed as `PARTIAL_MATCH_DETECTED` (Phase 14.6)
- [x] Combinatorial complexity bounding: enforced $2 \le k \le \text{max\_combination\_size} \le 10$ (default $k \le 4$) over candidate universe ($n \le 30$)
- [x] Truncation ambiguity defense: detects `TRUNCATED_UNIVERSE_AMBIGUITY` when candidate universe is truncated and not all invoices in match combination have explicit reference matches
- [x] Application layer: `MultiInvoiceMatchUseCase` and `BatchMultiInvoiceMatchUseCase` with fail-closed IDOR security
- [x] Presentation layer: `POST /api/v1/reconciliation/multi-invoice-match/{payment_id}` and `POST /api/v1/reconciliation/multi-invoice-match-batch`
- [x] Zero financial state mutation guarantee: verified `len(db.dirty) == 0` across single, batch, and repeated runs
- [x] 33 automated tests across 4 test files passing: `test_multi_invoice_matching_rules.py` (19), `test_multi_invoice_matching_api.py` (6), `test_multi_invoice_matching_tenant_security.py` (4), `test_multi_invoice_matching_adversarial.py` (4)
- [x] Full platform regression suite: 453 tests passing (100%), 0 failures, 137.07s runtime
- [x] Phase 14.7 phase history and freeze certification completed (`docs/phase-history/phase-14-7-multi-invoice-matching.md`)

---

## Disambiguation Rules:
1. **Reconciliation Engine Sub-Phases**: Phase 14.1 (Payment Intake), Phase 14.2 (Payer Identification), Phase 14.3 (Candidate Invoice Generation), Phase 14.4 (Candidate Filtering), Phase 14.5 (Exact Matching), Phase 14.6 (Partial Matching), Phase 14.7 (Multi-Invoice Matching), Phase 14.8 (Combination Matching).
2. **`retrieval_priority` vs Candidate Scoring**: `retrieval_priority` (0–100) is a Phase 14.3 candidate presentation heuristic; composite candidate scoring (`match_score`) belongs strictly to Phase 14.11. Phases 14.5, 14.6, and 14.7 produce factual matching signals only.
3. **`AUTO_ELIGIBLE`**: High evidence score qualification for expedited human review. NOT `AUTO_APPLIED`.
4. **Zero LLM Authority**: Deterministic rules decide. AI assists text extraction only. Humans resolve uncertainty.

---

## Up Next:
- **Phase 14.8**: Combination Matching Engine (competing subset evaluation across multiple candidate invoices, FIFO aging heuristics / Rule M-3, bounded candidate resolution).
- **Phase 14.9–14.13**: Evidence Collection & Normalization (14.9–14.10), Confidence Scoring (14.11), Decision Engine (14.12), Payment Allocation (14.13). Note: Generalized M:N (Many Payments to Many Invoices) is deferred to future post-core roadmap.
- **Phase 15**: Human Review & Exception Workflows.
- **Phase 16**: Review Center UI & Frontend Integration.


