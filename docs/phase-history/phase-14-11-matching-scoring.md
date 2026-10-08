# Phase 14.11 — Matching & Scoring: Phase History & Implementation Verification

## 1. Executive Summary

| Phase Dimension | Specification & Implementation Status |
| :--- | :--- |
| **Phase Target** | **Phase 14.11 — Reconciliation Engine: Matching & Scoring** |
| **Parent Module** | `backend/app/modules/reconciliation/` |
| **Architecture Pattern** | Modular Monolith + Clean Architecture + DDD Lite |
| **Upstream Contracts** | Phase 14.1 Payment Intake through Phase 14.10 Evidence Normalization (**ALL FROZEN**) |
| **Downstream Consumers** | Phase 14.12 Confidence Calibration, Phase 14.13 Decision Engine |
| **Observed Financial Mutation** | **NONE** (Strictly Read-Only Evaluation, 0 DB Writes, `len(db.dirty) == 0`, `len(db.new) == 0`, `len(db.deleted) == 0`) |
| **Database Migrations** | **0 New Migrations** (Stateless in-memory domain evaluation) |
| **Phase 14.11 Tests** | **14 Automated Tests** (8 pure domain rule & determinism tests, 6 integration/security tests, 100% Pass Rate) |
| **Platform Total Suite** | **533 Total Tests Passed**, 0 Failures (Runtime: 85.95s) |
| **Phase Status** | **FROZEN** |

---

## 2. Business Problem & Mission

In financial reconciliation, after evidence is gathered (Phase 14.9) and canonicalized (Phase 14.10), downstream systems need an objective, deterministic method to measure candidate alignment against business rules.

**Core Mission:**
Provide an authoritative, mathematically bounded, explainable scoring layer that consumes `CanonicalEvidenceObject` instances and produces reproducible candidate match scores between `0.00` and `100.00`.

**Critical Financial Boundary:**
> **A score is evidence aggregation. A score is NOT a financial decision.**

The Matching & Scoring Engine answers:
*"How strongly do active, unconflicted deterministic evidence signals support this candidate relative to defined scoring rules?"*

It explicitly does **NOT** answer:
- Should this candidate be auto-reconciled? (`AUTO_ELIGIBLE` and `MATCH_SUGGESTED` belong strictly to Phase 14.13).
- What is the probability that this match is correct? (Calibrated confidence belongs strictly to Phase 14.12).
- How much money should be allocated? (Financial allocations belong strictly to settlement/mutation phases).

---

## 3. Mathematical Model & Rule 2.4 Weights Configuration

### 3.1 Mathematical Formula

$$\text{raw\_score} = \sum_{s \in \text{ActiveSignals}} \text{weight}(s)$$

$$\text{total\_score} = \max\left(0.00, \min\left(100.00, \text{raw\_score}\right)\right)$$

All calculations utilize `Decimal` precision with explicit two-decimal quantizing (`Decimal("0.01")`, `ROUND_HALF_UP`). Zero binary floating-point arithmetic is permitted.

### 3.2 Canonical Scoring Signal Weights (Rule 2.4)

| Signal Type | Default Weight | Description / Trigger Condition |
| :--- | :--- | :--- |
| `IDENTIFIER_MATCH` | `+40.00` | Exact bank account or UTR identifier match between payment & customer/invoice |
| `INVOICE_NUMBER_MATCH` | `+35.00` | Exact invoice number found in payment reference or narration |
| `AMOUNT_EXACT_MATCH` | `+30.00` | Payment unallocated amount exactly equals candidate invoice open amount |
| `CUSTOMER_NAME_MATCH` | `+20.00` | Exact match or high-confidence token/alias match on customer counterparty name |
| `DATE_PROXIMITY_MATCH` | `+10.00` | Payment transaction date complies with date causality and is within 30 days of invoice due date |
| `HISTORICAL_PATTERN` | `+5.00` | Supporting evidence from recurring historical settlement patterns |

### 3.3 Empirical Validation Status (Provisional)
As formally documented in project architecture, Rule 2.4 weights are **provisional baseline weights** based on business heuristics. They have **not yet been empirically validated** against large-scale production financial datasets. The engine explicitly returns:
```json
"is_empirically_validated": false
```
on all domain objects, DTOs, and REST responses until empirical offline machine-learning or historical regression calibration is performed in future optimization phases.

---

## 4. Architectural Rules & Invariants

1. **Zero Financial Mutation:**
   - Evaluates evidence in memory. Zero database writes, zero accounting state updates.
   - Verified via `db_session.dirty`, `db_session.new`, `db_session.deleted` all remaining empty (`len == 0`).

2. **Zero Points for Missing Evidence:**
   - Any evidence classified as `MISSING` or resulting in `ABSENT` awards `0.00` points. Missing evidence never penalizes nor awards points.

3. **Conflict Suppression:**
   - If candidate evidence contains an active conflict (`result == CONFLICT` or `classification == CONFLICTING`), the corresponding positive score contribution is suppressed (`applied = False`, contribution = `0.00`).
   - Currency conflict (`CURRENCY_CONSISTENCY` conflict) suppresses `AMOUNT_EXACT_MATCH`.
   - Bank account conflict suppresses `IDENTIFIER_MATCH`.
   - Date causality conflict (`payment_date < invoice_date`) suppresses `DATE_PROXIMITY_MATCH`.

4. **Anti-Double Counting:**
   - If an invoice number is found in both payment reference and narration, it receives at most one `INVOICE_NUMBER_MATCH` (+35.00) credit.
   - If customer counterparty is matched by name token and alias, it receives at most one `CUSTOMER_NAME_MATCH` (+20.00) credit.

5. **Pure Determinism & Invariant Ordering:**
   - Multiple candidate scores are deterministically sorted by:
     1. `total_score` descending
     2. `invoice_number` ascending
     3. `str(invoice_id)` ascending
   - Scoring timestamps (`scored_at`) are excluded from serialized deterministic comparisons.
   - Tested and verified over 100 shuffled permutations yielding identical bitwise payloads.

6. **Explainability & Sensitive Coordinate Masking:**
   - Every candidate score provides a detailed list of `ScoreContribution` objects documenting every active and suppressed signal, weight, reason, source, and target entity.
   - Sensitive financial coordinates (e.g., bank account numbers) in contribution payloads are masked (e.g. `********5544`).

7. **Strict Multi-Tenancy (Fail-Closed IDOR):**
   - API endpoints enforce authenticated tenant scoping via `current_user.company_id`.
   - Attempts to score payments belonging to another tenant return `HTTP 404 PAYMENT_NOT_FOUND`.
   - Customer override belonging to another tenant returns `HTTP 404 CUSTOMER_NOT_FOUND`.

---

## 5. Artifacts and Files Changed

1. `backend/app/modules/reconciliation/domain/matching_scoring.py` (NEW)
   - Core domain aggregates, value objects, and deterministic engine: `ScoringSignalType`, `ScoringWeightsConfig`, `ScoreContribution`, `CandidateScoreResult`, `MatchingScoringResult`, `MatchingScoringEngine`.
2. `backend/app/modules/reconciliation/domain/__init__.py` (MODIFIED)
   - Exported Phase 14.11 domain classes alongside Phase 14.10 and earlier modules.
3. `backend/app/modules/reconciliation/application/use_cases.py` (MODIFIED)
   - `MatchingScoringUseCase`: Orchestrates lookup, evidence collection, normalization, and scoring.
   - `BatchMatchingScoringUseCase`: Bounded batch execution across up to 100 unreconciled payments.
4. `backend/app/modules/reconciliation/presentation/schemas.py` (MODIFIED)
   - Request and response DTOs: `ScoreContributionResponse`, `CandidateScoreResponse`, `MatchingScoringRequest`, `MatchingScoringResponse`, `BatchMatchingScoringRequest`, `BatchMatchingScoringResponse`.
5. `backend/app/modules/reconciliation/presentation/router.py` (MODIFIED)
   - Endpoints:
     - `POST /api/v1/reconciliation/matching-scoring/batch`
     - `POST /api/v1/reconciliation/matching-scoring/{payment_id}`
6. `backend/tests/unit/test_matching_scoring_rules.py` (NEW)
   - 8 comprehensive unit tests covering point allocations, mathematical clamping, anti-double counting, missing evidence handling, conflict suppression, 100-permutation determinism, tie handling, and weight configuration.
7. `backend/tests/integration/test_matching_scoring_api.py` (NEW)
   - 6 integration tests verifying REST e2e flow, zero financial mutation, cross-tenant IDOR protection, customer override IDOR protection, unauthenticated rejection, and batch processing.

---

## 6. Verification Evidence

### 6.1 Targeted Test Execution
```text
tests/unit/test_matching_scoring_rules.py ........                       [100%]
8 passed in 0.13s

tests/integration/test_matching_scoring_api.py ......                    [100%]
6 passed in 2.81s
```

### 6.2 Full Platform Regression Suite
```text
======================= 533 passed in 85.95s (0:01:25) =======================
```
All 533 tests in the repository pass with zero errors, zero warnings, and zero regressions.

---

## 7. Status Declaration

Phase 14.11 fulfills all requirements and quality gates:
- [x] Mathematical model and Rule 2.4 weights implemented.
- [x] Pure determinism and Decimal precision.
- [x] Conflict suppression and anti-double counting verified.
- [x] Zero financial mutation verified.
- [x] Non-decision boundary enforced (no decisions, no probabilities).
- [x] Tenant isolation and IDOR protection verified.
- [x] Empirical validation status flagged as provisional.
- [x] Full platform regression passed (533/533).

**Phase 14.11 is FROZEN.**
