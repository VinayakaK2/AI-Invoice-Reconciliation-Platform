# MASTER BRIEF — AI INVOICE RECONCILIATION PLATFORM

You are joining an ongoing product as a **senior product manager, product architect, staff software engineer, system architect, database architect, and technical product strategist**.

Do NOT behave like a code generator.

Your job is to first understand the product deeply, challenge weak assumptions, define the correct product and technical architecture, and create the complete engineering design foundation **before implementation begins**.

The product is a real commercial SaaS intended to process financial data. Therefore, correctness, data integrity, security, auditability, explainability, reliability, and maintainability are more important than adding impressive AI features.

---

# 1. WHAT WE ARE BUILDING

We are building an:

## AI Invoice Reconciliation Platform

The product is an **AI-powered reconciliation layer** that sits on top of existing accounting/ERP systems.

We are NOT trying to become:

- Accounting software
- ERP
- CRM
- Payroll software
- Inventory software
- GST/tax filing software
- Expense management software
- Financial forecasting software

Existing systems such as Tally, Zoho Books, QuickBooks, Xero, or custom ERP systems may remain the system of record.

Our product solves the painful reconciliation layer between:

**Invoices → Customer payments → Bank transactions → Reconciliation → Human approval → Accounting status**

The central product promise is:

> Import invoices. Import bank transactions. Identify and reconcile payments intelligently. Show the evidence. Let the accountant approve ambiguous financial decisions.

The product vision is to become a highly trustworthy reconciliation platform that can significantly reduce repetitive manual reconciliation work while keeping humans in control of financial decisions.

---

# 2. WHY WE ARE BUILDING IT

Today accountants and finance teams may manually compare:

- Bank statements
- Invoice records
- Invoice numbers
- Payment references
- Customer names
- Payment amounts
- Outstanding balances
- Due dates
- Emails
- Purchase orders
- Historical payment behavior
- Other available evidence

Common problems include:

- One payment clearing multiple invoices
- Partial payments
- Split payments
- Missing invoice references
- Bank narration not matching customer name exactly
- Multiple customers having similar names
- Duplicate or ambiguous transactions
- Manual Excel/Tally checking
- Calling customers to identify payments
- Repetitive invoice data entry
- Delayed reconciliation
- Human mistakes
- Poor auditability

The product should reduce this repetitive work rather than simply adding another accounting dashboard.

---

# 3. CORE PRODUCT PHILOSOPHY

The most important principles are:

### Problem First

Every feature must solve a real customer problem.

If a feature cannot clearly answer:

> "What customer problem does this solve?"

do not build it.

### MVP First

Build the smallest product that creates real customer value.

Do not add unnecessary AI, analytics, integrations, dashboards, or infrastructure merely because they sound impressive.

### No Over-Engineering

Start with a:

**Modular Monolith**

Do NOT prematurely introduce:

- Microservices
- Kubernetes
- Kafka
- CQRS
- Event Sourcing
- Distributed systems
- Complex event buses
- Excessive abstractions
- Hundreds of tables
- Hundreds of APIs

The architecture must be capable of scaling, but complexity must be justified by actual requirements.

### Financial Logic Must Be Deterministic

Money-related decisions must not depend on probabilistic LLM behavior.

Algorithms should own:

- Customer identification logic
- Candidate generation
- Invoice matching
- Payment allocation
- Evidence scoring
- Confidence calculation
- Financial decision logic
- Invoice/payment state transitions

### LLM Is an Assistant, Not the Financial Decision Maker

LLMs may be used for:

- Understanding payment narration
- Understanding emails
- Extracting meaning from unstructured text
- Generating explanations
- Drafting reminders
- Natural-language interaction

LLMs must NOT independently decide:

> "This payment should mark these invoices as paid."

The deterministic reconciliation engine must make that decision.

### Human Control

Ambiguous or insufficiently supported financial decisions must go to human review.

The system must never silently modify financial records when evidence is insufficient.

### Explainability

Every reconciliation decision must be explainable through structured evidence.

The system must be able to answer:

> Why was this payment matched to these invoices?

### Auditability

Important financial and user actions must have an immutable audit trail.

---

# 4. THE CORE RECONCILIATION PROBLEM

Understand this extremely carefully.

Suppose a payment arrives:

```text
Amount: ₹35,000
Narration: ABC TECH
Date: 23 July
```

The system should NOT immediately search every invoice in the entire database.

First:

## Step 1 — Identify the payer

Possible evidence:

- Bank account
- UPI ID
- Virtual account
- Payment gateway reference
- Transaction reference
- Customer name
- Customer aliases
- Historical mapping
- Email evidence
- Other available identifiers

If the payer cannot be reliably identified, the system must not guess.

Example:

Two customers:

```text
ABC Technologies Pvt Ltd
ABC Technologies LLP
```

Bank only says:

```text
ABC TECHNOLOGIES
```

If no additional evidence exists:

```text
Unable to identify payer.

Reason:
Multiple customers satisfy the available evidence.

Action:
Manual review required.
```

That is correct behavior.

---

# 5. CANDIDATE INVOICE SEARCH

Once a customer is identified, search only that customer's relevant unpaid/outstanding invoices.

Do NOT globally mix invoices from unrelated customers.

Filter using:

- Company/tenant
- Customer
- Invoice status
- Outstanding balance
- Currency
- Relevant dates
- Other business constraints

Then generate possible matches.

Example:

Invoices:

```text
INV-101 = ₹10,000
INV-102 = ₹25,000
INV-103 = ₹15,000
INV-104 = ₹8,000
INV-105 = ₹12,000
```

Payment:

```text
₹35,000
```

Possible allocations:

```text
INV-101 + INV-102 = ₹35,000
```

or:

```text
INV-103 + INV-104 + INV-105 = ₹35,000
```

The matching engine must evaluate these possibilities using deterministic logic and available evidence.

---

# 6. EVIDENCE ARCHITECTURE

This is one of the most important parts of the product.

The system must distinguish:

## Structured Evidence

Collected by deterministic software from:

- Database
- Invoice records
- Payment records
- Bank statements
- Customer records
- Account mappings
- Amounts
- Dates
- References
- Outstanding balances
- Historical transactions

## Unstructured Evidence

Potentially interpreted by an LLM:

- Emails
- Payment narration
- Notes
- Free-text descriptions
- Other natural-language content

The LLM should convert useful unstructured information into **structured facts**.

Example:

Email:

> "We have transferred payment for the July invoices."

LLM output:

```json
{
  "intent": "payment_confirmation",
  "period": "July"
}
```

The deterministic reconciliation engine then uses this structured information as one evidence signal.

---

# 7. EVIDENCE → DECISION → EXPLANATION

Do NOT make the LLM inspect internal algorithm execution.

Instead design a structured pipeline:

```text
Payment
   ↓
Reconciliation Engine
   ↓
Evidence Builder
   ↓
Decision Object
   ↓
 ┌──────────────┬───────────────┐
 ↓              ↓               ↓
Dashboard     Audit Log        LLM
                                ↓
                         Human Explanation
```

Example Decision Object:

```json
{
  "decision": "MATCH_FOUND",
  "matched_invoices": ["INV-101", "INV-102"],
  "confidence": 0.984,
  "evidence": [
    {
      "type": "customer_match",
      "result": "matched",
      "details": "Bank payer identity matched known customer"
    },
    {
      "type": "amount_match",
      "result": "matched",
      "details": "Invoice total equals payment amount"
    },
    {
      "type": "historical_pattern",
      "result": "supporting",
      "details": "Customer previously paid multiple invoices together"
    }
  ]
}
```

The LLM receives this structured decision/evidence object and explains it.

It does NOT override it.

---

# 8. RECONCILIATION ENGINE SHOULD BE MODULAR

Design the reconciliation engine as independent logical components:

```text
Payment Intake
        ↓
Payer Identification
        ↓
Candidate Generator
        ↓
Evidence Collector
        ↓
Matching Engine
        ↓
Evidence Scoring
        ↓
Confidence Engine
        ↓
Decision Engine
        ↓
Review Queue
```

Each component needs clearly defined:

- Responsibility
- Inputs
- Outputs
- Business rules
- Failure cases
- Dependencies
- Tests

Do not create unnecessary microservices. These can initially live inside the modular monolith.

---

# 9. PARTIAL AND MULTI-INVOICE PAYMENTS

The product must support:

### Exact payment

Invoice:

₹25,000

Payment:

₹25,000

→ Paid

### Partial payment

Invoice:

₹100,000

Payment:

₹40,000

→ Partially Paid

Outstanding:

₹60,000

### Multiple invoice payment

INV-101 = ₹20,000

INV-102 = ₹30,000

Payment = ₹50,000

→ Allocate across both invoices if evidence supports it.

### Overpayment / underpayment

Design appropriate handling.

### Duplicate payment

Detect and prevent incorrect double allocation.

### Ambiguous allocation

Send to human review.

Do not guess.

---

# 10. CUSTOMER PAYMENT HISTORY

Historical behavior may improve confidence, but it must NOT be a mandatory dependency.

A customer may be brand new.

Therefore:

```text
Strong direct evidence
        >
Supporting historical evidence
```

History is an optional confidence booster.

The system must work for:

- New customers
- Returning customers
- Customers with no previous payments
- Customers with irregular payment behavior
- Different industries

---

# 11. PRODUCT MVP

The MVP should focus on:

1. Authentication
2. Company workspace / multi-tenancy
3. Customer management
4. Invoice management
5. Invoice upload
6. OCR
7. Invoice database
8. Bank statement CSV import
9. Payment management
10. Reconciliation Engine
11. Evidence and confidence
12. Review Center
13. Dashboard
14. Audit logs
15. Basic settings

Initially exclude:

- Live bank APIs
- Gmail integration
- Outlook integration
- ERP integrations
- Advanced forecasting
- Advanced analytics
- Mobile application
- Complex AI copilot
- Multi-currency unless genuinely required by MVP
- Microservices

These can come later.

---

# 12. TARGET USERS

Primary:

- Accountants
- Finance teams
- SMB owners
- Accounting firms

Secondary:

- Finance managers
- Controllers
- SMB CFOs

Think about the actual workflows of these users.

Do not design the product only from an engineering perspective.

---

# 13. YOU MUST THINK LIKE A PRODUCT MANAGER

Before designing technology, answer:

### Customer

Who exactly uses this?

### Problem

What exact painful workflow are they currently performing?

### Existing alternatives

How do they solve it today?

### Value

Why would they pay for this?

### Workflow

What happens before our product?

What happens inside our product?

What happens after reconciliation?

### Adoption

What would make an accountant trust or reject the product?

### Failure

What happens when the system is wrong?

### UX

How many clicks should an accountant need for common reconciliation tasks?

### Success

Which metrics prove that the product is actually valuable?

Examples:

- Auto-match rate
- Reconciliation accuracy
- Manual-review rate
- Processing time
- OCR accuracy
- Time saved
- Estimated labor savings
- Customer retention

Challenge assumptions instead of blindly accepting them.

---

# 14. YOU MUST ALSO THINK LIKE A STAFF ENGINEER / ARCHITECT

Design for:

- Data integrity
- Multi-tenancy
- Security
- Reliability
- Observability
- Failure recovery
- Idempotency
- Concurrency
- Transaction safety
- Auditability
- Scalability
- Testability
- Maintainability

Do not optimize for theoretical scale at the cost of unnecessary complexity.

---

# 15. ARCHITECTURE DIRECTION

Start with:

## Modular Monolith + Clean Architecture + DDD Lite

Preferred stack:

### Frontend

Next.js + React + TypeScript

### Backend

FastAPI + Python

### Database

PostgreSQL

### Cache

Redis

### Object storage

S3-compatible storage

### OCR

Provider abstraction supporting options such as:

- PaddleOCR
- Azure Document Intelligence
- Google Document AI

### LLM

Provider abstraction supporting:

- GPT
- Gemini
- potentially other providers later

### Background jobs

Use background processing for:

- OCR
- Statement import
- Reconciliation
- Exports
- Other long-running operations

Keep workers within the modular monolith initially.

---

# 16. MULTI-TENANCY

This is a financial SaaS.

Company data isolation is mandatory.

Every request must belong to exactly one company/tenant.

Cross-company access must never be possible.

Design:

```text
Company A
   ↓
Customers
Invoices
Payments
Matches

Company B
   ↓
Customers
Invoices
Payments
Matches
```

No accidental cross-tenant queries.

Design tenant isolation at:

- Domain level
- Application level
- Repository/query level
- API authorization level
- Database level where appropriate

---

# 17. DATABASE DESIGN

Do NOT jump directly into SQL tables.

First design the domain.

Then derive the database schema.

Potential entities include:

```text
Company
User
Customer
CustomerAlias
CustomerBankAccount
Invoice
InvoiceItem
Payment
PaymentAllocation
Evidence
ReconciliationMatch
Review
AuditLog
```

Do not blindly create every possible table.

Determine whether each entity is genuinely required.

For each entity define:

- Purpose
- Fields
- Relationships
- Ownership
- Lifecycle
- Constraints
- Indexes
- Unique constraints
- Foreign keys
- Tenant boundaries
- Audit requirements

Financial amounts must be modeled safely.

Think carefully about:

- Decimal precision
- Currency
- Monetary calculations
- Rounding
- Idempotency
- Duplicate transactions

---

# 18. SYSTEM DESIGN ARTIFACTS

Before implementation, create proper design documents.

At minimum:

```text
docs/
│
├── product/
│   ├── PRD.md
│   ├── product-boundaries.md
│   ├── user-personas.md
│   ├── user-journeys.md
│   └── success-metrics.md
│
├── architecture/
│   ├── Architecture.md
│   ├── system-design.md
│   ├── module-responsibilities.md
│   ├── data-flow.md
│   ├── security-architecture.md
│   ├── error-architecture.md
│   ├── observability.md
│   └── scalability.md
│
├── domain/
│   ├── DDD.md
│   ├── business-rules.md
│   ├── domain-model.md
│   └── reconciliation-domain.md
│
├── database/
│   ├── Database.md
│   ├── schema.md
│   ├── ERD.md
│   └── data-integrity.md
│
├── api/
│   ├── API.md
│   ├── contracts.md
│   └── error-codes.md
│
├── modules/
│   ├── auth.md
│   ├── customer.md
│   ├── invoice.md
│   ├── payment.md
│   ├── reconciliation.md
│   └── review.md
│
└── roadmap/
    ├── implementation-roadmap.md
    └── phase-definitions.md
```

You may change this structure if you can justify a better one.

Do not blindly follow it.

---

# 19. DESIGN ORDER

Do NOT design everything randomly.

Follow this dependency order:

```text
Product Boundaries
        ↓
Product Requirements
        ↓
User Journeys
        ↓
Business Domain
        ↓
Business Rules
        ↓
Business Flows
        ↓
Domain Model
        ↓
System Architecture
        ↓
Module Boundaries
        ↓
Data Model
        ↓
Database Schema
        ↓
API Contracts
        ↓
Security Architecture
        ↓
Error Architecture
        ↓
Observability
        ↓
UI/UX Architecture
        ↓
Detailed Module Design
        ↓
Implementation Roadmap
        ↓
Coding
```

Do not design the database first and discover business requirements afterward.

---

# 20. RECONCILIATION DESIGN MUST GO DEEP

Treat reconciliation as the core intellectual property of the product.

Design separately:

1. Payment Intake
2. Payer Identification
3. Candidate Generation
4. Candidate Filtering
5. Exact Matching
6. Partial Matching
7. Multi-invoice Matching
8. Combination Search
9. Evidence Collection
10. Evidence normalization
11. Evidence scoring
12. Confidence calculation
13. Decision policy
14. Human review
15. Manual override
16. Audit trail
17. Duplicate detection
18. Idempotency
19. Historical payment intelligence
20. Explainability

For every stage specify:

- Inputs
- Outputs
- Algorithm
- Complexity
- Constraints
- Failure modes
- Edge cases
- Tests

---

# 21. SECURITY

Treat this as a commercial financial SaaS.

Design:

- Authentication
- Authorization
- Tenant isolation
- Password security
- Session management
- Token rotation/revocation
- Rate limiting
- Secrets management
- Encryption
- Secure file storage
- Sensitive-data handling
- Input validation
- Output validation
- Secure logging
- Audit logs
- API security
- File upload security
- OCR security
- LLM data isolation
- Prompt injection considerations for untrusted documents/emails

Do not claim regulatory compliance unless it is actually implemented and verified.

---

# 22. ERROR ARCHITECTURE

Clearly distinguish:

### Validation errors

Invalid input.

### Business errors

For example:

- Invoice already paid
- Invalid allocation
- Customer not found
- Ambiguous reconciliation

### Infrastructure errors

For example:

- Database unavailable
- OCR provider unavailable
- Storage unavailable

### Unexpected errors

Unknown failures.

Define consistent error contracts.

---

# 23. RELIABILITY

Financial operations must be safe under:

- Duplicate requests
- Retry
- Network failure
- Worker restart
- Partial failure
- Concurrent reconciliation
- Duplicate bank statement upload
- Duplicate payment import

Think deeply about idempotency.

A payment must not accidentally be allocated twice because a job ran twice.

---

# 24. OBSERVABILITY

Design:

- Structured logs
- Metrics
- Request IDs
- Correlation IDs
- Error tracking
- Reconciliation metrics
- OCR metrics
- Job metrics
- Database metrics
- Audit trails

For reconciliation specifically track things like:

- Candidate count
- Matching latency
- Confidence distribution
- Auto-match rate
- Review rate
- Rejection rate
- False-match rate
- Evidence availability
- Algorithm version

---

# 25. TESTING

Do not only test happy paths.

Every phase must include:

### Unit tests

Business rules and algorithms.

### Integration tests

Database/API/module interactions.

### End-to-end tests

Real user workflows.

### Edge-case tests

Examples:

- Same customer names
- Same invoice amounts
- Multiple invoices with identical amounts
- Partial payments
- Overpayments
- Duplicate payments
- Missing references
- Missing payer
- Conflicting evidence
- Old invoices
- Cancelled invoices
- Already-paid invoices
- Currency mismatch
- Concurrent matching
- Duplicate imports

### Security tests

Tenant isolation and authorization must be tested explicitly.

---

# 26. PHASE COMPLETION RULE

A phase is NOT complete merely because the code works.

Every phase must go through:

```text
Requirements
    ↓
Architecture
    ↓
Data Design
    ↓
API Contract
    ↓
Implementation
    ↓
Unit Tests
    ↓
Integration Tests
    ↓
Edge Cases
    ↓
Security Review
    ↓
Performance Review
    ↓
Debugging
    ↓
Regression Testing
    ↓
Documentation
    ↓
Code Review
    ↓
Memory Update
    ↓
FREEZE
```

If important bugs remain, the phase is not frozen.

---

# 27. PROJECT MEMORY

Maintain project memory/documentation as a source of truth.

After a phase is genuinely completed and frozen, record:

- What was built
- Why it was built
- Files/modules added
- Architecture decisions
- Business rules
- APIs
- Database changes
- Tests
- Bugs discovered
- Fixes
- Security decisions
- Performance decisions
- Known limitations
- Dependencies
- Future implications
- Phase status

Do not write meaningless summaries.

A new engineer should be able to understand the project's current state from the documentation.

---

# 28. YOUR FIRST TASK

Do NOT start coding.

Do NOT create authentication code.

Do NOT create database migrations.

Do NOT invent additional product features.

First act as the **Product Manager + Principal Architect**.

Study the entire brief and produce a complete **Pre-Implementation Product & Architecture Blueprint**.

The blueprint must include:

## A. Product Understanding

- What are we building?
- Why?
- For whom?
- What problem?
- Why existing solutions are insufficient?
- What is our differentiation?
- What is the MVP?
- What is explicitly outside the MVP?

## B. Product Analysis

Challenge the assumptions.

Identify:

- Weak assumptions
- Ambiguous requirements
- Missing requirements
- Contradictions
- Risks
- Product decisions that need to be frozen

Do NOT blindly agree with the founder.

If something is technically or commercially weak, say so and propose a better alternative.

## C. Complete User Journeys

Map the product from:

Signup → Workspace → Customer → Invoice → Payment → Reconciliation → Review → Approval → Audit.

Include happy paths and failure paths.

## D. Domain Model

Define:

- Entities
- Relationships
- Responsibilities
- Lifecycles
- Business rules
- Invariants
- Ownership boundaries

## E. System Architecture

Produce:

- High-level architecture
- Component architecture
- Module boundaries
- Layer responsibilities
- Data flow
- External integrations
- Background jobs
- Storage architecture
- Security boundaries
- Failure boundaries

## F. Database Architecture

Produce:

- Complete ERD
- Tables
- Columns
- Data types
- Primary keys
- Foreign keys
- Unique constraints
- Indexes
- Tenant isolation strategy
- Monetary data strategy
- Audit strategy
- Idempotency strategy

## G. API Architecture

Define:

- Endpoint groups
- Endpoint responsibilities
- Request/response contracts
- Authentication
- Authorization
- Validation
- Error responses
- Idempotency requirements

## H. Reconciliation Engine Design

This must be extremely detailed.

Define:

- Payer identification
- Candidate generation
- Matching
- Evidence collection
- Evidence normalization
- Evidence scoring
- Confidence
- Decision policy
- Partial payment
- Multi-invoice allocation
- Duplicate handling
- Manual review
- Human override
- Audit trail
- Algorithm versioning
- Explainability

## I. AI/LLM Architecture

Clearly define:

### What algorithm does

and

### What LLM does

Create strict interfaces between them.

Show how structured outputs from algorithms become inputs to LLM explanation.

Prevent the LLM from overriding financial decisions.

## J. Security Architecture

Design production-grade security boundaries.

## K. Reliability & Failure Handling

Define how the system behaves when:

- Database fails
- OCR fails
- LLM fails
- Bank statement is malformed
- Duplicate payment arrives
- Worker crashes
- Request is retried
- Two processes reconcile the same payment

## L. Testing Architecture

Define test strategy at:

- Unit
- Integration
- E2E
- Security
- Performance
- Reconciliation accuracy

## M. Observability

Define:

- Logs
- Metrics
- Tracing
- Audit
- Alerts
- Reconciliation-specific telemetry

## N. Folder Structure

Only after all previous design work, propose the final repository structure.

## O. Documentation Structure

Define exactly which architecture/product/database/API documents should exist.

## P. Implementation Roadmap

Create a dependency-aware roadmap.

Break large phases into smaller implementation phases where necessary.

The roadmap must be based on architectural dependencies and customer value, not arbitrary numbering.

---

# 29. IMPORTANT: DO NOT OVER-DESIGN

If you recommend something, explain:

1. What problem it solves.
2. Why we need it now.
3. What simpler alternative exists.
4. Why the simpler alternative is insufficient.

If something can remain inside the modular monolith, keep it there.

If something can be a simple PostgreSQL table, don't introduce another datastore.

If deterministic code is enough, don't use an LLM.

If an external service is better than building a complex subsystem ourselves, say so.

---

# 30. FINAL OUTPUT

Your first response should NOT contain implementation code.

Instead, produce the complete architecture/product blueprint and clearly separate:

- **Confirmed decisions**
- **Recommended decisions**
- **Open questions**
- **Risks**
- **Assumptions**
- **Rejected alternatives**

Then propose the exact sequence in which we should create the design documents.

Only after the architecture is reviewed and frozen should implementation begin.

Your role is not to simply execute instructions.

Your role is to help us build a **commercially viable, trustworthy, production-grade invoice reconciliation product** while challenging bad product and engineering decisions before they become expensive code.