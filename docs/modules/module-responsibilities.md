# Module Responsibility Map

# AI Invoice Reconciliation Platform

**Version:** 1.0  
**Status:** Frozen  

---

## 1. Module Matrix

| Module | Core Responsibility | Key Inputs | Key Outputs | Must NOT Own |
|---|---|---|---|---|
| **Auth** | User identity, credentials, JWT issuance, session validation | Email, password, registration info | Access tokens, authenticated identity | Tenant configuration, payment logic |
| **Company** | Tenant boundary, company profile, membership | Company details, user roles | Company context, active membership | User authentication, financial data |
| **Customer** | Counterparty identity, aliases, payment identifiers | Customer details, aliases, bank accounts | Normalized customer profile, search matches | Invoices, payments, matching decisions |
| **Invoice** | Accounts receivable lifecycle, balances, document links | Invoice data, uploaded PDFs, allocations | Invoice state (`Pending`, `Paid`), balance | Bank transactions, reconciliation logic |
| **Payment** | Bank transaction lifecycle, statement ingestion, idempotency | CSV statements, transaction rows | Normalized payments, unallocated balances | Customer profiles, matching algorithms |
| **Reconciliation** | Payer identification, candidate generation, evidence collection, matching, confidence calculation | Payments, eligible invoices, customer signals | Structured Decision Object, Confidence Score, Evidence Items | Mutating invoice status directly, UI layout |
| **Review Center**| Human-in-the-loop exception queue, approval workflow, transactional settlement | Review actions (approve, reject, manual match) | Committed allocations, state updates | Algorithm execution, raw OCR |
| **Dashboard** | Operational aggregation, summary statistics | Business entity queries | Pending counts, aging metrics, queue status | Financial mutations, business logic |
| **Audit** | Immutable append-only audit trail | Domain events, state snapshots, actor ID | Audit log queries, historical timeline | Business rules, authorization policies |

---

## 2. Invariant Rule of Monolith Coupling

1. Modules must **never** perform direct cross-module SQL joins or mutations.
2. If `Reconciliation` needs invoices, it calls the `InvoiceQueryPort` (application service contract).
3. If `Review Center` approves an allocation, it invokes `ApplyAllocationUseCase`, which executes the transaction across `Payment` and `Invoice` bounded contexts with domain invariant verification.
