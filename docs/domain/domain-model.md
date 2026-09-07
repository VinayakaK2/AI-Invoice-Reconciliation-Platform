# Domain Model Specification

# AI Invoice Reconciliation Platform

**Version:** 1.0  
**Status:** Frozen  
**Pattern:** DDD Lite + Clean Architecture  

---

## 1. Bounded Contexts & Aggregate Roots

```text
┌─────────────────────────────────────────────────────────────┐
│ Company Bounded Context (Tenant Root)                       │
│ - Company (Aggregate Root)                                  │
│ - User (Role: OWNER, ACCOUNTANT)                            │
└──────────────────────────┬──────────────────────────────────┘
                           │ scopes
┌──────────────────────────▼──────────────────────────────────┐
│ Customer Bounded Context                                    │
│ - Customer (Aggregate Root)                                 │
│ - CustomerAlias (Entity)                                    │
│ - CustomerPaymentIdentifier (Entity: Bank Account, UPI VPA) │
└──────────────────────────┬──────────────────────────────────┘
                           │ bills
┌──────────────────────────▼──────────────────────────────────┐
│ Invoice Bounded Context                                     │
│ - Invoice (Aggregate Root)                                  │
│ - InvoiceItem (Entity)                                      │
└──────────────────────────┬──────────────────────────────────┘
                           │ receives
┌──────────────────────────▼──────────────────────────────────┐
│ Payment Bounded Context                                     │
│ - Payment (Aggregate Root)                                  │
│ - BankStatement (Entity)                                    │
└──────────────────────────┬──────────────────────────────────┘
                           │ matched by
┌──────────────────────────▼──────────────────────────────────┐
│ Reconciliation Bounded Context (Core Differentiator)        │
│ - ReconciliationCandidate (Value Object)                    │
│ - EvidenceItem (Value Object)                               │
│ - ConfidenceScore (Value Object)                            │
│ - ReconciliationDecision (Aggregate Root)                   │
│ - PaymentAllocation (Aggregate Root - Join Entity)          │
└──────────────────────────┬──────────────────────────────────┘
                           │ reviewed by
┌──────────────────────────▼──────────────────────────────────┐
│ Review Bounded Context                                      │
│ - ReviewItem (Aggregate Root)                               │
│ - ManualResolution (Command/Action)                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Core Entities & Value Objects

### 2.1 Money (Value Object)
- **Attributes**: `amount: Decimal`, `currency: str`
- **Invariants**:
  - Exact precision: 2 decimal places (or 4 for intermediary calculations).
  - Cross-currency addition/subtraction forbidden without explicit exchange rate.
  - Immutability.

### 2.2 Company (Aggregate Root)
- **Attributes**: `id: UUID`, `name: str`, `base_currency: str`, `is_active: bool`
- **Responsibility**: Top-level tenant context. All queries in the system must be scoped by `company_id`.

### 2.3 User (Entity)
- **Attributes**: `id: UUID`, `company_id: UUID`, `email: str`, `hashed_password: str`, `role: UserRole`, `is_active: bool`
- **Roles**: `OWNER`, `ACCOUNTANT`

### 2.4 Customer (Aggregate Root)
- **Attributes**: `id: UUID`, `company_id: UUID`, `name: str`, `tax_id: Optional[str]`, `email: Optional[str]`, `phone: Optional[str]`, `is_archived: bool`
- **Entities**:
  - `CustomerAlias`: `id: UUID`, `alias_name: str` (normalized string for statement matching).
  - `CustomerPaymentIdentifier`: `id: UUID`, `identifier_type: IdentifierType`, `identifier_value: str` (e.g. Virtual Account, Bank Account No., UPI ID).

### 2.5 Invoice (Aggregate Root)
- **Attributes**: `id: UUID`, `company_id: UUID`, `customer_id: UUID`, `invoice_number: str`, `issue_date: date`, `due_date: date`, `total_amount: Money`, `paid_amount: Money`, `outstanding_amount: Money`, `status: InvoiceStatus`, `document_url: Optional[str]`
- **Status Lifecycle**:
  $$\text{PENDING} \xrightarrow{\text{Partial Allocation}} \text{PARTIALLY\_PAID} \xrightarrow{\text{Full Allocation}} \text{PAID}$$
  $$\text{PENDING} \xrightarrow{\text{Void/Cancel}} \text{CANCELLED}$$
- **Invariants**:
  - `outstanding_amount = total_amount - paid_amount`
  - `0 <= outstanding_amount <= total_amount`
  - Cancelled invoices cannot receive allocations.

### 2.6 Payment (Aggregate Root)
- **Attributes**: `id: UUID`, `company_id: UUID`, `statement_id: Optional[UUID]`, `transaction_date: date`, `amount: Money`, `allocated_amount: Money`, `unallocated_amount: Money`, `narration: str`, `reference_number: Optional[str]`, `bank_account_number: Optional[str]`, `status: PaymentStatus`
- **Status Lifecycle**:
  $$\text{UNMATCHED} \xrightarrow{\text{Suggested Match}} \text{SUGGESTED} \xrightarrow{\text{Approved}} \text{ALLOCATED}$$
  $$\text{UNMATCHED} \xrightarrow{\text{Partial Allocation}} \text{PARTIALLY\_ALLOCATED}$$
  $$\text{UNMATCHED} \xrightarrow{\text{Mark Ignored}} \text{IGNORED}$$
- **Invariants**:
  - `unallocated_amount = amount - allocated_amount`
  - `0 <= unallocated_amount <= amount`

### 2.7 PaymentAllocation (Join Entity / Financial Record)
- **Attributes**: `id: UUID`, `company_id: UUID`, `payment_id: UUID`, `invoice_id: UUID`, `allocated_amount: Money`, `allocated_by_user_id: Optional[UUID]`, `created_at: datetime`
- **Invariants**:
  - `allocated_amount > 0`
  - $\sum \text{allocated\_amount for payment} \le \text{payment.amount}$
  - $\sum \text{allocated\_amount for invoice} \le \text{invoice.total\_amount}$

### 2.8 ReconciliationDecision (Aggregate Root)
- **Attributes**: `id: UUID`, `company_id: UUID`, `payment_id: UUID`, `status: DecisionStatus`, `match_score: Decimal` (deterministic evidence score 0–100, NOT a statistical probability), `algorithm_version: str`, `evidence: List[EvidenceItem]`, `created_at: datetime`
- **Decision Statuses**:
  - `MATCH_SUGGESTED`: Deterministic candidate found, awaiting human review.
  - `AUTO_ELIGIBLE`: High evidence score match eligible for expedited review. (NOTE: This does NOT mean `AUTO_APPLIED`. In the MVP, all financial mutations are controlled and require explicit human or policy approval; silent autonomous mutations are forbidden).
  - `REVIEW_REQUIRED`: Discrepancy or ambiguous candidates requiring human inspection.
  - `AMBIGUOUS`: Conflicting signals (e.g. multiple customers match bank narration).
  - `NO_MATCH`: No candidate invoices found.
