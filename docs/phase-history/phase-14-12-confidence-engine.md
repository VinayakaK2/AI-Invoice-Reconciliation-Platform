# Phase 14.12 — Confidence Engine Specification & Verification Report

## Status: IMPLEMENTED — READY FOR INDEPENDENT VERIFICATION

---

## 1. Executive Summary & Core Responsibility

Phase 14.12 introduces the **Deterministic Confidence Engine** to the AI Invoice Reconciliation Platform.
In strict alignment with the core architectural principle:

$$\text{Evidence} \neq \text{Scoring} \neq \text{Confidence} \neq \text{Decision} \neq \text{Approval} \neq \text{Financial Mutation}$$

The Confidence Engine takes the already-established candidate scores from Phase 14.11 (`MatchingScoringResult`) and the canonical normalized evidence from Phase 14.10 (`EvidenceNormalizationResult`), and computes an objective, reproducible confidence assessment $[0.00, 100.00]$ along with an explainable qualitative rating (`ConfidenceLevel`) and transparent adjustment factors (`ConfidenceAdjustmentFactor`).

### Invariant & Boundary Guarantees
1. **Confidence is Aggregation & Dampening, NOT a Financial Decision**: The Confidence Engine does NOT recommend `AUTO_ELIGIBLE` or `MATCH_SUGGESTED`, does NOT route to human review, and does NOT generate decision objects (these belong strictly to Phase 14.13).
2. **Observed Financial Mutation: NONE**:
   - Database writes: **0**
   - Session mutations: `len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`
   - Invoice / Payment status transitions: **NONE**
3. **Deterministic Anchoring**: Unconflicted candidates retain their exact Phase 14.11 base score.
4. **No Double Counting**: Base evidence weights (invoice reference, amount match, bank account, date proximity) were scored in Phase 14.11 and are NOT re-added in Phase 14.12.
5. **Conflict Suppression & Hard Ceiling**: Any candidate associated with active conflicting evidence incurs a 50% deduction and is clamped to a hard ceiling of $40.00$, preventing high-confidence classification.
6. **Multi-Candidate Ambiguity Dampening**: When competing candidates exhibit near-equal base scores ($\Delta \le 10.00$), a -15.00 point ambiguity penalty is deterministically deducted to reflect candidate ambiguity.
7. **Empirical Calibration Boundary**: Confidence scores are explicitly marked `is_statistically_calibrated=False`, preventing misleading claims of Bayesian or ML posterior probability.
8. **Pure Decimal Arithmetic**: All computations use `Decimal` with `ROUND_HALF_UP` to two decimal places, avoiding binary floating-point rounding errors.

---

## 2. Confidence Level Mapping

The qualitative mapping is strictly bounded:

| Qualitative Level | Score Range | Description |
|---|---|---|
| `VERY_HIGH` | $[90.00, 100.00]$ | Deterministic direct evidence present, exact amount match, unconflicted. |
| `HIGH` | $[70.00, 89.99]$ | Strong supporting evidence, zero conflicting signals. |
| `MEDIUM` | $[40.00, 69.99]$ | Moderate supporting signals or multi-candidate competition. |
| `LOW` | $[0.01, 39.99]$ | Weak evidence or suppressed by active conflict penalty ceiling. |
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
- `ConfidenceFactorType`: Enum identifying exact reasons for confidence adjustments (`BASE_MATCH_SCORE_ANCHOR`, `CONFLICT_PENALTY`, `HARD_CONFLICT_CEILING_ENFORCED`, `MULTI_CANDIDATE_AMBIGUITY_DAMPENING`, `SCORE_BOUNDS_CLAMPING`).
- `ConfidenceAdjustmentFactor`: Transparent explanation DTO with `factor_type`, `points_delta`, and `description`.
- `ConfidenceConfig`: Immutable threshold configuration with fail-closed validation.
- `CandidateConfidenceAssessment`: Evaluated candidate with `invoice_id`, `invoice_number`, `base_match_score`, `confidence_score`, `confidence_level`, `has_direct_evidence`, `has_conflicting_evidence`, `is_statistically_calibrated=False`, and adjustment audit trail.
- `ConfidenceEngineResult`: Container for all evaluated candidates, sorted deterministically by `(-confidence_score, -base_match_score, invoice_number, str(invoice_id))`.

---

## 4. Test Verification Summary

### Targeted Test Suite
- Unit Tests: `tests/unit/test_confidence_engine_rules.py` (12 passed)
- Integration & IDOR Tests: `tests/integration/test_confidence_engine_api.py` (6 passed)
- **Subtotal Phase 14.12 Tests**: **18 passed**

### Targeted Suite Output
```text
============================= test session starts =============================
platform win32 -- Python 3.11.8, pytest-9.1.1, pluggy-1.6.0
collected 18 items

tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineRules::test_base_score_anchoring_unconflicted PASSED [  5%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineRules::test_qualitative_confidence_levels_mapping PASSED [ 11%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineRules::test_conflict_penalty_and_hard_ceiling PASSED [ 16%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineRules::test_multi_candidate_ambiguity_dampening PASSED [ 22%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineRules::test_zero_score_remains_zero_none PASSED [ 27%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineRules::test_direct_evidence_grounding_flag PASSED [ 33%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineRules::test_100_run_permutation_determinism PASSED [ 38%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineRules::test_adversarial_mismatched_payment_tenant_id_fails_closed PASSED [ 44%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineRules::test_custom_confidence_config_thresholds PASSED [ 50%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineRules::test_invalid_config_threshold_ordering_raises_domain_error PASSED [ 55%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineRules::test_deterministic_ranking_order_ties PASSED [ 61%]
tests/unit/test_confidence_engine_rules.py::TestConfidenceEngineRules::test_out_of_bounds_confidence_score_raises_domain_error PASSED [ 66%]
tests/integration/test_confidence_engine_api.py::test_confidence_api_e2e PASSED [ 72%]
tests/integration/test_confidence_engine_api.py::test_confidence_zero_financial_mutation PASSED [ 77%]
tests/integration/test_confidence_engine_api.py::test_confidence_cross_tenant_payment_idor PASSED [ 83%]
tests/integration/test_confidence_engine_api.py::test_confidence_cross_tenant_customer_override_idor PASSED [ 88%]
tests/integration/test_confidence_engine_api.py::test_confidence_unauthenticated_rejected PASSED [ 94%]
tests/integration/test_confidence_engine_api.py::test_batch_confidence_endpoint PASSED [100%]

============================= 18 passed in 3.02s ==============================
```

### Full Platform Regression Suite Output
```text
============================= test session starts =============================
platform win32 -- Python 3.11.8, pytest-9.1.1, pluggy-1.6.0
collected 559 items

........................................................................ [ 12%]
........................................................................ [ 25%]
........................................................................ [ 38%]
........................................................................ [ 51%]
........................................................................ [ 64%]
........................................................................ [ 77%]
........................................................................ [ 90%]
.......................................................                  [100%]
559 passed in 208.85s (0:03:28)
```

---

## 5. Security & Tenant Isolation Audit
- **Tenant Isolation**: Both payment retrieval and customer override resolution strictly mandate `company_id`. Mismatched requests fail closed with HTTP 404 (`PAYMENT_NOT_FOUND` / `CUSTOMER_NOT_FOUND`).
- **Internal Cross-Tenant Integrity**: The engine asserts `candidate_scores.company_id == normalized_evidence.company_id == payment_context.company_id`. Any discrepancy raises `DomainError` immediately.
- **Data Protection**: Bank account numbers and PII in evidence are masked (e.g., `********5544`) in all outputs.
- **Zero Financial Mutation**: Confirmed with active checks against `len(db_session.dirty) == 0`.

---

## 6. Non-Goals for Phase 14.12
- Decision engine policy (`AUTO_ELIGIBLE`, `MATCH_SUGGESTED`, `NEEDS_REVIEW`) $\rightarrow$ strictly deferred to Phase 14.13.
- Decision objects and human review workflow routing $\rightarrow$ Phase 14.14.
- Invoice and Payment ledger allocation, settlement, or balance updates $\rightarrow$ downstream financial state mutation phases.
