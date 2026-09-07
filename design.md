# Product Design Document

# AI Invoice Reconciliation Platform

**Version:** 1.0  
**Status:** Draft / Design-Freeze Candidate  
**Derived From:** PRD.md, Architecture.md, Product Design Process, Product Roadmap

---

# 1. Purpose

This document defines the product and UX design of the AI Invoice Reconciliation Platform.

It answers:

- How users interact with the product.
- What each screen is responsible for.
- How information is presented.
- How reconciliation decisions are communicated.
- How uncertainty is represented.
- How users review and approve financial decisions.
- How the product keeps workflows simple for accountants and finance users.

This document is a **product design specification**, not a frontend implementation document.

The design must remain consistent with the product boundary and system architecture.

---

# 2. Design Philosophy

The product is financial software.

Therefore the interface must optimize for:

1. Trust
2. Clarity
3. Accuracy
4. Speed
5. Evidence
6. Human control
7. Low cognitive load

The UI should not attempt to look "AI-powered" at the expense of clarity.

The user should understand:

> What happened?

> What does the system suggest?

> Why?

> What evidence supports it?

> What do I need to do?

The product should never hide uncertainty behind confident language.

---

# 3. Core Design Principles

## 3.1 Problem First

Every screen must support a real user workflow.

No decorative feature should exist without a clear purpose.

---

## 3.2 Simple UI

The product is designed for accountants and finance teams.

The user should not need to understand:

- Algorithms
- LLMs
- OCR internals
- Database architecture
- Confidence formulas

The system should expose outcomes and evidence, not implementation complexity.

---

## 3.3 Evidence Before Explanation

The system should first show structured evidence.

Natural-language AI explanations are secondary.

Example:

```text
Matched customer       ✓
Amount matched         ✓
Invoice reference      ✓
Historical pattern     Supporting
```

Then:

> "This payment was suggested for these invoices because..."

---

## 3.4 Human Control

Users must always understand when the system is asking them to make a decision.

Never use:

> "AI decided this is correct."

Prefer:

> "Suggested match"

and:

> "Review required"

---

## 3.5 Conservative Visual Language

Avoid visual patterns that make uncertain financial decisions look certain.

A high score may be shown as strong evidence, but confidence must not visually imply guaranteed correctness.

---

## 3.6 Progressive Disclosure

The primary screen should remain simple.

Additional detail can be revealed when requested.

Example:

```text
Payment
↓
Suggested Match
↓
Why?
↓
Evidence Details
↓
Raw Source
```

---

# 4. Primary User

The primary UX is designed around the accountant.

The accountant's core workflow is:

```text
Import
   ↓
Inspect
   ↓
Reconcile
   ↓
Review exceptions
   ↓
Approve
   ↓
Continue
```

The system should minimize repetitive navigation.

---

# 5. Core User Journey

```text
Login
  ↓
Dashboard
  ↓
Import invoices
  ↓
Invoices become available
  ↓
Import bank statement
  ↓
Payments processed
  ↓
Reconciliation runs
  ↓
Results appear
  ↓
Review Center
  ↓
Approve / Reject / Manual Match
  ↓
Updated financial state
  ↓
Audit trail
```

---

# 6. Information Architecture

Initial application structure:

```text
App
│
├── Dashboard
│
├── Customers
│   └── Customer Details
│
├── Invoices
│   ├── Invoice List
│   ├── Invoice Details
│   └── Upload Invoice
│
├── Payments
│   ├── Payment List
│   └── Upload Statement
│
├── Review Center
│
├── Audit
│
└── Settings
    ├── Company
    ├── Users
    └── Preferences
```

Authentication exists outside the main application workspace.

---

# 7. Global Application Shell

After login, the application should use a consistent workspace shell.

Conceptually:

```text
┌─────────────────────────────────────────────────────┐
│ Logo / Workspace                     User / Settings│
├──────────────┬──────────────────────────────────────┤
│              │                                      │
│ Dashboard    │                                      │
│ Customers    │             Main Content             │
│ Invoices     │                                      │
│ Payments     │                                      │
│ Review       │                                      │
│ Audit        │                                      │
│              │                                      │
└──────────────┴──────────────────────────────────────┘
```

The navigation should remain predictable across screens.

---

# 8. Navigation Priorities

The most important destinations are:

1. Review Center
2. Dashboard
3. Payments
4. Invoices
5. Customers

Review Center deserves high visual priority because it represents the user's outstanding decisions.

---

# 9. Authentication Screens

## 9.1 Login

Purpose:

Allow existing users to access their workspace.

Fields:

- Email
- Password

Actions:

- Sign in
- Forgot password

Secondary:

- Create account

---

## 9.2 Registration

Purpose:

Create the initial company workspace and owner account.

Fields should remain limited to information required to start.

After successful registration:

```text
Account
 ↓
Company Workspace
 ↓
Dashboard
```

---

## 9.3 Password Reset

Simple workflow:

```text
Email
 ↓
Reset Link
 ↓
New Password
 ↓
Confirmation
```

---

# 10. Dashboard Design

The dashboard is an operational overview, not an analytics product.

Primary cards:

```text
Outstanding Invoices
Paid
Unmatched Payments
Needs Review
```

Potential additional operational information:

```text
Payments Imported
Invoices Processed
Recent Activity
```

---

## 10.1 Dashboard Hierarchy

Recommended hierarchy:

### First

What requires attention now?

### Second

What is the current financial state?

### Third

What has the system processed?

### Fourth

Recent activity.

The dashboard should not overwhelm users with dozens of charts.

---

# 11. Customer List

The customer list should help users quickly find customers.

Columns may include:

- Customer
- Outstanding
- Open invoices
- Last payment
- Status

Actions:

- Open customer
- Search
- Filter
- Add customer

---

# 12. Customer Details

Customer details should combine identity and reconciliation context.

Structure:

```text
Customer Header
   ↓
Summary
   ↓
Outstanding Invoices
   ↓
Payment History
   ↓
Known Payment Identifiers
   ↓
Customer Information
```

Important information:

- Customer name
- GST/PAN where applicable
- Aliases
- Known bank accounts
- Payment history
- Outstanding invoices

This screen should not become a CRM.

---

# 13. Invoice List

The invoice list should prioritize operational state.

Useful columns:

- Invoice number
- Customer
- Date
- Due date
- Amount
- Outstanding
- Status

Filters:

- Status
- Customer
- Date
- Overdue
- Outstanding

Primary actions:

- Upload invoice
- Open invoice
- Search
- Filter

---

# 14. Invoice Details

The invoice details page should show:

```text
Invoice Header
      ↓
Customer
      ↓
Amounts
      ↓
Dates
      ↓
Outstanding Balance
      ↓
Source Document
      ↓
Payment History
      ↓
Audit / History
```

The original uploaded invoice document should be accessible.

---

# 15. Invoice Upload Flow

The upload experience should minimize friction.

```text
Upload
 ↓
File Validation
 ↓
Processing
 ↓
OCR
 ↓
Extracted Data
 ↓
Validation
 ↓
User Confirmation if Needed
 ↓
Invoice Created
```

The user should not need to wait on a long-running OCR request inside the browser.

---

# 16. OCR Review UX

OCR results should be presented as extracted fields with clear status.

Example:

```text
Invoice Number       INV-101        ✓
Customer             ABC Pvt Ltd    ✓
Amount               ₹35,000        ✓
Issue Date           12 Jul         ✓
Due Date             11 Aug         ✓
```

If extraction is uncertain:

```text
Amount               ₹35,000        ⚠ Review
```

The user should be able to correct extracted values before the invoice becomes trusted data.

---

# 17. Payment / Bank Statement Screen

The payment screen represents imported bank transactions.

Columns may include:

- Date
- Amount
- Payer / narration
- Reference
- Status

Statuses:

```text
Imported
Matched
Review
Rejected
```

The list should allow filtering by status.

---

# 18. Bank Statement Upload Flow

```text
Select CSV
   ↓
Validate File
   ↓
Preview
   ↓
Map/Confirm Columns if Required
   ↓
Import
   ↓
Duplicate Detection
   ↓
Payment Records
   ↓
Reconciliation
```

The user should see import progress and failures.

For invalid rows, show:

- Row
- Problem
- Suggested correction if deterministic
- Skip/retry behavior

---

# 19. Reconciliation Result UX

A reconciliation result should communicate four things immediately:

1. Payment
2. Suggested match
3. Confidence/status
4. Reason/evidence

Example:

```text
Payment
₹35,000
ABC Technologies

Suggested Match
INV-101 + INV-102

Confidence
98%

Why?
✓ Customer identified
✓ Amount exactly matches
✓ Eligible invoices found
✓ Supporting historical pattern
```

Actions:

```text
Approve
Reject
Review Details
Manual Match
```

---

# 20. Confidence Presentation

Do not treat confidence as a universal probability unless statistically calibrated.

Use product language such as:

- Strong match
- Likely match
- Needs review
- No reliable match

A numerical score can be shown as secondary information.

Example:

```text
Strong match
98 score
```

rather than:

```text
98% guaranteed correct
```

---

# 21. Review Center

The Review Center is the most important operational screen after reconciliation.

It should answer:

> What requires my attention?

The screen should present a queue.

Conceptually:

```text
Review Center

12 items need review

┌────────────────────────────────────────────────────┐
│ Payment       Suggested Match       Reason   Action │
│ ₹35,000       INV-101 + INV-102     Amount   Review│
│ ₹12,500       Unknown               Payer    Review│
│ ₹50,000       INV-201               Conflict Review│
└────────────────────────────────────────────────────┘
```

---

# 22. Review Item Details

Selecting a review item opens a focused reconciliation workspace.

Recommended layout:

```text
┌──────────────────────────────────────────────────────┐
│ Payment Details                                      │
│ ₹35,000 | 23 Jul | ABC TECH                          │
├───────────────────────┬──────────────────────────────┤
│ Suggested Match       │ Evidence                     │
│                       │                              │
│ INV-101  ₹10,000      │ Customer       ✓             │
│ INV-102  ₹25,000      │ Amount         ✓             │
│                       │ Reference      —             │
│ Total    ₹35,000      │ History        Supporting    │
│                       │                              │
│ Confidence: Strong    │                              │
├───────────────────────┴──────────────────────────────┤
│ [Approve] [Reject] [Manual Match] [Keep Unresolved] │
└──────────────────────────────────────────────────────┘
```

---

# 23. Evidence Panel

Evidence should be grouped by signal.

Example:

### Customer Identity

```text
Bank account matches customer record
Strong
```

### Amount

```text
Payment: ₹35,000
Candidate total: ₹35,000
Exact match
```

### Invoice Reference

```text
Not present
```

### Historical Pattern

```text
Customer previously paid multiple invoices together
Supporting evidence
```

This structure is more useful than showing a generic AI paragraph first.

---

# 24. Conflicting Evidence

Conflicting evidence must be visually explicit.

Example:

```text
⚠ Conflicting evidence

Customer name matches two customers.

ABC Technologies Pvt Ltd
ABC Technologies LLP
```

The UI should immediately communicate:

> This is why the system cannot safely decide.

Do not hide conflict inside a low confidence score.

---

# 25. Manual Match Flow

If automatic matching is insufficient:

```text
Payment
 ↓
Select Customer
 ↓
Select Invoice(s)
 ↓
Enter Allocation
 ↓
Validate
 ↓
Confirm
 ↓
Audit
```

The system must prevent invalid allocations.

Example:

```text
Payment = ₹35,000

Selected invoices = ₹42,000

→ Cannot approve
→ Allocation exceeds payment
```

---

# 26. Partial Payment UX

Example:

```text
Invoice
INV-500

Total
₹100,000

Payment
₹40,000

Outstanding after payment
₹60,000
```

The user must clearly see:

```text
Payment applied: ₹40,000
Remaining: ₹60,000
Status: Partially Paid
```

---

# 27. Multi-Invoice Payment UX

Example:

```text
Payment
₹50,000

Selected invoices:

INV-101   ₹20,000
INV-102   ₹30,000

Total allocated
₹50,000
```

The allocation should visibly reconcile to the payment.

---

# 28. Overpayment UX

Example:

```text
Payment
₹40,000

Invoice allocation
₹35,000

Unallocated
₹5,000
```

The system should not silently discard the difference.

The user must understand what remains unresolved.

---

# 29. Empty States

Every major list needs a useful empty state.

Examples:

### No Customers

> Add your first customer to start organizing invoice and payment information.

### No Invoices

> Upload your first invoice to begin reconciliation.

### No Payments

> Import a bank statement to bring in customer payments.

### No Reviews

> You're caught up. No payments currently require review.

Empty states should explain the next action.

---

# 30. Loading States

Long-running operations must show progress.

Examples:

```text
Uploading...
Processing...
Extracting invoice data...
Validating...
Importing transactions...
Reconciling payments...
```

Do not show an unexplained spinner for long operations.

---

# 31. Error States

Errors should answer:

1. What happened?
2. Is my data safe?
3. What can I do next?

Bad:

> Something went wrong.

Better:

> We couldn't process 8 rows from the statement because the amount column contains invalid values. The remaining 492 valid rows were imported successfully.

---

# 32. Destructive Actions

Destructive or financial actions require explicit confirmation where appropriate.

Examples:

- Delete/archive customer
- Cancel invoice
- Reject reconciliation
- Manual allocation
- Undo/adjust financial state

Confirmation should state the consequence.

---

# 33. Audit UI

The audit interface should be chronological.

Example:

```text
23 Jul 14:21
Payment imported
₹35,000

23 Jul 14:22
Reconciliation executed
Suggested INV-101 + INV-102

23 Jul 14:25
Approved by Vinayaka

23 Jul 14:25
Invoices updated
```

The user should be able to understand what changed and who performed it.

---

# 34. Settings

Initial settings should remain small.

### Company

- Company information

### Users

- Members
- Invitations

### Preferences

- Currency
- Timezone
- Reconciliation configuration where supported

Do not turn Settings into a generic configuration dump.

---

# 35. Notification Design

MVP notifications should focus on actionable events.

Potential notifications:

- Import completed
- Import failed
- OCR requires review
- Reconciliation completed
- Payments require review

Avoid excessive notification volume.

---

# 36. AI Explanation UX

AI explanations should appear as an interpretation of existing evidence.

Example:

> This payment was suggested for INV-101 and INV-102 because the payer matched the customer record and the combined outstanding amount exactly matches ₹35,000.

The interface should also expose the underlying evidence.

AI text should never replace evidence.

---

# 37. AI Failure UX

If explanation generation fails:

```text
Explanation temporarily unavailable.
```

The structured evidence and decision remain visible.

The financial workflow must continue.

The user should never see:

> "The AI failed, so your reconciliation result is unavailable."

AI explanation is non-authoritative.

---

# 38. Trust Design

Trust should be created through:

- Transparent evidence
- Clear status
- Visible uncertainty
- Audit history
- Human approval
- Predictable behavior
- No exaggerated AI claims

Avoid:

- "AI knows this is correct."
- "100% automatic."
- "Guaranteed match."

---

# 39. Accessibility

The UI should not rely solely on:

- Color
- Icons
- Position

Statuses should have text labels.

Example:

```text
Strong match
Needs review
Rejected
Unmatched
```

Keyboard navigation and readable contrast should be considered from the start.

---

# 40. Responsive Design

Primary usage is expected on desktop because accounting workflows involve tables and detailed records.

The design should still degrade gracefully on smaller screens.

Priority:

1. Desktop
2. Large tablet
3. Mobile viewing

Mobile should not become a separate application in MVP.

---

# 41. Design System Direction

The visual language should be:

- Professional
- Calm
- Financial
- Modern
- Minimal
- Trustworthy

Avoid:

- Futuristic neon
- Excessive gradients
- Overly playful financial UI
- Excessive animations
- "AI magic" visual effects

Typography should prioritize readability.

Tables should prioritize scanability.

---

# 42. Color Semantics

Color should communicate state consistently.

Suggested semantic categories:

### Positive

Matched / approved / completed

### Warning

Needs review / uncertain / partial

### Error

Failed / invalid / rejected

### Neutral

Imported / pending / informational

Exact brand palette is a later design-system decision.

Color must not be the only status indicator.

---

# 43. Tables

Tables are central to the product.

Requirements:

- Sticky headers where useful
- Pagination
- Sorting where meaningful
- Filters
- Search
- Clear numeric alignment
- Right-aligned monetary values
- Consistent date formats
- Status labels

Avoid excessive columns.

Secondary information should be accessible from details rather than forcing every field into the table.

---

# 44. Monetary Display

Monetary values should be:

- Clearly labeled
- Consistently formatted
- Easy to compare
- Right aligned in tables

Example:

```text
₹35,000.00
```

The exact display precision should follow the product's currency configuration.

---

# 45. Search

Search should exist where users manage many records.

Initial search targets:

- Customers
- Invoice numbers
- Payments/reference numbers

Search should not silently search unrelated entities.

---

# 46. Filtering

Useful filters:

### Invoices

- Status
- Customer
- Date
- Outstanding

### Payments

- Status
- Customer
- Date
- Amount

### Review Center

- Reason
- Confidence category
- Date
- Customer

---

# 47. UX for Uncertainty

Uncertainty must be explicit.

Example states:

```text
Strong Match
Likely Match
Needs Review
Conflicting Evidence
No Match
```

The user should understand that:

```text
Needs Review
```

is a valid and intentional system outcome, not a system failure.

---

# 48. UX for Evidence Provenance

Where practical, the user should be able to trace evidence to its source.

Example:

```text
Amount Match
₹35,000
Source: Bank transaction

Invoice Total
₹35,000
Source: INV-101 + INV-102
```

For uploaded documents:

```text
Source: Invoice PDF
```

For customer records:

```text
Source: Customer profile
```

This improves auditability and trust.

---

# 49. Reconciliation Explainability Hierarchy

Use this order:

```text
Decision
 ↓
Confidence / Status
 ↓
Evidence Summary
 ↓
Detailed Evidence
 ↓
Source
 ↓
Natural Language Explanation
```

Not:

```text
AI paragraph
 ↓
Maybe evidence
```

---

# 50. Review Efficiency

The Review Center should be optimized for repeated decisions.

Users should be able to:

- Open next review item
- Approve
- Reject
- Manually match
- Return to queue

The interface should minimize unnecessary navigation.

A future keyboard-shortcut system may be added after actual user testing proves value.

---

# 51. Bulk Actions

Bulk approval should NOT be introduced simply to save clicks.

Because this is financial software, bulk actions should only be allowed when:

- The selected items satisfy explicit safety criteria.
- The user can clearly understand the consequence.
- Audit records can capture the bulk action.

This should be evaluated after MVP usage data.

---

# 52. Design for Failure

The design must treat failure as part of the normal product.

Examples:

```text
OCR failed
→ Edit manually / retry

Statement import partially failed
→ Show successful and failed rows

Payer ambiguous
→ Review customer candidates

No invoice match
→ Keep unresolved

LLM explanation failed
→ Show structured evidence

Database/provider unavailable
→ Preserve state and show retryable status
```

---

# 53. Design for Trustworthy Automation

Automation should be visible but controllable.

Example:

```text
System processed 500 payments

Matched / suggested: 440
Needs review: 60
```

The user should understand what happened without opening every transaction.

---

# 54. Time-Saved UX

The dashboard may eventually display:

```text
Payments processed       1,180
Manual reviews              60
Estimated time saved      31.4 h
```

But estimates must be labeled.

Do not present estimates as measured facts.

Potential explanation:

> Based on your configured baseline processing time and the number of transactions handled automatically.

---

# 55. Design Metrics

UX success should be evaluated through:

- Time to complete reconciliation
- Review completion time
- Number of clicks/actions per review
- Review abandonment
- Manual correction rate
- False-match discovery rate
- User confidence/trust
- Search success
- Import completion rate
- OCR correction rate

---

# 56. MVP Screen Inventory

Final MVP screen inventory:

```text
01 Login
02 Register
03 Forgot Password
04 Dashboard
05 Customers
06 Customer Details
07 Add/Edit Customer
08 Invoices
09 Invoice Details
10 Upload Invoice
11 OCR Review
12 Payments
13 Upload Bank Statement
14 Import Result
15 Review Center
16 Reconciliation Detail
17 Audit
18 Settings
19 Profile
```

The exact screen count may change during implementation if screens are consolidated without reducing usability.

---

# 57. Screen Priority

## P0 — Core Workflow

- Dashboard
- Invoices
- Upload Invoice
- Payments
- Upload Statement
- Review Center
- Reconciliation Detail

## P1 — Supporting Workflow

- Customers
- Customer Details
- Invoice Details
- OCR Review
- Import Result

## P2 — Platform

- Authentication
- Settings
- Profile
- Audit

---

# 58. Design Dependencies

The UI depends on:

```text
PRD
 ↓
Domain Model
 ↓
Business Rules
 ↓
Database/Data Model
 ↓
API Contracts
 ↓
UI Design
```

UI must not invent business rules that do not exist in the domain/application layer.

---

# 59. Design Freeze Criteria

Design is ready to freeze when:

- Primary user journeys are defined.
- Every MVP screen has a clear purpose.
- Review workflow is fully defined.
- Reconciliation evidence presentation is defined.
- Uncertainty states are defined.
- Loading/error/empty states are defined.
- Financial actions have appropriate confirmation.
- Tenant/workspace context is visible where required.
- AI explanation is clearly subordinate to evidence.
- Accessibility basics are covered.
- No major screen contradicts the PRD or architecture.

---

# 60. Design Decisions

## Confirmed

- Simple accountant-focused UI
- Review Center as a primary workflow
- Evidence-first reconciliation presentation
- Human approval for ambiguous cases
- Operational dashboard rather than advanced analytics
- Approximately 12–19 MVP screens depending on implementation consolidation
- Desktop-first workflow
- No futuristic/neon visual language
- AI explanations are secondary to structured evidence

## Recommended

- Persistent application shell
- Review queue with next-item workflow
- Progressive disclosure of evidence
- Strong semantic status labels
- Explicit uncertainty states
- Source/provenance display
- Conservative visual treatment of confidence

## To Validate Through User Testing

- Exact dashboard layout
- Exact table columns
- Whether numeric confidence should be visible by default
- Bulk review actions
- Keyboard shortcuts
- Exact navigation order
- Exact terminology for match states
- Amount of evidence shown by default

---

# 61. Final Design Principle

The product should feel like a **trusted financial operations tool**, not an AI experiment.

The ideal experience is:

```text
Import
   ↓
System processes
   ↓
User sees what happened
   ↓
Evidence is visible
   ↓
Ambiguity is explicit
   ↓
User approves only what needs judgment
   ↓
System records everything
```

The product should make the accountant feel:

> **"The system has done the repetitive work, shown me why, and left the important decisions under my control."**
