# Business Rules & Invariants Specification

# AI Invoice Reconciliation Platform

**Version:** 1.0  
**Status:** Frozen  

---

## 1. Financial Integrity Rules

1. **Exact Precision**: Floating-point types are strictly forbidden for money. All calculations must use `Decimal` with rounding `ROUND_HALF_UP` to 2 decimal places.
2. **Conservation of Value**:
   - Every rupee/cent allocated to an invoice must decrement the payment's unallocated amount and the invoice's outstanding amount by the exact same amount.
   - An allocation cannot create or destroy money.
3. **Over-Allocation Prevention**:
   - Total allocations to an invoice cannot exceed `invoice.total_amount`.
   - Total allocations from a payment cannot exceed `payment.amount`.
4. **State Consistency Preconditions**:
   - Before executing an allocation, the target invoice status must be `PENDING` or `PARTIALLY_PAID`.
   - The target payment status must be `UNMATCHED`, `SUGGESTED`, or `PARTIALLY_ALLOCATED`.
   - If an invoice was marked `CANCELLED` or `PAID` by a concurrent operation, allocation must fail.
5. **Atomic Commit Boundary**:
   - Updating `Payment`, updating `Invoice`, creating `PaymentAllocation`, and writing the `AuditLog` must occur in a single database transaction.

---

## 2. Reconciliation Engine Rules

### 2.1 Payer Identification Rules
- **Rule P-1 (Direct Identifier)**: If narration or bank reference matches a `CustomerPaymentIdentifier` (account number, virtual account, or UPI VPA), customer match is direct with maximum confidence.
- **Rule P-2 (Exact Alias / Name)**: If narration contains an exact customer alias or exact business name, customer match is established.
- **Rule P-3 (Fuzzy Match Threshold)**: Normalized token similarity $\ge 0.85$ matches customer if no competing customer scores $\ge 0.70$.
- **Rule P-4 (Ambiguity Guard)**: If multiple customers match with scores within 0.15 of each other and no direct identifier exists, emit `AMBIGUOUS_PAYER`. Do not guess.

### 2.2 Candidate Generation & Filtering Rules
- **Rule C-1 (Tenant Isolation)**: Invoices considered for matching must strictly belong to the same `company_id`.
- **Rule C-2 (Customer Scope)**: When payer is identified, only invoices belonging to that `customer_id` are candidate invoices.
- **Rule C-3 (Status Filter)**: Only invoices with `status IN ('PENDING', 'PARTIALLY_PAID')` and `outstanding_amount > 0` are evaluated.
- **Rule C-4 (Currency Match)**: Payment currency and invoice currency must be identical. Cross-currency matching is deferred.

### 2.3 Combinatorial Matching Rules
- **Rule M-1 (Exact Single Match)**: If an invoice exists where `invoice.outstanding_amount == payment.amount`, it is a primary candidate.
- **Rule M-2 (Bounded Multi-Invoice Search)**: If no single invoice matches the exact payment, search for subsets of candidate invoices whose sum of outstanding balances equals `payment.amount`. (Note: Initial search parameters—such as max combination depth $k \le 4$, candidate limit $n \le 30$, and 100ms execution timeout—are initial engineering policies subject to empirical benchmark validation, not unalterable business truths).
- **Rule M-3 (FIFO Aging Heuristic)**: If multiple subset combinations sum to the same payment amount, the combination that settles the oldest outstanding invoices (by `due_date`) receives priority, but is flagged as `REVIEW_REQUIRED` due to ambiguity.
- **Rule M-4 (Partial Payment Match)**: If narration contains the exact invoice number `INV-XXXX` and `payment.amount < invoice.outstanding_amount`, suggest partial allocation.

### 2.4 Evidence & Match Scoring Rules (`match_score`)
The system computes a deterministic heuristic **`match_score` (0.00 to 100.00)** by aggregating active structured evidence weights. This score represents deterministic evidence strength, **NOT a calibrated statistical probability**. Claiming "probability of correctness" or "guaranteed match" is strictly forbidden.

Structured evidence signals:
- `IDENTIFIER_MATCH` (+40 points): Direct account/UPI match.
- `INVOICE_NUMBER_MATCH` (+35 points): Explicit invoice number in narration.
- `AMOUNT_EXACT_MATCH` (+30 points): Invoice or combination sum equals payment.
- `CUSTOMER_NAME_MATCH` (+20 points): Verified customer name/alias match.
- `DATE_PROXIMITY_MATCH` (+10 points): Payment date is within 30 days of invoice due date.
- `HISTORICAL_PATTERN` (+5 points): Customer previously paid invoices in this pattern.

$$\text{match\_score} = \min\left(100, \sum \text{Weights of Active Unconflicted Evidence}\right)$$

### 2.5 Decision Policy Thresholds
- **Score $\ge 90$ + Direct Evidence + Zero Conflict**: Status = `AUTO_ELIGIBLE`. (Note: `AUTO_ELIGIBLE` qualifies a match for expedited review or one-click approval. It is strictly NOT `AUTO_APPLIED`. In the MVP, the system never silently mutates financial records without human review and an audit trail).
- **Score $70–89$ OR Multi-Invoice Match**: Status = `MATCH_SUGGESTED` (routed to Review Center).
- **Multiple competing combinations OR Ambiguous customer**: Status = `AMBIGUOUS` (requires review with conflict explanation).
- **No matching candidate found**: Status = `NO_MATCH` (unallocated payment).

---

## 3. Review Center & Human Override Rules

1. **Human Authority**: An accountant can accept, reject, or manually adjust any suggested match.
2. **Validation on Approval**: Even when an accountant clicks "Approve", the server must re-verify that the invoice outstanding amounts have not changed since the suggestion was made.
3. **Audit Trail**: Every approval, rejection, and manual allocation must record:
   - User ID of the actor
   - Timestamp
   - Decision ID and original algorithmic suggestion
   - Final applied allocation amounts
