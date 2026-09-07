# Implementation Phases

# AI Invoice Reconciliation Platform

**Version:** 1.0  
**Status:** Roadmap / Freeze Candidate  
**Purpose:** Dependency-aware product, architecture, and implementation roadmap

---

# 1. Roadmap Philosophy

This roadmap defines how the AI Invoice Reconciliation Platform will move from product definition to a production-ready MVP and then to future platform capabilities.

The roadmap follows four principles:

1. **Product value before technical complexity.**
2. **Architecture before implementation.**
3. **Core financial workflow before secondary features.**
4. **Every phase is tested, debugged, documented, and frozen before dependent work proceeds.**

The product is intentionally a **modular monolith** rather than a premature distributed system.

The Reconciliation Engine receives the largest amount of engineering attention because it is the core product capability and competitive differentiator.

---

# 2. Important Roadmap Distinction

There are two different kinds of phases:

## A. Design / Architecture Phases

These establish what the system should be.

They produce:

- Product requirements
- Domain definitions
- Architecture
- Database design
- API contracts
- Security design
- Error architecture
- Observability design
- UI/UX design
- Implementation plan

## B. Development Phases

These implement the frozen design.

A development phase is not considered complete when code merely works.

It must pass:

```text
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
Memory Update
↓
Freeze
```

---

# 3. Current Design Foundation

Already established:

- `PRD.md`
- `Architecture.md`
- `design.md`

These define the current product, system architecture, and UX direction.

The next design artifacts should derive from them rather than independently inventing new requirements.

---

# 4. Phase 0 — Product Definition & Freeze

## Goal

Define exactly what product is being built before technical implementation begins.

## Scope

- Product vision
- Problem statement
- Target users
- Jobs-to-be-done
- Product boundaries
- MVP
- Non-goals
- Success metrics
- AI vs algorithm boundary
- Human-control principle
- Product risks
- Major assumptions

## Deliverables

- PRD
- Product boundaries
- User personas
- User journeys
- Success metrics
- Initial roadmap

## Exit Criteria

- Product scope understood
- MVP clearly defined
- Non-goals explicitly defined
- Major product contradictions resolved

## Freeze

**Phase 0 → Product Frozen**

---

# 5. Phase 1 — Domain & Business Model Design

## Goal

Define the business language and financial concepts before designing the database.

## Scope

Define:

- Company
- User
- Customer
- Customer Alias
- Customer Payment Identifier
- Invoice
- Invoice Item
- Payment
- Payment Allocation
- Reconciliation
- Candidate
- Evidence
- Decision
- Review
- Audit Record

## Define for each concept

- Responsibility
- Ownership
- Lifecycle
- Relationships
- Invariants
- Allowed state transitions

## Special Focus

Financial state transitions must be explicit.

Example:

```text
Pending
   ↓
Partially Paid
   ↓
Paid
```

Invalid transitions must also be defined.

## Deliverables

- `domain-model.md`
- `business-rules.md`
- Domain glossary
- Entity lifecycle definitions
- State transition definitions

## Exit Criteria

- No major domain ambiguity
- Business rules are explicit
- Entity ownership is clear
- Financial invariants are documented

## Freeze

**Phase 1 → Domain Frozen**

---

# 6. Phase 2 — System Architecture

## Goal

Translate the product and domain into a technical system.

## Scope

- Modular monolith
- Clean Architecture
- DDD Lite
- Module boundaries
- Dependency rules
- Infrastructure boundaries
- Background processing
- Storage
- OCR boundary
- LLM boundary
- External integrations
- Multi-tenancy
- Reliability
- Scalability

## Deliverables

- `Architecture.md`
- System architecture diagram
- Component diagram
- Module responsibility matrix
- Data-flow diagrams
- Integration boundary definitions

## Exit Criteria

- Module ownership clear
- No circular business dependencies
- Infrastructure boundaries defined
- AI/algorithm boundary explicit
- Multi-tenancy strategy defined

## Freeze

**Phase 2 → Architecture Frozen**

---

# 7. Phase 3 — Database Architecture

## Goal

Derive the persistence model from the domain.

## Scope

Design:

- Tables
- Columns
- Types
- Primary keys
- Foreign keys
- Unique constraints
- Check constraints
- Indexes
- Partial indexes where justified
- Tenant boundaries
- Monetary precision
- Audit data
- Idempotency data
- Relationships

## Core initial data areas

```text
Users
Companies
Customers
Invoices
Payments
Reconciliation Matches
Audit Logs
```

Additional tables may be introduced only when the domain requires them.

## Deliverables

- `database/schema.md`
- `database/ERD.md`
- Data-integrity rules
- Migration strategy

## Exit Criteria

- Schema represents domain correctly
- Financial amounts are modeled safely
- Tenant isolation is enforceable
- Duplicate-processing constraints are defined
- Important relationships are constrained

## Freeze

**Phase 3 → Database Frozen**

---

# 8. Phase 4 — API Contract Design

## Goal

Define the frontend/backend contract before implementation.

## Scope

API groups:

- Authentication
- Company
- Customers
- Invoices
- Payments
- Reconciliation
- Review
- Dashboard
- Audit
- Settings

For every endpoint define:

- Method
- Path
- Request
- Response
- Validation
- Authentication
- Authorization
- Error responses
- Idempotency requirements
- Pagination where applicable

## Deliverables

- `api/contracts.md`
- Endpoint list
- Error-code specification
- OpenAPI direction

## Exit Criteria

- Core user journeys have API support
- No endpoint owns business logic that belongs elsewhere
- Error contracts are consistent

## Freeze

**Phase 4 → API Contracts Frozen**

---

# 9. Phase 5 — Security Architecture

## Goal

Define security before financial data is processed.

## Scope

- Authentication
- Authorization
- Tenant isolation
- Password security
- Session/token security
- Rate limiting
- Secrets
- File upload security
- Sensitive-data handling
- API security
- Audit requirements
- LLM data boundary
- Untrusted document handling

## Deliverables

- `security-architecture.md`
- Threat model
- Security controls
- Tenant-isolation test plan

## Exit Criteria

- Threats identified
- Core controls defined
- Tenant escape scenarios addressed
- Sensitive-data handling defined

## Freeze

**Phase 5 → Security Architecture Frozen**

---

# 10. Phase 6 — Error & Reliability Architecture

## Goal

Define how the system behaves when things go wrong.

## Error Categories

```text
Validation Error
Domain Error
Authorization Error
Authentication Error
Infrastructure Error
Unexpected Error
```

## Reliability Scope

- Transactions
- Idempotency
- Retries
- Worker failure
- Provider timeout
- Duplicate imports
- Concurrent processing
- Partial failure
- Recovery

## Deliverables

- `error-architecture.md`
- Error taxonomy
- Retry policy
- Idempotency strategy
- Failure-state definitions

## Freeze

**Phase 6 → Reliability Design Frozen**

---

# 11. Phase 7 — Storage & External Provider Architecture

## Goal

Define how documents and external capabilities are integrated.

## Scope

### Object Storage

- Invoice PDFs
- OCR artifacts where required
- Evidence documents
- Exports

### OCR

Provider-independent adapter.

### LLM

Provider-independent adapter.

### Email

Future provider abstraction.

### Accounting Integrations

Future adapter boundary.

## Deliverables

- Storage design
- OCR adapter design
- LLM adapter design
- Integration-port definitions

## Freeze

**Phase 7 → Integration Architecture Frozen**

---

# 12. Phase 8 — Backend Architecture & Project Foundation

## Goal

Create the executable backend foundation without implementing business features.

## Scope

- Repository structure
- FastAPI bootstrap
- Configuration
- Dependency management
- Logging foundation
- Global exception handling
- Database initialization
- Alembic initialization
- Health endpoint
- Testing foundation
- API versioning
- Request context
- Module skeletons

## Explicitly Do Not Implement

- Authentication business logic
- Invoice logic
- Payment logic
- Reconciliation
- OCR processing
- LLM processing

## Deliverable

A runnable backend foundation.

## Verification

- Application starts
- Health endpoint works
- Tests run
- Lint passes
- Type checking passes
- No business logic accidentally introduced

## Freeze

**Phase 8 → Engineering Foundation Frozen**

---

# 13. Phase 9 — Authentication & Company Workspace

## Goal

Establish secure user access and tenant boundaries.

## Sub-phases

### 9.1 Authentication Foundation

- Auth module structure
- Contracts
- Exceptions
- Dependencies

### 9.2 User & Company Domain

- User
- Company
- Roles
- Account status
- Identity value objects

### 9.3 Persistence

- SQLAlchemy models
- Repositories
- Migrations

### 9.4 Registration

- Company registration
- Owner account
- Password hashing
- Validation

### 9.5 Login

- Password verification
- Access token
- Refresh/session mechanism
- Logout

### 9.6 Email Verification

- Verification flow
- Resend

### 9.7 Password Reset

- Reset token
- Expiry
- Password change

### 9.8 User Invitation

- Owner invites accountant
- Invitation acceptance
- Membership

## Security Tests

- Unauthorized access
- Cross-tenant access
- Invalid tokens
- Expired tokens
- Repeated login failures
- Password handling

## Freeze

**Phase 9 → Authentication & Tenant Foundation Frozen**

---

# 14. Phase 10 — Customer Management

## Goal

Create the customer identity foundation required by reconciliation.

## Scope

- Customer CRUD
- Customer archive
- Customer aliases
- Known bank accounts
- UPI identifiers where available
- Customer search
- Customer details
- Payment history view
- Outstanding invoices view

## Critical Requirement

Customer management must support reconciliation without becoming a CRM.

## Verification

- Tenant isolation
- Duplicate/identity handling
- Search
- Archive behavior
- Identifier uniqueness where applicable

## Freeze

**Phase 10 → Customer Module Frozen**

---

# 15. Phase 11 — Invoice Management

## Goal

Create the authoritative invoice dataset.

## Scope

- PDF upload
- Manual entry
- CSV import where required
- Invoice validation
- Invoice CRUD
- Invoice status
- Outstanding balance
- Invoice history
- Source-document metadata

## States

```text
Pending
Partially Paid
Paid
Cancelled
```

## Critical Requirements

- Safe state transitions
- Monetary precision
- Tenant isolation
- Duplicate invoice handling
- Source-document traceability

## Freeze

**Phase 11 → Invoice Management Frozen**

---

# 16. Phase 12 — OCR & Document Processing

## Goal

Convert uploaded invoice documents into validated invoice data.

## Pipeline

```text
PDF
 ↓
Object Storage
 ↓
OCR Job
 ↓
OCR Provider
 ↓
Normalized Extraction
 ↓
Validation
 ↓
User Correction if Needed
 ↓
Invoice
```

## Scope

- OCR adapter
- Extraction schema
- Validation
- OCR status
- Retry/failure states
- Review UX
- Provider abstraction

## AI Boundary

OCR/LLM may extract information.

They do not decide financial reconciliation.

## Freeze

**Phase 12 → OCR Frozen**

---

# 17. Phase 13 — Payment & Bank Statement Management

## Goal

Create the authoritative payment dataset.

## MVP Input

```text
CSV Bank Statement
```

## Scope

- Upload
- Validation
- Column mapping where necessary
- Parsing
- Normalization
- Transaction persistence
- Duplicate detection
- Import status
- Payment list
- Payment details

## Transaction Fields

Potentially:

- Date
- Amount
- Narration
- Reference
- Credit/debit
- Balance
- UTR where available

## Freeze

**Phase 13 → Payment Module Frozen**

---

# 18. Phase 14 — Reconciliation Engine

# CORE PRODUCT PHASE

This is the most important implementation phase.

It must be split into multiple sub-phases.

---

## 14.1 Payment Intake

Validate whether a payment is eligible for reconciliation.

---

## 14.2 Payer Identification

Evaluate signals such as:

1. Bank account
2. UPI
3. Virtual account
4. Payment reference
5. UTR
6. Invoice/reference number
7. Customer name
8. Customer aliases
9. Historical mapping
10. Future unstructured evidence

Direct identifiers should have stronger authority than weak contextual signals.

Ambiguous payer:

```text
PAYER_AMBIGUOUS
```

→ Review.

---

## 14.3 Candidate Invoice Generation

Once payer identity is sufficiently established:

```text
Customer
 ↓
Outstanding eligible invoices
 ↓
Candidate set
```

Do not globally search unrelated customers unless explicitly required by a fallback strategy.

---

## 14.4 Candidate Filtering

Filter using:

- Tenant
- Customer
- Status
- Outstanding amount
- Currency
- Date constraints
- Other business rules

The goal is to keep expensive matching bounded.

---

## 14.5 Exact Matching

Support:

```text
Payment = Invoice Outstanding
```

This is the simplest high-confidence case.

---

## 14.6 Partial Payment Matching

Support:

```text
Invoice = ₹100,000
Payment = ₹40,000
```

Result:

```text
Applied = ₹40,000
Outstanding = ₹60,000
```

---

## 14.7 Multi-Invoice Matching

Support:

```text
INV-101 = ₹20,000
INV-102 = ₹30,000

Payment = ₹50,000
```

Potential allocation:

```text
INV-101 + INV-102
```

---

## 14.8 Combination Matching

For multiple outstanding invoices:

```text
10k
25k
15k
8k
12k
```

Payment:

```text
35k
```

Possible combinations must be evaluated.

Candidate search must be bounded to avoid combinatorial explosion.

---

## 14.9 Evidence Collection

Build structured evidence from:

- Customer identity
- Bank account
- UTR
- Reference
- Invoice number
- Amount
- Outstanding balance
- Dates
- Aliases
- Historical patterns
- Future email/PO context

Classify evidence:

```text
Direct
Supporting
Missing
Conflicting
```

---

## 14.10 Evidence Normalization

Convert raw signals into consistent evidence objects.

Each evidence object should contain:

- Type
- Source
- Result
- Strength
- Details
- Relevant identifiers
- Algorithm/rule version where required

---

## 14.11 Matching & Scoring

Calculate deterministic candidate scores.

Potential signals:

- Bank account match
- Invoice/reference match
- Amount match
- Customer name match
- Date proximity
- Historical behavior

Exact weights must be validated against real data.

---

## 14.12 Confidence Engine

Produce a deterministic score.

Important:

Confidence is not automatically a probability of correctness.

It becomes a calibrated probability only if statistically validated.

---

## 14.13 Decision Engine

Possible outcomes:

```text
MATCH_SUGGESTED
AUTO_ELIGIBLE
REVIEW_REQUIRED
AMBIGUOUS
NO_MATCH
DUPLICATE
INVALID
```

The final state vocabulary should be frozen in the reconciliation-domain design.

---

## 14.14 Decision Object

Produce a canonical structured result containing:

- Payment
- Candidate/allocation
- Decision
- Confidence
- Evidence
- Algorithm version
- Review requirement

---

## 14.15 Duplicate Protection

Ensure a payment cannot be allocated twice because of:

- Duplicate import
- Retry
- Concurrent worker
- Repeated API request

---

## 14.16 Reconciliation Audit

Every decision must preserve:

- Decision
- Evidence
- Score
- Algorithm/rule version
- Timestamp
- Processing context

## Freeze

**Phase 14 → Reconciliation Engine Frozen**

Only after extensive scenario testing.

---

# 19. Phase 15 — Review Center

## Goal

Turn reconciliation output into a fast human workflow.

## Scope

- Review queue
- Payment details
- Suggested customer
- Suggested invoices
- Evidence
- Confidence/status
- Conflict explanation
- Approve
- Reject
- Manual match
- Manual allocation
- Keep unresolved

## Safety

Approval must be transactional.

```text
Approve
 ↓
Validate
 ↓
Apply allocation
 ↓
Update invoice/payment state
 ↓
Audit
 ↓
Commit
```

Failure:

```text
Rollback
```

## Freeze

**Phase 15 → Review Center Frozen**

---

# 20. Phase 16 — Dashboard

## Goal

Provide operational visibility.

## MVP Metrics

- Pending invoices
- Paid invoices
- Total outstanding
- Payments imported
- Matched payments
- Unmatched payments
- Review queue

Do not build advanced analytics yet.

## Freeze

**Phase 16 → Dashboard Frozen**

---

# 21. Phase 17 — Audit & Compliance Foundation

## Goal

Make financial actions traceable.

## Scope

Record events such as:

- Login
- Invoice upload
- OCR completion
- Invoice edit
- Payment import
- Reconciliation execution
- Match suggestion
- Approval
- Rejection
- Manual override
- Invoice status change

Audit records should include where applicable:

- Timestamp
- Actor
- Company
- Entity
- Entity ID
- Action
- Before/after information
- Correlation ID
- Algorithm version

Audit history must be append-oriented and protected from ordinary modification.

## Freeze

**Phase 17 → Audit Frozen**

---

# 22. Phase 18 — Settings & Operational Controls

## Goal

Provide only the configuration necessary for the MVP.

## Scope

- Company
- Users
- Currency
- Timezone
- Relevant reconciliation settings
- Profile

Do not expose internal architecture as configuration.

## Freeze

**Phase 18 → MVP Settings Frozen**

---

# 23. Phase 19 — Production Hardening

## Goal

Prepare the MVP for real customer usage.

## Scope

### Security

- Security review
- Tenant-isolation tests
- File-upload security
- Authentication review
- Secrets review

### Reliability

- Retry behavior
- Failure recovery
- Idempotency
- Concurrency
- Transaction testing

### Performance

- Database indexes
- Query profiling
- Import performance
- Reconciliation performance
- Large candidate-set tests

### Observability

- Structured logs
- Metrics
- Health
- Error tracking
- Alerts

### Deployment

```text
Next.js
 ↓
FastAPI
 ↓
PostgreSQL
 ↓
Redis
 ↓
Object Storage
```

Avoid Kubernetes unless a concrete requirement emerges.

## Freeze

**Phase 19 → MVP Production Readiness Frozen**

---

# 24. MVP Completion Gate

The MVP is complete only when the complete workflow works:

```text
Register
 ↓
Company Workspace
 ↓
Create Customer
 ↓
Upload Invoice
 ↓
OCR / Validate
 ↓
Invoice Stored
 ↓
Upload Bank Statement
 ↓
Payments Stored
 ↓
Reconciliation
 ↓
Evidence
 ↓
Decision
 ↓
Review
 ↓
Approve
 ↓
Invoice State Updated
 ↓
Audit Record
```

And the system passes:

- Unit tests
- Integration tests
- End-to-end tests
- Security tests
- Edge-case tests
- Regression tests
- Production-readiness review

---

# 25. Reconciliation Testing Matrix

The reconciliation engine must explicitly test:

## Basic

- Exact invoice/payment match
- Customer identified
- Reference match

## Multi-Invoice

- Two invoices
- Three invoices
- Multiple valid combinations
- No valid combination

## Partial

- Partial invoice payment
- Multiple partial payments
- Remaining balance

## Ambiguity

- Same customer names
- Similar names
- Missing identifiers
- Conflicting evidence

## Duplicate

- Duplicate statement
- Duplicate payment
- Retry
- Concurrent reconciliation

## Edge

- Zero amount
- Negative/invalid transaction
- Cancelled invoice
- Already-paid invoice
- Currency mismatch
- Old invoice
- Overpayment
- Underpayment

No reconciliation phase is frozen until these classes of scenarios are tested.

---

# 26. AI Feature Roadmap

AI must remain bounded.

## MVP / Early

### OCR

Extract invoice information.

### Narration Understanding

Interpret unstructured bank narration where deterministic parsing is insufficient.

### Explanation

Explain an already-determined reconciliation result.

## Later

### Email Understanding

Interpret payment-related emails.

### Reminder Drafting

Generate customer reminder drafts.

### Natural-Language Search

Search business data using natural language.

The AI layer must not become the financial decision engine.

---

# 27. Future Integration Roadmap

After the MVP is stable:

## Phase F1 — Communication

- Gmail
- Outlook

## Phase F2 — Accounting

- Zoho Books
- QuickBooks
- Tally
- Other ERP systems

## Phase F3 — Payments

- Bank APIs
- Payment gateways
- Other payment providers

Each integration must be isolated behind an adapter.

---

# 28. Future Intelligence Roadmap

After sufficient production data exists:

- Customer payment profiles
- Pattern learning
- Auto-confidence tuning
- Anomaly detection
- Duplicate intelligence
- Advanced reconciliation optimization

These features must be driven by actual customer data and measured performance.

Do not build them merely because they are technically interesting.

---

# 29. Future Business Intelligence

Later capabilities:

- Time-saved measurement
- Labor-savings estimation
- Productivity reporting
- Operational reports
- Customer ROI reporting

The product must clearly distinguish:

```text
Measured
vs
Estimated
```

---

# 30. Enterprise Readiness Roadmap

Only after product-market validation:

- Advanced multi-company management
- Multi-currency
- SSO
- API keys
- Webhooks
- Advanced rate limits
- Backup strategy
- Disaster recovery
- Enterprise administration

---

# 31. Phase Documentation Contract

Every development phase must have a phase-history document.

Example:

```text
docs/
└── phase-history/
    ├── phase-08-foundation.md
    ├── phase-09-auth-company.md
    ├── phase-10-customer.md
    └── ...
```

Before a phase is marked frozen, its history must record:

- Objective
- Business problem
- Requirements
- Implementation
- Files/modules changed
- Architecture decisions
- Database changes
- APIs
- Tests
- Bugs
- Root causes
- Fixes
- Security review
- Performance review
- Known limitations
- Dependencies
- Verification evidence
- Final status

---

# 32. Project Memory Update

After every frozen phase:

1. Update project memory.
2. Record completed phase.
3. Record current architecture.
4. Record decisions.
5. Record known limitations.
6. Record remaining roadmap.
7. Link the detailed phase-history document.

Memory must not be updated prematurely.

The phase must first be:

```text
Implemented
+
Tested
+
Debugged
+
Reviewed
+
Verified
```

Then:

```text
Memory Update
↓
Freeze
```

---

# 33. Phase Freeze Rule

A phase is NOT frozen if:

- Tests fail.
- Important bugs remain.
- Security gaps remain unresolved.
- Critical edge cases are untested.
- Architecture contradicts the approved design.
- Financial integrity can be violated.
- Tenant isolation is not verified.
- Documentation is missing.
- Known behavior is not recorded.

The correct behavior is to remain in the current phase until the issue is resolved.

---

# 34. Dependency Graph

```text
Phase 0
Product
  ↓
Phase 1
Domain
  ↓
Phase 2
Architecture
  ↓
Phase 3
Database
  ↓
Phase 4
API Contracts
  ↓
Phase 5
Security
  ↓
Phase 6
Reliability
  ↓
Phase 7
Integrations/Storage
  ↓
Phase 8
Engineering Foundation
  ↓
Phase 9
Auth + Company
  ↓
Phase 10
Customer
  ↓
Phase 11
Invoice
  ↓
Phase 12
OCR
  ↓
Phase 13
Payment
  ↓
Phase 14
Reconciliation ⭐
  ↓
Phase 15
Review
  ↓
Phase 16
Dashboard
  ↓
Phase 17
Audit
  ↓
Phase 18
Settings
  ↓
Phase 19
Production Hardening
  ↓
MVP
```

---

# 35. Why Reconciliation Comes Late

The Reconciliation Engine depends on:

```text
Company
Customer
Invoice
Payment
```

Therefore it should not be implemented before those authoritative datasets exist.

However, reconciliation should be **designed early**, because its requirements influence:

- Customer identifiers
- Invoice fields
- Payment fields
- Database constraints
- Evidence
- Audit
- APIs

Therefore:

> **Design reconciliation early. Implement reconciliation after its dependencies are stable.**

---

# 36. Development Execution Strategy

Development should proceed in small increments.

For each implementation phase:

```text
Read frozen design
 ↓
Create implementation plan
 ↓
Implement smallest slice
 ↓
Run tests
 ↓
Debug
 ↓
Run regression tests
 ↓
Review architecture
 ↓
Update documentation
 ↓
Freeze
```

Do not implement multiple unrelated modules simultaneously unless their dependency relationship requires it.

---

# 37. Suggested Coding Sprints

The development phases can be grouped into practical sprints:

## Sprint 1

Foundation

- Project bootstrap
- Config
- Logging
- Error handling
- Database infrastructure
- Testing

## Sprint 2

Authentication + Company

## Sprint 3

Customer

## Sprint 4

Invoice

## Sprint 5

OCR

## Sprint 6

Payment Import

## Sprint 7+

Reconciliation Engine

Because reconciliation is the largest module, it should be divided into multiple implementation sprints.

## Following

- Review
- Dashboard
- Audit
- Settings
- Production hardening

The exact sprint duration should be determined by actual implementation progress, not assumed calendar estimates.

---

# 38. Core Engineering Rule

Never optimize the roadmap for:

> "How quickly can we write all the code?"

Optimize it for:

> "How quickly can we reach a trustworthy product without creating architectural debt that blocks us later?"

---

# 39. Final Roadmap Summary

```text
PRODUCT / DESIGN
────────────────────────────────────────

0   Product Definition
1   Domain & Business Model
2   System Architecture
3   Database Architecture
4   API Contracts
5   Security Architecture
6   Error & Reliability Architecture
7   Storage & Integration Architecture


IMPLEMENTATION
────────────────────────────────────────

8   Engineering Foundation
9   Authentication + Company
10  Customer
11  Invoice
12  OCR
13  Payment
14  Reconciliation ⭐⭐⭐⭐⭐
15  Review Center
16  Dashboard
17  Audit
18  Settings
19  Production Hardening


POST-MVP
────────────────────────────────────────

F1  Gmail / Outlook
F2  Accounting Integrations
F3  Bank / Payment Integrations
F4  AI Extensions
F5  Intelligence
F6  Business Intelligence
F7  Enterprise Readiness
```

---

# 40. Final Principle

The roadmap is intentionally conservative.

The product should first become:

> **A reliable invoice + payment reconciliation product.**

Then:

> **A highly intelligent reconciliation product.**

Then:

> **A broader financial-operations platform.**

Do not reverse that order.

The Reconciliation Engine is the product's core moat, but it must be built on trustworthy customer, invoice, payment, evidence, transaction, and audit foundations.

The ultimate development rule is:

> **Build the smallest trustworthy system first. Then make it smarter. Then make it broader.**
