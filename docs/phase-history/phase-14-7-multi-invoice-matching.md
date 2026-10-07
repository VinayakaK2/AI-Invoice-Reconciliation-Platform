# Phase 14.7 — Multi-Invoice Matching (1:N): Phase History & Forensic Certification

## 1. Executive Summary

| Phase Dimension | Specification & Certification Status |
| :--- | :--- |
| **Phase Target** | **Phase 14.7 — Reconciliation Engine: Multi-Invoice Matching (1:N)** |
| **Parent Module** | `backend/app/modules/reconciliation/` |
| **Architecture Pattern** | Modular Monolith + Clean Architecture + DDD Lite |
| **Upstream Contracts** | Phase 14.1 Payment Intake, Phase 14.2 Payer Identification, Phase 14.3 Candidate Invoices, Phase 14.4 Candidate Filtering, Phase 14.5 Exact Matching, Phase 14.6 Partial Matching (**ALL FROZEN**) |
| **Downstream Consumers** | Phase 14.8 Combination Matching, Phase 15 Review Center |
| **Observed Financial Mutation** | **NONE** (Strictly Read-Only In-Memory Fact Generation, 0 DB Writes, `len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`) |
| **Database Migrations** | **0 New Migrations** (Stateless in-memory domain evaluation) |
| **New Phase 14.7 Tests** | **34 New Automated Tests** across 4 test files (100% Pass Rate) |
| **Reconciliation Suite** | **210 Total Tests Passed** across reconciliation test suite (0 Failures) |
| **Platform Total Suite** | **454 Total Tests Passed**, 0 Failures (Runtime: 142.77s) |
| **Phase Status** | **CERTIFIED, AUDITED, VERIFIED & FROZEN** |

---

## 2. Scope Boundaries & Non-Goals

1. **Matching Fact Only ($\text{Filtering} \neq \text{Matching} \neq \text{Scoring} \neq \text{Decision} \neq \text{Approval} \neq \text{Mutation}$)**:
   Phase 14.7 establishes an objective, deterministic multi-invoice matching fact/hypothesis (`MULTI_INVOICE_MATCH`, `NO_MULTI_INVOICE_MATCH`, `AMBIGUOUS_MULTI_INVOICE_MATCH`). It does **not** auto-approve, settle, or allocate financial amounts.
2. **Authoritative Outstanding Balance Comparator**:
   Phase 14.7 evaluates:
   $$\sum_{i=1}^k \text{candidate}_i\text{.outstanding\_amount} == \text{payment.effective\_amount}$$
   where $2 \le k \le \text{max\_combination\_size}$ (default 4). It never compares against original invoice totals if prior partial payments exist.
3. **Zero Financial Mutation (Rule 4)**:
   Phase 14.7 is strictly an in-memory evaluator. It makes zero modifications to `invoices`, `payments`, `allocations`, or ledgers. Verified via SQLAlchemy session dirty checks (`len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`).
4. **Multi-Invoice Scope Only (1:N, $k \ge 2$)**:
   - Single invoice exact matches ($k=1$) are rejected as multi-invoice matches and diagnosed as `EXACT_MATCH_DETECTED` (governed by Phase 14.5).
   - Single invoice partial matches ($k=1$) are rejected and diagnosed as `PARTIAL_MATCH_DETECTED` (governed by Phase 14.6).
   - Evaluating and resolving competing subset combinations with aging/FIFO heuristics (Rule M-3) belongs to Phase 14.8.
5. **Zero LLM Authority**:
   100% deterministic rules, exact `Decimal` comparisons, and immutable dataclass outputs. Zero probabilistic inference.
6. **Strict Ambiguity Preservation (Prohibition of Autonomous Disambiguation)**:
   When multiple distinct combinations of candidate invoices sum to the payment amount (`total_combinations_found > 1`), the engine unconditionally emits `AMBIGUOUS_MULTI_INVOICE_MATCH` (`MULTIPLE_MULTI_INVOICE_MATCHES`) with `matched_combination = None` and populates all combinations in `competing_combinations`. Autonomous tie-breaking is strictly prohibited.
7. **Elimination of Premature Phase 14.11 Scoring**:
   Phase 14.7 produces factual matching signals only (`MULTI_INVOICE_SUM_EXACT`, `INVOICE_NUMBER_MATCH`, `CUSTOMER_NAME_MATCH`, `DATE_PROXIMITY_MATCH`). No composite numeric `match_score` is calculated.
8. **Incomplete Universe Bounding Awareness**:
   If the candidate universe was truncated to enforce $K \le 30$ and not all invoices in the single matching combination have explicit reference matches, the engine emits `AMBIGUOUS_MULTI_INVOICE_MATCH` (`TRUNCATED_UNIVERSE_AMBIGUITY`) to guard against unretrieved candidates.

---

## 3. Architecture & Dataflow

```text
HTTP Client (FastAPI Request)
    │
    ▼
FastAPI Router (`POST /api/v1/reconciliation/multi-invoice-match/{payment_id}`)
    │  - JWT Bearer Authentication (`current_user`)
    │  - Tenant Isolation: Scopes payment & customer to `current_user.company_id`
    │  - Fail-Closed IDOR Defense: HTTP 404 on cross-tenant payment or customer
    │
    ▼
Use Case (`MultiInvoiceMatchUseCase`)
    │  - Validates Payment Intake Context (Phase 14.1)
    │  - Resolves Filtered Candidate Universe (Phase 14.4)
    │  - Executes Domain Engine evaluation
    │
    ▼
Domain Engine (`MultiInvoiceMatchRuleEngine`)
    │  - Verifies currency compatibility (Zero FX policy)
    │  - Filters eligible candidates (0 < outstanding_amount < effective_amount)
    │  - Evaluates subset sums for 2 <= k <= max_combination_size (default 4)
    │  - Renders structured evidence signals
    │  - Sorts matching hypotheses deterministically:
    │    (has_ref DESC, ref_count DESC, combo_size ASC, date_diff ASC, inv_numbers ASC, inv_ids ASC)
    │
    ▼
`MultiInvoiceMatchResult` (Immutable Domain Aggregate)
    │  - `status`: `MULTI_INVOICE_MATCH`, `NO_MULTI_INVOICE_MATCH`, `AMBIGUOUS_MULTI_INVOICE_MATCH`
    │  - `matched_combination`: `Optional[MultiInvoiceMatchHypothesis]`
    │  - `competing_combinations`: `List[MultiInvoiceMatchHypothesis]`
    │  - `total_combinations_found`: `int`
    │  - `reason_code`: Machine-readable taxonomy
    │  - `is_universe_truncated`: Bounded universe flag
    │  - `is_deterministic`: `True`
```

---

## 4. Reason Code Taxonomy

| Reason Code | Meaning & Trigger |
| :--- | :--- |
| `MULTI_INVOICE_MATCH_FOUND` | Exactly one combination of 2 to $k$ invoices sums to payment amount without narration reference. |
| `MULTI_INVOICE_WITH_REFERENCE_MATCH` | Exactly one combination of 2 to $k$ invoices sums to payment amount and references invoice numbers. |
| `NO_MULTI_INVOICE_COMBINATION_FOUND` | No combination of 2 to $k$ candidate invoices sums to the payment amount. |
| `MULTIPLE_MULTI_INVOICE_MATCHES` | Two or more distinct combinations sum to payment amount. Strictly flagged for human review. |
| `TRUNCATED_UNIVERSE_AMBIGUITY` | Combination matching payment exists, but candidate universe truncation creates ambiguity risk. |
| `EXACT_MATCH_DETECTED` | A single candidate invoice exactly equals payment amount (governed by Phase 14.5). |
| `PARTIAL_MATCH_DETECTED` | Payment is less than all open candidate invoices (governed by Phase 14.6). |
| `UNDERPAYMENT_DETECTED` | Sum of all eligible candidate invoices is less than payment amount. |
| `PAYMENT_INELIGIBLE` | Payment failed Phase 14.1 intake validation or has non-positive amount. |
| `CUSTOMER_UNRESOLVED` | Payer could not be uniquely identified in Phase 14.2. |
| `NO_RETAINED_CANDIDATES` | Zero candidate invoices remained after Phase 14.4 candidate filtering. |
| `INSUFFICIENT_CANDIDATES` | Fewer than 2 candidate invoices have balance less than payment amount. |
| `CURRENCY_MISMATCH` | Incompatible ISO-4217 currencies between payment and candidates. |

---

## 5. Implementation Files

| Layer | File Path | Responsibilities |
| :--- | :--- | :--- |
| **Domain** | `backend/app/modules/reconciliation/domain/multi_invoice_matching.py` | `MultiInvoiceMatchStatus`, `MultiInvoiceMatchReasonCode`, `MultiInvoiceMatchEvidenceType`, `MultiInvoiceMatchEvidenceSignal`, `MultiInvoiceMatchCriteria`, `MultiInvoiceMatchHypothesis`, `MultiInvoiceMatchResult`, `MultiInvoiceMatchRuleEngine` |
| **Domain Export** | `backend/app/modules/reconciliation/domain/__init__.py` | Exports all Phase 14.7 symbols |
| **Application** | `backend/app/modules/reconciliation/application/use_cases.py` | `MultiInvoiceMatchUseCase`, `BatchMultiInvoiceMatchUseCase` |
| **Presentation (DTOs)** | `backend/app/modules/reconciliation/presentation/schemas.py` | `MultiInvoiceMatchCriteriaRequest`, `MultiInvoiceMatchRequest`, `MultiInvoiceMatchEvidenceSignalResponse`, `MultiInvoiceMatchHypothesisResponse`, `MultiInvoiceMatchResponse`, `BatchMultiInvoiceMatchRequest`, `BatchMultiInvoiceMatchResponse` |
| **Presentation (Router)** | `backend/app/modules/reconciliation/presentation/router.py` | `POST /api/v1/reconciliation/multi-invoice-match/{payment_id}`, `POST /api/v1/reconciliation/multi-invoice-match-batch` |

---

## 6. Test Suite & Forensic Verification Evidence

### 6.1 Unit Tests (`tests/unit/test_multi_invoice_matching_rules.py` — 19 Tests Passed)
- Criteria invariant validation (`max_combination_size` $\in [2, 10]$, non-negative tolerance and proximity days).
- Hypothesis invariant validation ($k \ge 2$, no duplicate invoice IDs, length matching, sum balance == matched amount, zero projected remaining balances).
- Exact 2-invoice match, 3-invoice match, 4-invoice match.
- Bounded combination limit enforcement ($k \le 4$ default, $k=5$ rejected under default and accepted when configured).
- Evidence signals generation (`MULTI_INVOICE_SUM_EXACT`, `INVOICE_NUMBER_MATCH`, `CUSTOMER_NAME_MATCH`, `DATE_PROXIMITY_MATCH`).
- Non-multi-invoice diagnosis (`EXACT_MATCH_DETECTED`, `PARTIAL_MATCH_DETECTED`, `UNDERPAYMENT_DETECTED`).
- Ambiguity preservation across disjoint subsets and different combination sizes.
- Truncated universe ambiguity detection (`TRUNCATED_UNIVERSE_AMBIGUITY`).
- Full reference match override in truncated universe.
- Currency mismatch rejection.
- Upstream status propagation (`CUSTOMER_UNRESOLVED`, `CURRENCY_MISMATCH`).
- 100-run permutation determinism test: Bit-for-bit identical results regardless of candidate shuffle order.
- Micro-penny Decimal precision validation.

### 6.2 Integration API Tests (`tests/integration/test_multi_invoice_matching_api.py` — 6 Tests Passed)
- `test_multi_invoice_match_api_e2e_success`: End-to-end API execution verifying HTTP 200, correct DTO mapping, and matched combination.
- `test_multi_invoice_match_zero_financial_state_mutation`: 5 consecutive API calls verify invoice and payment DB states remain 100% unmutated (`PENDING`, `UNRECONCILED`, unchanged balances).
- `test_multi_invoice_match_api_ambiguous_match`: Multiple combinations return `AMBIGUOUS_MULTI_INVOICE_MATCH` with competing combinations and `matched_combination = None`.
- `test_multi_invoice_match_api_no_match_single_exact`: Single exact candidate diagnosed as `EXACT_MATCH_DETECTED`.
- `test_multi_invoice_match_api_batch`: Bounded batch execution over multiple unreconciled payments.
- `test_multi_invoice_match_api_unresolved_customer`: Payment with unknown counterparty returns `CUSTOMER_UNRESOLVED`.

### 6.3 Tenant Security Tests (`tests/integration/test_multi_invoice_matching_tenant_security.py` — 4 Tests Passed)
- `test_multi_invoice_match_cross_tenant_payment_idor`: Company B cannot evaluate Company A's payment (HTTP 404 Fail-closed).
- `test_multi_invoice_match_cross_tenant_customer_override_idor`: Company A cannot inject Company B's customer ID (HTTP 404 Fail-closed).
- `test_multi_invoice_match_cross_tenant_invoice_bleed_immunity`: Company A's invoices are completely invisible to Company B even with identical counterparty and bank details.
- `test_multi_invoice_match_unauthenticated_rejected`: HTTP 401 Unauthorized on unauthenticated requests.

### 6.4 Adversarial Tests (`tests/integration/test_multi_invoice_matching_adversarial.py` — 5 Tests Passed)
- `test_adversarial_ghost_debt_paid_invoice_cannot_match`: Fully paid invoices cannot participate in multi-invoice matches.
- `test_adversarial_sqlalchemy_session_zero_dirty_objects`: Live assertion that `len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`.
- `test_adversarial_micro_penny_floating_point_defense`: Micro-penny 2-decimal precision (49.99 + 50.01 == 100.00) without float drift.
- `test_adversarial_combinatorial_candidate_flooding_performance`: 20 candidate invoices evaluated in < 1.0s.
- `test_adversarial_max_combination_size_bounded_to_k4_http_422`: External callers attempting $k > 4$ (e.g. $k=5, 10$) or $k < 2$ are rejected with HTTP 422 Unprocessable Entity, enforcing Rule M-2 bounded combinatorial depth at API boundary.

---

## 7. Forensic Verification & Freeze Status

```text
Requirements Verification:     [PASS] Strict 1:N multi-invoice matching (2 <= k <= 4)
Architecture Boundaries:       [PASS] Zero mutation, DDD Lite, Modular Monolith
Security & Tenant Isolation:   [PASS] 404 IDOR fail-closed, cross-tenant bleed immune, API k<=4 hard-bounded
Determinism:                   [PASS] 100-run shuffle test verified bit-for-bit identical
Decimal Precision:             [PASS] Strict Python Decimal, zero binary floats
Test Coverage:                 [PASS] 34/34 Phase 14.7 tests passed, 454/454 platform tests passed
Status:                        FROZEN
```
