# Phase 12 History — OCR & Document Processing

**Phase:** Phase 12  
**Status:** COMPLETE & FROZEN  
**Date:** 2026-09-08  

---

## 1. Objective

Deliver the production-grade **OCR & Document Processing** capability for the AI Invoice Reconciliation Platform.

This phase transforms raw uploaded invoice documents (PDF, PNG, JPEG) into structured, mathematically validated invoice records with complete document provenance, multi-tenant isolation, failure recovery, and human accountant correction controls.

In accordance with **Core Engineering Rule 15**:
> *"Deterministic systems decide. AI assists. Humans resolve uncertainty."*

OCR and external AI model extractions are treated as untrusted external inputs that **never** directly mutate financial ground truth. Extracted invoices enter as candidate `DRAFT` records or `VALIDATION_REQUIRED` documents; promotion to active operational `PENDING` status strictly requires verified invariants and human confirmation.

---

## 2. Product Boundaries & Non-Goals

### What Phase 12 Delivers
1. **Intake Security**: Magic-byte signature verification, polyglot script/executable defense, extension cross-matching, bounded streaming intake with pre-flight content checks.
2. **Pluggable OCR Architecture**: Clean abstract provider boundary (`InvoiceOCRProvider`) supporting Heuristic, Mock/Test, Cloud Vision Adapter, Multimodal AI Adapter, and Fallback Composite providers.
3. **Deterministic Financial Parsing**: Exact `Decimal(14, 2)` parsing with half-up rounding, Indian numbering (lakhs/crores) and Western comma grouping, formula injection defense (CWE-1236).
4. **Canonical Date & Identifier Parsing**: Ambiguity flagging for numeric dates (`DD/MM/YYYY` vs `MM/DD/YYYY`), temporal order validation (`due_date >= issue_date`), and cross-exclusion of GSTIN, PAN, VAT, PO, and UTR from invoice numbers.
5. **Deterministic Mathematical Cross-Validation**: Validation of `subtotal + tax - discount + round_off ≈ total` with zero silent modifications (Rule 20).
6. **First-Class Document Management REST APIs**: Full lifecycle endpoints for document listing, detail inspection, streaming with browser security headers, human correction, retry, and promotion.

### Explicit Product Non-Goals
- Payment reconciliation (reserved for Phase 14)
- Customer identification and match scoring (Phase 14.2)
- Candidate invoice generation (Phase 14.3)
- Payment allocation (Phase 15)
- Automated accounting or GL journal entries (External ERP concern)

---

## 3. Architecture & Implementation Delivered

### 3.1 Domain Layer (`app/modules/invoice/domain/`)

1. **File Security & Signature Verification (`file_security.py`)**:
   - `FileSecurityValidator`: Pure-Python magic byte scanner without external C-library dependencies.
   - Verifies true signatures: `%PDF-` for PDF, `\x89PNG\r\n\x1a\n` for PNG, `\xff\xd8\xff` for JPEG.
   - Rejects polyglots and executable headers (`MZ` DOS/PE, `\x7fELF`, `#!/bin/sh`, `<script>`, `<?php`).
   - Validates extension cross-matching to prevent MIME spoofing.
   - Enforces upload bounds ($8\text{ bytes} \le \text{size} \le 10\text{ MB}$).

2. **Extraction, Normalization & Math Validation Rules (`extraction_rules.py`)**:
   - `parse_monetary_amount`: Converts Indian (`1,50,000.00`) and Western (`150,000.00`) groupings into exact `Decimal(14, 2)`. Neutralizes formula injection attempts (`=SUM(...)`).
   - `parse_date_string`: Canonicalizes textual (`15 Jan 2026`) and ISO dates. Flags ambiguous dates (`04/05/2026`) while defaulting to Indian `DD-MM-YYYY`.
   - `validate_temporal_consistency`: Enforces `due_date >= issue_date`.
   - `extract_identifiers`: Isolates 15-character GSTIN, extracts constituent PAN, isolates PO numbers and UTR references, and prevents stopwords or tax IDs from becoming invoice numbers.
   - `validate_invoice_mathematics`: Validates `subtotal + tax - discount + round_off ≈ total` with explicit tolerance (0.05 default, 1.00 for round-off under CGST Sec 170). Flags discrepancies without silently changing amounts.
   - `evaluate_extraction_confidence_and_ambiguity`: Calculates weighted confidence (0.00 to 1.00) across 6 signals and flags `is_ambiguous = True` whenever key fields are missing or inconsistent.

3. **Domain Entities & Lifecycle State Machine (`entities.py`)**:
   - Expanded `OCRStatus`: `UPLOADED`, `PROCESSING`, `EXTRACTED`, `VALIDATION_REQUIRED`, `VALIDATED`, `FAILED`, `MANUALLY_CORRECTED`, plus backward-compatible `PENDING` and `COMPLETED`.
   - State transition methods on `InvoiceDocument`:
     - `start_processing()`: `PENDING`/`UPLOADED` $\to$ `PROCESSING`
     - `mark_extracted(raw_data)`: `PROCESSING` $\to$ `EXTRACTED`
     - `mark_validated(payload)`: $\to$ `VALIDATED`
     - `mark_validation_required(payload)`: $\to$ `VALIDATION_REQUIRED`
     - `mark_ocr_failed(error, retryable)`: $\to$ `FAILED`
     - `mark_manually_corrected(data)`: $\to$ `MANUALLY_CORRECTED`

### 3.2 Infrastructure & Persistence Layer (`app/modules/invoice/infrastructure/`)

1. **Storage Hardening (`ports.py: LocalStorageService`)**:
   - Path traversal prevention using `Path.resolve().is_relative_to(tenant_dir)`.
   - Null-byte rejection and filename sanitization with 64-character length capping.
   - Opaque content-addressed storage keys: `<sha256_hash>_<clean_filename>`.
   - Complete tenant isolation: files stored strictly under `storage/invoices/<company_id>/`.

2. **Database Schema & Indexes (`models.py`)**:
   - Added `updated_at` timestamp tracking to `InvoiceDocumentModel`.
   - Added composite operational index `idx_invoice_docs_company_status` on `(company_id, ocr_status)` for fast tenant-scoped queue queries.

3. **Alembic Migration (`backend/alembic/versions/0006_phase_12_ocr_documents.py`)**:
   - Safe, idempotent schema migration adding `updated_at` column and composite index to `invoice_documents`.

4. **Repository Capabilities (`repositories.py: InvoiceDocumentRepository`)**:
   - `update(doc)`: Updates extraction payload, status, retry count, and `updated_at`.
   - `delete(doc_id, company_id)`: Tenant-scoped document deletion.
   - `list_documents(company_id, ocr_status, has_invoice, limit, offset)`: Filterable, paginated queries.
   - `acquire_for_processing(doc_id, company_id)`: Atomic optimistic lock for worker jobs.

### 3.3 Application Pipeline & Use Cases (`app/modules/invoice/application/`)

1. **6-Stage Processing Pipeline (`pipeline.py`)**:
   - Stage 1: Security & File Signature Intake Validation
   - Stage 2: Tenant Storage & Content Hashing (SHA-256 deduplication)
   - Stage 3: Pluggable OCR Extraction
   - Stage 4: Deterministic Normalization
   - Stage 5: Deterministic Financial Validation Gate
   - Stage 6: Structured Result Assembly

2. **Application Use Cases (`use_cases.py`)**:
   - `UploadInvoiceDocumentUseCase`: Validates intake, invokes pipeline, and generates candidate `DRAFT` invoice if valid minimum fields exist.
   - `GetInvoiceDocumentUseCase`: Retrieves document metadata, structured extraction, and linked invoice.
   - `ListInvoiceDocumentsUseCase`: Paginated document listing with status and invoice filtering.
   - `GetInvoiceDocumentFileStreamUseCase`: Streams original document bytes with tenant verification.
   - `RetryInvoiceDocumentProcessingUseCase`: Safely re-runs extraction on stored bytes without file duplication.
   - `CorrectInvoiceDocumentUseCase`: Applies human accountant edits to normalized fields and synchronizes with linked draft invoice.
   - `PromoteInvoiceDocumentUseCase`: Converts validated document into active operational `PENDING` invoice.
   - `DeleteInvoiceDocumentUseCase`: Safely deletes document and purges physical file, strictly preventing deletion if linked to an invoice with recorded payments.

### 3.4 Presentation Layer & REST Endpoints (`app/modules/invoice/presentation/`)

1. **Chunked Streaming Intake (`upload_utils.py`)**:
   - `read_upload_file_safely`: Pre-flight `Content-Length` validation and chunked 64KB streaming read. Early aborts before buffering oversized files into memory (DoS protection).

2. **REST Endpoints (`router.py`)**:
   - `POST /api/v1/invoices/upload` (201 Created) — Secure upload and OCR intake.
   - `GET /api/v1/invoices/documents` (200 OK) — Paginated document queue with status and invoice filtering.
   - `GET /api/v1/invoices/documents/{id}` (200 OK) — Structured extraction details and validation report.
   - `GET /api/v1/invoices/documents/{id}/file` (200 OK) — Secure file stream with `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'`, and `Cache-Control: no-store, private`.
   - `POST /api/v1/invoices/documents/{id}/retry` (200 OK) — Re-execute OCR without file duplication.
   - `PUT /api/v1/invoices/documents/{id}/correct` (200 OK) — Human accountant corrections.
   - `POST /api/v1/invoices/documents/{id}/promote` (200 OK) — Promote document into active operational `PENDING` invoice.
   - `DELETE /api/v1/invoices/documents/{id}` (200 OK) — Safe deletion with payment protection.
   - `GET /api/v1/invoices/{invoice_id}/document` (200 OK) — Secure file stream with browser protection headers.

---

## 4. Verification & Test Evidence

### 4.1 Automated Test Execution Summary

The platform test suite was executed in full:
```text
Total Tests Collected: 293
Total Tests Passed:    293 (100%)
Total Tests Failed:    0
Test Warnings:         1 (Starlette deprecation warning for httpx in testclient)
Total Duration:        110.89 seconds
```

### 4.2 Phase 12 Dedicated Test Suites (62 Tests Total)

1. `tests/unit/test_ocr_security.py` (19 tests):
   - Magic byte detection (`%PDF-`, PNG, JPEG).
   - Polyglot & executable blocking (DOS/PE, ELF, shebang, script/HTML, PHP).
   - MIME spoofing detection (`.pdf` containing PNG, `.jpg` containing PDF).
   - Size boundary guards (empty, minimum header, 10MB upload limit).
   - Local storage path traversal prevention (`../../`) and null-byte rejection.

2. `tests/unit/test_ocr_parsing_and_validation.py` (22 tests):
   - Indian numbering format with lakhs and crores (`1,50,000.00`).
   - Western numbering format (`150,000.00`).
   - Malformed comma rejection, alphabetical characters in amounts, and negative amount checks.
   - Formula injection defenses (`=SUM(...)`).
   - Canonical ISO and textual date parsing (`15 Jan 2026`).
   - Date ambiguity detection (`04/05/2026`), day-first / month-first configurations, and temporal order validation (`due_date >= issue_date`).
   - Invalid calendar date handling (e.g. 31 February).
   - Multi-signal identifier isolation (GSTIN, PAN, PO, UTR) with stopword exclusion.
   - Deterministic mathematical consistency (`subtotal + tax - discount + round_off ≈ total`).
   - Weighted confidence scoring and ambiguity triggers.

3. `tests/unit/test_ocr_pipeline.py` (8 tests):
   - Document state machine valid and illegal transitions.
   - Mock OCR provider error simulations (timeout, 503 outage, corrupt document).
   - Composite fallback provider primary-to-fallback cascade.
   - End-to-end pipeline execution for clean and ambiguous documents.

4. `tests/integration/test_ocr_document_integration.py` (3 tests):
   - Complete lifecycle: Upload $\to$ List $\to$ Inspect Detail $\to$ Stream with Security Headers $\to$ Correct $\to$ Promote.
   - Document retry execution without duplicate storage records.
   - Document deletion with financial payment protection.

5. `tests/integration/test_ocr_tenant_security.py` (1 test):
   - Strict multi-tenant isolation and IDOR protection.
   - Company B receives `404 Not Found` when attempting to access, read, stream, correct, retry, promote, or delete Company A's documents.
   - Document list queries strictly return tenant-owned records.

6. `tests/integration/test_ocr_forensic_edge_cases.py` (9 tests):
   - Payment allocation deletion protection: rejecting deletion when linked invoice has recorded payments (400 Bad Request).
   - Safe disassociation of `document_id` when deleting document linked to an unpaid PENDING invoice.
   - Standalone document promotion without prior draft invoice.
   - Idempotent repeated promotion calls returning existing invoice without duplicates.
   - Duplicate invoice number rejection (409 Conflict) on promotion.
   - Archived customer rejection (400 Bad Request) on promotion.
   - Human correction domain guards (temporal ordering `due_date >= issue_date` and positive total amounts).
   - Pipeline re-upload content deduplication without duplicate records.
   - OCR provider runtime exception handling transitioning document to `FAILED`.
   - Document listing query filters (`ocr_status`, `has_invoice`).

### 4.3 Code Coverage Report for Invoice Module

```text
Name                                                 Stmts   Miss  Cover   Missing
----------------------------------------------------------------------------------
app\modules\invoice\application\pipeline.py             41      0   100%
app\modules\invoice\application\ports.py               224     39    83%   41, 46, 51, 66, 78, 89, 104, 114, 128, 134, 183, 234, 271-273, 281-283, 291, 373, 397-398, 403-406, 413, 418-421, 440-447, 451
app\modules\invoice\application\use_cases.py           594     84    86%   99, 119, 204-210, 235-239, 250, 309, 311, 330, 333, 335, 344, 378, 413-414, 426-428, 432-438, 447-448, 461-463, 486-487, 516-517, 664, 669, 695, 835-842, 849-862, 868-870, 874, 924-928, 934, 942, 944, 946, 952, 964, 968, 970, 976, 1012, 1026, 1036, 1038, 1040, 1042, 1063, 1114
app\modules\invoice\domain\entities.py                 172      4    98%   77, 102, 112, 144
app\modules\invoice\domain\extraction_rules.py         217     23    89%   187, 199-200, 223, 260-261, 285-286, 290-293, 305, 323, 343, 354, 358, 397-399, 519-520, 542
app\modules\invoice\domain\file_security.py             44      1    98%   38
app\modules\invoice\infrastructure\models.py            45      0   100%
app\modules\invoice\infrastructure\repositories.py     151     17    89%   120-121, 149, 164, 182, 201-209, 309, 343, 363, 369, 371, 373, 375, 377, 379, 401
app\modules\invoice\presentation\router.py             168      5    97%   134, 198-199, 445-446
app\modules\invoice\presentation\schemas.py            137      0   100%
app\modules\invoice\presentation\upload_utils.py        28      5    82%   31-36, 48-49
----------------------------------------------------------------------------------
TOTAL                                                 1821    178    90%
```
All components exceed the 80% coverage threshold, with the entire Invoice module achieving 90% overall coverage and the Document Processing Pipeline achieving 100% coverage.

---

## 5. Security & Invariant Review

| Security Dimension | Defense Mechanism | Verification |
|---|---|---|
| **MIME Spoofing** | Magic bytes verification via `FileSecurityValidator` comparing signature with file extension. | Verified in `test_ocr_security.py` |
| **Executable / Polyglots** | Scans leading 512 bytes for MZ, ELF, shell shebang, `<script>`, `<?php`. | Verified in `test_ocr_security.py` |
| **Path Traversal** | Path resolution checked via `.is_relative_to(tenant_dir)`; null bytes rejected. | Verified in `test_ocr_security.py` |
| **Denial of Service (DoS)** | Pre-flight `Content-Length` check and chunked 64KB read stream with early abort. | Verified in `upload_utils.py` |
| **Formula Injection** | Rejects monetary strings starting with `=`, `@`, `\t`, `\r`. | Verified in `test_ocr_parsing_and_validation.py` |
| **Browser Script Execution** | Document streaming headers: `X-Content-Type-Options: nosniff`, CSP `default-src 'none'`, `no-store`. | Verified in `test_ocr_document_integration.py` |
| **Tenant Isolation (IDOR)** | Authenticated company context enforced in repository layer; cross-tenant calls return 404. | Verified in `test_ocr_tenant_security.py` |
| **Zero Silent Modifications** | Mismatches between subtotal+tax and total are recorded as errors without altering values. | Verified in `test_ocr_parsing_and_validation.py` |
| **Payment Protection** | Documents linked to invoices with recorded payments cannot be deleted. | Verified in `test_ocr_document_integration.py` |

---

## 6. Known Limitations & Phase Handoff

1. **Optical Character Recognition Engines**:
   - The platform includes production-ready heuristics and pluggable adapter abstractions (`CloudVisionOCRAdapter`, `MultimodalAIOCRAdapter`).
   - When deploying with cloud OCR providers (AWS Textract, Google Document AI, Azure Form Recognizer, or Gemini Multimodal Vision), the adapter boundary in `app/modules/invoice/application/ports.py` only requires setting the respective cloud API credentials.
2. **Background Async Worker Queue**:
   - Currently, document processing is invoked synchronously within use cases or via fast local heuristics.
   - For multi-page high-volume background processing, `InvoiceDocumentRepository.acquire_for_processing()` provides the atomic state transition lock ready for background queue workers (e.g. Celery / ARQ) in future operational scaling phases.

---

## 7. Final Phase Status

**Phase 12 (OCR & Document Processing) is hereby certified as COMPLETE, FULLY TESTED, AND FROZEN.**
