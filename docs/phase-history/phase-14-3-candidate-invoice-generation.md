# Phase 13.2 — Candidate Invoice Generation: Phase History & Forensic Certification

## 1. Executive Summary

| Phase Dimension | Specification & Certification Status |
| :--- | :--- |
| **Phase Target** | **Phase 13.2 — Candidate Invoice Generation** |
| **Parent Module** | `backend/app/modules/reconciliation/` |
| **Architecture Pattern** | Modular Monolith + Clean Architecture + DDD Lite |
| **Upstream Contract** | Phase 13.1 Customer Identification Foundation (**FROZEN**) |
| **Downstream Consumer** | Phase 13.3 Multi-Signal Matching & Combinatorial Scoring Engine |
| **Financial State Mutation Risk** | **0.00%** (Strictly Read-Only Evaluation, 0 Session Mutations) |
| **Database Migrations** | **0 New Migrations** (Leverages existing Phase 11 composite indexes) |
| **Test Verification** | **231 Passed**, 0 Failed, 1 Warning (99.07s runtime) |
| **Statement Coverage** | **93% Total Platform Coverage** (4,418 statements across platform) |
| **Phase Status** | **IMPLEMENTED & VERIFIED** |

---

## 2. Scope Boundaries & Explicit Non-Goals

1. **Zero Invoice Matching (1:1, 1:N, N:1, M:N)**:
   Combinatorial matching, subset-sum search, and settlement proposals are strictly deferred to Phase 13.3+.
2. **Zero Payment Allocation / State Mutation**:
   Phase 13.2 never mutates `invoice.paid_amount`, `invoice.outstanding_amount`, `invoice.status`, `payment.allocated_amount`, `payment.unallocated_amount`, or `payment.status`.
3. **Zero LLM Authority**:
   100% deterministic rules, regex token boundaries, and exact `Decimal` monetary arithmetic.
4. **Retrieval Priority vs Reconciliation Confidence Separation**:
   The `retrieval_priority` (0.0 to 100.0) computed in Phase 13.2 is an ordering heuristic used solely to bound and rank open invoice candidates into a manageable universe ($K \le 30$). It is **explicitly NOT a reconciliation match confidence score**.
5. **Strict Currency Boundary**:
   Multi-currency auto-reconciliation is strictly prohibited in MVP. Mismatching currencies yield 0 candidates with explicit `CURRENCY_MISMATCH` diagnostics.

---

## 3. Architecture & Layered Implementation

```text
HTTP Client
    │
    ▼
FastAPI Router (`POST /api/v1/reconciliation/candidates/{payment_id}`)
    │  - JWT Authentication (`current_user`)
    │  - Fail-Closed IDOR Check (404 on Cross-Tenant)
    │
    ▼
Use Case (`GenerateCandidateInvoicesUseCase`)
    │  - Checks Payment Invariants (positive amount, unreconciled)
    │  - Resolves Customer Context (Phase 13.1 or Manual Override)
    │
    ├──► `PaymentLookupPort` ──► `SQLAlchemyPaymentLookupAdapter`
    ├──► `CustomerLookupPort` ──► `SQLAlchemyCustomerLookupAdapter`
    ├──► `InvoiceLookupPort` ──► `SQLAlchemyInvoiceLookupAdapter`
    │                                │ (Queries `invoices` with composite index
    │                                │  `idx_invoices_company_customer`)
    ▼
Domain Engine (`CandidateInvoiceRuleEngine`)
    │  - Evaluates Evidence Signals per Invoice
    │  - Applies 5-Key Deterministic Sorting
    │  - Bounds Candidate Universe to Limit ($K \le 30$)
    │  - Generates Audit Truncation Metadata
    ▼
`CandidateInvoiceUniverse` (Immutable Domain Aggregate)
```

---

## 4. Evidence Signals & Scoring Taxonomy

| Evidence Signal | Mathematical / Logical Trigger | Base Weight | Signal Strength |
| :--- | :--- | :--- | :--- |
| `INVOICE_NUMBER_MATCH` | Clean alphanumeric token match with word boundaries `\b[A-Za-z0-9][A-Za-z0-9\-_/]{2,}\b` in payment narration or reference | +40.0 | `STRONG` |
| `EXACT_AMOUNT_MATCH` | `payment.amount == invoice.outstanding_amount` | +35.0 | `STRONG` |
| `EXACT_ORIGINAL_AMOUNT_MATCH` | `payment.amount == invoice.total_amount` (when `paid_amount > 0`) | +25.0 | `MEDIUM` |
| `PARTIAL_AMOUNT_COMPATIBLE` | `payment.amount < invoice.outstanding_amount` | +15.0 | `MEDIUM` |
| `DATE_RELEVANCE` | Causality (`issue_date <= payment_date`, +10) + Due Date Proximity ($\le 7$ days: +10, $\le 30$ days: +5, $> 30$ days: +2) | Up to +20.0 | `STRONG` / `MEDIUM` / `WEAK` |

### 5-Key Deterministic Sorting Specification
To guarantee 100% reproducible sorting across databases and operating systems without floating point drift:
1. `retrieval_priority` DESC (with micro-aging tie breaker $\min(0.99, \text{days\_overdue} / 1000)$ favoring older unpaid debt)
2. `is_exact_amount_match` DESC
3. `is_reference_match` DESC
4. `due_date` ASC (FIFO preference, older due date first)
5. `invoice_id` ASC (Canonical UUID integer tie-breaker)

---

## 5. Test Verification Matrix

### New Phase 13.2 Test Files
1. `backend/tests/unit/test_candidate_invoice_entities.py` (7 tests):
   - Positive balance invariants, balance conservation (`paid + outstanding == total`), status restriction (`PENDING` or `PARTIALLY_PAID`), priority bounds (`0.0 <= p <= 100.0`), dictionary serialization.
2. `backend/tests/unit/test_candidate_invoice_rules.py` (11 tests):
   - Exact outstanding amount, exact original gross amount, partial payment compatibility, reference extraction from narration/reference, date relevance, deterministic ranking, bounding & truncation, currency filtering, invalid invoice exclusions, 100-iteration determinism.
3. `backend/tests/integration/test_candidate_invoice_api.py` (6 tests):
   - End-to-end endpoint `POST /candidates/{payment_id}`, auto-identified customer, unresolved customer, explicit customer override, currency mismatch, cross-tenant IDOR protection (404 Fail-closed), zero financial state mutation assertion.
4. `backend/tests/integration/test_candidate_invoice_performance.py` (1 test):
   - Scalability benchmark on customer with 100 open invoices: verified sub-500ms execution, exact ranking of amount match, and strictly monotonic ranks 1..30.

### Full Test Suite Results
- Total Tests: **231 passed**, 0 failed, 1 warning in 99.07s
- Coverage: **93% total platform statement coverage** across 4,418 statements
- Reconciliation Module Coverage:
  - `candidate_invoices.py`: 100%
  - `invoice_rules.py`: 98%
  - `adapters.py`: 98%
  - `router.py`: 100%
  - `schemas.py`: 99%
  - `use_cases.py`: 93%
