# Phase 14.12 — Confidence Engine Specification & Verification Report

## Status: FROZEN

---

## 1. Executive Summary & Core Responsibility

Phase 14.12 introduces the **Deterministic Confidence Engine** to the AI Invoice Reconciliation Platform.
In strict alignment with the core architectural principle:

$$\text{Evidence} \neq \text{Scoring} \neq \text{Confidence} \neq \text{Decision} \neq \text{Approval} \neq \text{Financial Mutation}$$

The Confidence Engine takes the already-established candidate scores from Phase 14.11 (`MatchingScoringResult`) and the canonical normalized evidence from Phase 14.10 (`EvidenceNormalizationResult`), and computes an objective, reproducible confidence assessment $[0.00, 100.00]$ along with an explainable qualitative rating (`ConfidenceLevel`) and transparent adjustment factors (`ConfidenceAdjustmentFactor`).

### Invariant & Boundary Guarantees
1. **Confidence is Aggregation & Evaluation, NOT a Financial Decision**: The Confidence Engine does NOT recommend `AUTO_ELIGIBLE` or `MATCH_SUGGESTED`, does NOT route to human review, and does NOT generate decision objects (these belong strictly to Phase 14.13).
2. **Observed Financial Mutation: NONE**:
   - Database writes: **0**
   - Session mutations: `len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`
   - Invoice / Payment status transitions: **NONE**
3. **Deterministic Anchoring & Anti-Double Counting**: Candidates anchor directly to their exact Phase 14.11 base match score ($\Delta = 0.00$). Base evidence weights (invoice reference, amount match, bank account, date proximity) are NOT re-added or double-counted.
4. **Zero Invented Penalties, Ceilings, or Deductions**: Remediation removed unauthorized 50% conflict deductions, 40.00 conflict ceilings, and 15.00 point ambiguity penalties.
5. **Factual Conflict Observation**: Conflicting evidence is surfaced factually (`has_conflicting_evidence = True`, `ConfidenceFactorType.CONFLICT_OBSERVED`, $\Delta = 0.00$) rather than applying synthetic score subtractions.
6. **Decoupled Classification**: `VERY_HIGH` is a pure confidence band ($\ge 90.00$) and does NOT require direct counterparty identifier grounding (Rule P-1). Direct identifier grounding is a separate Phase 14.13 safety condition for `AUTO_ELIGIBLE`, not a confidence classification condition.
7. **Empirical Calibration Boundary**: Confidence scores are explicitly marked `is_statistically_calibrated=False`, preventing misleading claims of Bayesian or ML posterior probability.
8. **Pure Decimal Arithmetic**: All computations use `Decimal` with `ROUND_HALF_UP` bounded to $[0.00, 100.00]$, avoiding binary floating-point rounding errors.
9. **Canonical Versioned Policy**: Formalized as `ConfidencePolicy` (`CANONICAL_CONFIDENCE_POLICY`).

---

## 2. Confidence Level Mapping

The qualitative mapping is strictly bounded and semantic:

| Qualitative Level | Score Range | Description |
|---|---|---|
| `VERY_HIGH` | $[90.00, 100.00]$ | Highest tier deterministic candidate support. |
| `HIGH` | $[70.00, 89.99]$ | Strong supporting evidence. |
| `MEDIUM` | $[40.00, 69.99]$ | Moderate supporting evidence. |
| `LOW` | $[0.01, 39.99]$ | Weak circumstantial evidence. |
| `NONE` | $0.00$ | No matching signals or zero base score. |

---

## 3. Architecture & Code Structure

```text
backend/app/modules/reconciliation/
├── domain/
│   ├── confidence_engine.py      # Core pure domain models, enums, config, and ConfidenceEngine
│   └── __init__.py               # Domain exports
├── application/
│   └── use_cases.py              # ConfidenceAssessmentUseCase & BatchConfidenceAssessmentUseCase
└── presentation/
    ├── schemas.py                # Pydantic DTOs for requests and responses
    └── router.py                 # POST /reconciliation/confidence/{payment_id} & /batch
```

### Domain Models (`confidence_engine.py`)
- `ConfidenceLevel`: Enum (`VERY_HIGH`, `HIGH`, `MEDIUM`, `LOW`, `NONE`).
- `ConfidenceFactorType`: Enum identifying exact reasons for confidence assessment provenance (`BASE_MATCH_SCORE`, `DIRECT_IDENTIFIER_GROUNDING`, `CONFLICT_OBSERVED`, `INCOMPLETE_EVIDENCE_OBSERVED`).
- `ConfidenceAdjustmentFactor`: Transparent explanation DTO with `factor_type`, `delta`, and `description`.
- `ConfidencePolicy`: Canonical immutable threshold configuration (`CANONICAL_CONFIDENCE_POLICY`) with fail-closed validation.
- `CandidateConfidenceAssessment`: Evaluated candidate with `invoice_id`, `invoice_number`, `base_match_score`, `confidence_score`, `confidence_level`, `has_direct_evidence`, `has_conflicting_evidence`, `is_statistically_calibrated=False`, and adjustment audit trail.
- `ConfidenceEngineResult`: Container for all evaluated candidates, sorted deterministically by `(-confidence_score, -base_match_score, invoice_number, str(invoice_id))`.

---

## 4. Test Verification Summary

### Targeted Test Suite
- Unit Tests: `tests/unit/test_confidence_engine_rules.py` (24 passed)
- Integration, IDOR & Benchmark Tests: `tests/integration/test_confidence_engine_api.py` (8 passed)
- **Subtotal Phase 14.12 Tests**: **32 passed**

### Targeted Suite Output
```text
============================= test session starts =============================
platform win32 -- Python 3.11.8, pytest-9.1.1, pluggy-1.6.0
collected 32 items

tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_canonical_confidence_presentation_bands[0.00-NONE] PASSED [  3%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_canonical_confidence_presentation_bands[1.00-LOW] PASSED [  6%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_canonical_confidence_presentation_bands[39.00-LOW] PASSED [  9%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_canonical_confidence_presentation_bands[39.99-LOW] PASSED [ 12%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_canonical_confidence_presentation_bands[40.00-MEDIUM] PASSED [ 15%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_canonical_confidence_presentation_bands[69.00-MEDIUM] PASSED [ 18%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_canonical_confidence_presentation_bands[69.99-MEDIUM] PASSED [ 21%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_canonical_confidence_presentation_bands[70.00-HIGH] PASSED [ 25%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_canonical_confidence_presentation_bands[89.00-HIGH] PASSED [ 28%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_canonical_confidence_presentation_bands[89.99-HIGH] PASSED [ 31%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_canonical_confidence_presentation_bands[90.00-VERY_HIGH] PASSED [ 34%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_canonical_confidence_presentation_bands[95.00-VERY_HIGH] PASSED [ 37%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_canonical_confidence_presentation_bands[100.00-VERY_HIGH] PASSED [ 40%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_very_high_does_not_require_direct_identifier PASSED [ 43%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_anti_penalty_conflict_does_not_reduce_score PASSED [ 46%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_anti_bonus_direct_identifier_does_not_add_points PASSED [ 50%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_multi_invoice_match_does_not_receive_penalty PASSED [ 53%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_incomplete_evidence_surfaced_factually_without_penalty PASSED [ 56%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_decision_separation_no_decision_fields PASSED [ 59%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_statistical_calibration_explicitly_false PASSED [ 62%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_100_run_permutation_determinism PASSED [ 65%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_canonical_schema_alias_properties PASSED [ 68%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_deterministic_ranking_order_ties PASSED [ 71%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineCanonicalRules::test_out_of_bounds_confidence_score_raises_domain_error PASSED [ 75%]
tests/integration/test_confidence_engine_api.py::test_confidence_api_e2e PASSED [ 78%]
tests/integration/test_confidence_engine_api.py::test_confidence_zero_financial_mutation PASSED [ 81%]
tests/integration/test_confidence_engine_api.py::test_confidence_cross_tenant_payment_idor PASSED [ 84%]
tests/integration/test_confidence_engine_api.py::test_confidence_cross_tenant_customer_override_idor PASSED [ 87%]
tests/integration/test_confidence_engine_api.py::test_confidence_unauthenticated_rejected PASSED [ 90%]
tests/integration/test_confidence_engine_api.py::test_batch_confidence_endpoint PASSED [ 93%]
tests/integration/test_confidence_engine_api.py::test_batch_confidence_exceeds_max_limit_rejected PASSED [ 96%]
tests/integration/test_confidence_engine_api.py::test_confidence_performance_benchmark PASSED [100%]

============================= 32 passed in 4.49s ==============================
```

### Full Platform Regression Suite Output
```text
============================= test session starts =============================
platform win32 -- Python 3.11.8, pytest-9.1.1, pluggy-1.6.0
collected 573 items

........................................................................ [ 12%]
........................................................................ [ 25%]
........................................................................ [ 37%]
........................................................................ [ 50%]
........................................................................ [ 62%]
........................................................................ [ 75%]
........................................................................ [ 87%]
.....................................................................    [100%]
573 passed in 181.41s (0:03:01)
```

---

## 5. Security & Tenant Isolation Audit
- **Tenant Isolation**: Both payment retrieval and customer override resolution strictly mandate `company_id`. Mismatched requests fail closed with HTTP 404 (`PAYMENT_NOT_FOUND` / `CUSTOMER_NOT_FOUND`).
- **Internal Cross-Tenant Integrity**: The engine asserts `candidate_scores.company_id == normalized_evidence.company_id == payment_context.company_id`. Any discrepancy raises `DomainError` immediately.
- **Data Protection**: Bank account numbers and PII in evidence are masked (e.g., `********5544`) in all outputs.
- **Zero Financial Mutation**: Confirmed with active checks against `len(db_session.dirty) == 0`.

---

## 6. Final Certification
- **Independent repository verification:** CERTIFIED
- **PR:** #13 — merged
- **Merge commit:** `24914372be5a138c241523db7c69e1343ec8e213`
- **Final Phase Status:** **FROZEN**
- **Next permitted phase:** Phase 14.13 — Decision Engine

---

## 7. Non-Goals for Phase 14.12
- Decision engine policy (`AUTO_ELIGIBLE`, `MATCH_SUGGESTED`, `NEEDS_REVIEW`) $\rightarrow$ strictly deferred to Phase 14.13.
- Decision objects and human review workflow routing $\rightarrow$ Phase 14.14.
- Invoice and Payment ledger allocation, settlement, or balance updates $\rightarrow$ downstream financial state mutation phases.
