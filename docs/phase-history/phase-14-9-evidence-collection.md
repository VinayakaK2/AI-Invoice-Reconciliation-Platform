# Phase 14.9 — Evidence Collection: Phase History & Forensic Certification

## 1. Executive Summary

| Phase Dimension | Specification & Certification Status |
| :--- | :--- |
| **Phase Target** | **Phase 14.9 — Reconciliation Engine: Evidence Collection** |
| **Parent Module** | `backend/app/modules/reconciliation/` |
| **Architecture Pattern** | Modular Monolith + Clean Architecture + DDD Lite |
| **Upstream Contracts** | Phase 14.1 Payment Intake, Phase 14.2 Payer Identification, Phase 14.3 Candidate Invoices, Phase 14.4 Candidate Filtering, Phase 14.5 Exact Matching, Phase 14.6 Partial Matching, Phase 14.7 Multi-Invoice Matching, Phase 14.8 Combination Matching (**ALL FROZEN**) |
| **Downstream Consumers** | Phase 14.10 Cross-Currency Matching, Phase 14.11 Scoring Engine, Phase 14.12 Confidence Calibration, Phase 15 Review Center |
| **Observed Financial Mutation** | **NONE** (Strictly Read-Only Evaluation, 0 DB Writes, `len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`) |
| **Database Migrations** | **0 New Migrations** (Stateless in-memory domain evaluation) |
| **Phase 14.9 Tests** | **15 Automated Tests** (9 unit tests, 6 integration/security tests, 100% Pass Rate) |
| **Platform Total Suite** | **501 Total Tests Passed**, 0 Failures (Runtime: 136.63s) |
| **Phase Status** | **FROZEN** |

---

## 2. Scope Boundaries & Canonical Domain Invariants

1. **Pure Evidence Collection Layer**:
   Phase 14.9 converts raw reconciliation facts, signals, and candidate relations into an objective, structured classification without making financial decisions or calculating scores.
2. **Strict Four-Tier Classification**:
   - `DIRECT`: High-fidelity, explicit matches such as registered bank account equality, exact invoice number references in payment text/narration, and exact balance equality.
   - `SUPPORTING`: Contextual signals reinforcing a potential match, such as customer name token overlap, known alias matching, UTR reference presence, partial amount compatibility, date causality (payment on/after invoice issue date), and date proximity (payment within 30 days of due date).
   - `MISSING`: Factual absence of a signal (e.g. empty payment reference, missing UTR, no registered bank account, payment date distant from due date) without fabricating data or assuming absence means negation.
   - `CONFLICTING`: Contradictory evidence indicating mismatch, such as payment account registered to a different active customer, narration explicitly referencing an unrelated invoice, payment date strictly preceding invoice issue date (non-causal), or currency mismatch.
3. **Separation of Concerns**:
   - Evidence Collection $\neq$ Matching $\neq$ Scoring $\neq$ Confidence $\neq$ Approval $\neq$ Mutation.
   - Zero composite scoring (`match_score` deferred strictly to Phase 14.11).
   - Zero confidence calibration (`confidence_score` deferred strictly to Phase 14.12).
   - Zero approval / financial state mutation.
4. **Zero Financial Mutation**:
   Strictly read-only evaluation. Verified via SQLAlchemy session dirty checks (`len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`).
5. **Exact Decimal Precision & Serialized Determinism**:
   All monetary amounts and balance calculations use exact Python `Decimal`. Zero floats.
   - **Remediation 1 (Serialized Determinism)**: `EvidenceCollectionResult.to_deterministic_payload()` separates non-deterministic evaluation metadata (`collected_at`) from the core evidence payload. 100-run permutation test proves exact bit-for-bit equivalence across randomized candidate orders.
   - Candidate evidence bundles are sorted deterministically by `(invoice_number, str(invoice_id))` in the domain engine.
6. **Authoritative Cross-Tenant Candidate Defense**:
   - **Remediation 2 (Tenant-Boundary Defense)**: Domain engine `EvidenceCollectionEngine.evaluate()` and application service `EvidenceCollectionUseCase.execute()` enforce explicit candidate invoice ownership invariants (`cand.company_id == payment.company_id`). Any cross-tenant candidate injected into the engine raises `DomainError` / `ForbiddenError`, closing any reliance on repository-only filtering.
7. **Multi-Tenant Fail-Closed Security**:
   FastAPI endpoint `POST /api/v1/reconciliation/evidence-collection/{payment_id}` enforces JWT tenant context (`company_id`). Cross-tenant access to payments or customer context overrides returns HTTP 404. Unauthenticated calls return HTTP 401.

---

## 3. Architecture & Dataflow

```text
HTTP Client (FastAPI Request)
    │
    ▼
FastAPI Router (`POST /api/v1/reconciliation/evidence-collection/{payment_id}`)
    │  - JWT Bearer Authentication (`current_user`)
    │  - Tenant Isolation: Scopes payment & customer to `current_user.company_id`
    │  - Fail-Closed IDOR Defense: HTTP 404 on cross-tenant payment or customer override
    │
    ▼
Use Case (`EvidenceCollectionUseCase` / `BatchEvidenceCollectionUseCase`)
    │  - Validates Payment Intake Context (Phase 14.1)
    │  - Resolves Candidate Invoices (Phase 14.3 / 14.4)
    │  - Validates candidate invoice tenant ownership against authenticated company_id
    │  - Resolves Payer / Customer Context (Phase 14.2)
    │
    ▼
Domain Engine (`EvidenceCollectionEngine`)
    │  - Enforces CandidateInvoice tenant boundary invariant
    │  - collect_payment_evidence(payment, customer, all_customers)
    │  - collect_candidate_evidence(payment, candidate, payment_evidence, customer)
    │  - Enforces deterministic bundle total order
    │  - Emits EvidenceCollectionResult with full Direct/Supporting/Missing/Conflicting breakdown
    │
    ▼
Presentation Layer (`EvidenceCollectionResponse`)
    │  - Returns structured, auditable evidence item breakdown
```

---

## 4. Verification & Test Evidence

- **Unit Suite (`tests/unit/test_evidence_collection_rules.py`)**:
  - `test_direct_evidence_classification`: Proves account match, invoice number in narration, and exact amount are classified as `DIRECT`.
  - `test_supporting_evidence_classification`: Proves name token overlap, partial amount, date causality, and date proximity are classified as `SUPPORTING`.
  - `test_missing_evidence_classification`: Proves empty reference, absent UTR, and unprovided account are classified as `MISSING` without data fabrication.
  - `test_conflicting_evidence_classification_other_customer_account`: Proves account registered to other customer is classified as `CONFLICTING`.
  - `test_conflicting_evidence_different_invoice_referenced`: Proves narration referencing a different invoice emits `CONFLICTING` on candidate.
  - `test_conflicting_evidence_non_causal_date`: Proves payment prior to invoice issue date emits `CONFLICTING`.
  - `test_100_run_permutation_determinism`: Proves complete serialized deterministic payload (`to_deterministic_payload`) is bit-for-bit identical across 100 random candidate permutations, separating `collected_at` timestamp.
  - `test_adversarial_cross_tenant_candidate_rejected`: Proves candidate invoice belonging to a different tenant is rejected with `DomainError` at domain engine boundary.
  - `test_matching_tenant_candidate_accepted`: Proves matching tenant candidate evaluates successfully.
- **Integration & Security Suite (`tests/integration/test_evidence_collection_api.py`)**:
  - `test_evidence_collection_api_e2e`: Proves end-to-end API response conforms to schema and classifies items correctly.
  - `test_evidence_collection_zero_financial_mutation`: Proves `len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`.
  - `test_evidence_collection_cross_tenant_payment_idor`: Proves cross-tenant payment request fails with HTTP 404.
  - `test_evidence_collection_cross_tenant_customer_override_idor`: Proves cross-tenant customer override fails with HTTP 404.
  - `test_evidence_collection_unauthenticated_rejected`: Proves missing token fails with HTTP 401.
  - `test_evidence_collection_use_case_adversarial_cross_tenant_candidate`: Proves adversarial caller injecting a cross-tenant candidate at use-case boundary is rejected with `ForbiddenError`.
- **Full Regression Test Suite**:
  - 501 tests passing across the entire repository. Zero failures. Zero regressions.

---

## 5. Certification Status

Phase 14.9 Evidence Collection satisfies all requirements of clean architecture, deterministic financial safety, zero mutation, multi-tenant isolation, serialized payload determinism, and four-tier evidence classification. Remediation items are complete. Current status is **FROZEN**.
