# Phase 14.8 — Combination Matching: Phase History & Forensic Certification

## 1. Executive Summary

| Phase Dimension | Specification & Certification Status |
| :--- | :--- |
| **Phase Target** | **Phase 14.8 — Reconciliation Engine: Combination Matching** |
| **Parent Module** | `backend/app/modules/reconciliation/` |
| **Architecture Pattern** | Modular Monolith + Clean Architecture + DDD Lite |
| **Upstream Contracts** | Phase 14.1 Payment Intake, Phase 14.2 Payer Identification, Phase 14.3 Candidate Invoices, Phase 14.4 Candidate Filtering, Phase 14.5 Exact Matching, Phase 14.6 Partial Matching, Phase 14.7 Multi-Invoice Matching (**ALL FROZEN**) |
| **Downstream Consumers** | Phase 14.9 Cross-Currency Matching, Phase 14.11 Scoring Engine, Phase 15 Review Center |
| **Observed Financial Mutation** | **NONE** (Strictly Read-Only In-Memory Evaluation, 0 DB Writes, `len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`) |
| **Database Migrations** | **0 New Migrations** (Stateless in-memory domain evaluation) |
| **New Phase 14.8 Tests** | **32 New Automated Tests** across 4 test files (100% Pass Rate) |
| **Platform Total Suite** | **486 Total Tests Passed**, 0 Failures (Runtime: 108.30s) |
| **Phase Status** | **CERTIFIED, AUDITED, VERIFIED & FROZEN** |

---

## 2. Scope Boundaries & Canonical Domain Invariants

1. **Rule M-2 / M-3 Canonical Scope**:
   Phase 14.8 evaluates combinations of outstanding invoices ($2 \le k \le 4$, candidate pool $n \le 30$) summing to the payment effective amount.
2. **Rule M-3 FIFO Aging Prioritization**:
   When competing subsets of invoices sum to the exact same payment amount (e.g. ₹35,000 payment against candidates {10k, 25k, 15k, 8k, 12k}), the combination settling the oldest outstanding invoices by `due_date` receives priority:
   - Subset A: 10k (due May 1) + 25k (due May 15) $\implies$ oldest due date: 2026-05-01
   - Subset B: 15k (due June 1) + 8k (due June 10) + 12k (due June 20) $\implies$ oldest due date: 2026-06-01
   - Engine classifies outcome as `PRIORITIZED_COMBINATION_MATCH` (`FIFO_AGING_PRIORITIZED`), flags `requires_review = True`, and attaches structured evidence signal `FIFO_OLDEST_INVOICE_PRIORITY`.
3. **Reference Match Precedence**:
   Explicit narration token matches for candidate invoice numbers take priority over the default FIFO aging heuristic. If narration explicitly matches Subset B invoices, Subset B is prioritized with `REFERENCE_PRIORITIZED`.
4. **Preservation of Strict Ambiguity & Incomplete Universe Defense**:
   - When competing combinations have identical due dates and reference matches (or when FIFO aging is disabled via criteria toggle), the engine preserves unresolvable ambiguity (`AMBIGUOUS_COMBINATION_MATCH`, `MULTIPLE_COMBINATIONS_UNRESOLVED`, `prioritized_combination = None`).
   - **Finding A Remediation**: When the candidate universe was truncated (`is_truncated=True`), unretrieved invoices could form older or competing subsets. FIFO aging is **strictly overridden by truncation uncertainty**—emitting `AMBIGUOUS_COMBINATION_MATCH` (`TRUNCATED_UNIVERSE_AMBIGUITY`, `prioritized_combination = None`, `requires_review = True`), unless 100% of the invoices in a candidate combination possess an explicit narration reference match.
5. **Zero Financial Mutation (Rule 4)**:
   Evaluates purely in-memory. Zero database mutations. Verified via SQLAlchemy session dirty checks (`len(db.dirty) == 0`).
6. **Hard Bounded Search Depth & Strict Monetary Precision**:
   - **Finding B Remediation**: `amount_tolerance` is strictly locked to `Decimal("0.00")` across domain and presentation schemas (`le=Decimal("0.00")`), preserving absolute monetary conservation.
   - **Finding C Remediation**: Domain `CombinationMatchCriteria` validates $k \in [2, 4]$ (`max_combination_size > 4` raises `DomainError`), aligning domain layer directly with the presentational boundary (`ge=2, le=4`) to eliminate any possibility of combinatorial explosion.
   - Evaluated in $< 1.0\text{s}$ under $n=30, k=4$ combinatorial benchmark (31,900 combinations evaluated in $< 150\text{ms}$).
7. **Zero Composite Scoring & Zero Calibration**:
   Composite scoring deferred to Phase 14.11; confidence calibration deferred to Phase 14.12.

---

## 3. Architecture & Dataflow

```text
HTTP Client (FastAPI Request)
    │
    ▼
FastAPI Router (`POST /api/v1/reconciliation/combination-match/{payment_id}`)
    │  - JWT Bearer Authentication (`current_user`)
    │  - Tenant Isolation: Scopes payment & customer to `current_user.company_id`
    │  - Fail-Closed IDOR Defense: HTTP 404 on cross-tenant payment or customer override
    │  - Schema Validation: Hard-bounds max_combination_size to [2, 4] and tolerance to 0.00 (HTTP 422 if invalid)
    │
    ▼
Use Case (`CombinationMatchUseCase` / `BatchCombinationMatchUseCase`)
    │  - Validates Payment Intake Context (Phase 14.1)
    │  - Resolves Filtered Candidate Universe (Phase 14.4)
    │  - Evaluates Combination Match Rule Engine
    │
    ▼
Domain Engine (`CombinationMatchRuleEngine`)
    │  - Evaluates combinations (k=2, 3, 4) against payment effective amount
    │  - Computes structured evidence signals
    │  - Resolves competing subsets via Rule M-3 FIFO Aging or Narration Reference
    │  - Strictly overrides FIFO aging with ambiguity when universe is truncated
    │  - Flags ambiguity and review required when competing subsets exist
    │
    ▼
Presentation DTO (`CombinationMatchResponse`)
       - Immutable serialized response with full structured audit trail
```

---

## 4. Verification Evidence & Test Summary

- **Unit Suite (`backend/tests/unit/test_combination_matching_rules.py`)**: 17 tests passed (including Finding A truncation defense, tolerance validation, and $k \le 4$ domain validation).
- **Integration API Suite (`backend/tests/integration/test_combination_matching_api.py`)**: 4 tests passed.
- **Tenant Security Suite (`backend/tests/integration/test_combination_matching_tenant_security.py`)**: 4 tests passed.
- **Adversarial & Forensic Suite (`backend/tests/integration/test_combination_matching_adversarial.py`)**: 7 tests passed (including $n=30, k=4$ 31,900-combination benchmark $< 1.0\text{s}$, and tolerance $> 0.00$ rejection).
- **Total Phase 14.8 Suite**: 32 tests passed (100% pass rate).
- **Full Platform Regression Suite**: 486 tests passed, 0 failures, runtime 108.30s.
