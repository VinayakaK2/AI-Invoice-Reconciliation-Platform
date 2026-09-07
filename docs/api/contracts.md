# API Contracts Specification

# AI Invoice Reconciliation Platform

**Version:** 1.0  
**Prefix:** `/api/v1`  
**Security:** Bearer JWT Token via HTTP Authorization Header (`Authorization: Bearer <token>`)  
**Status:** Frozen  

---

## 1. Global Response Envelopes & Error Schema

### 1.1 Success Response
```json
{
  "success": true,
  "data": { ... },
  "meta": {
    "requestId": "req_01j7...",
    "timestamp": "2026-09-05T14:15:00Z"
  }
}
```

### 1.2 Error Response
```json
{
  "success": false,
  "error": {
    "code": "INSUFFICIENT_UNALLOCATED_AMOUNT",
    "message": "Payment only has ₹15,000.00 unallocated, cannot allocate ₹20,000.00",
    "details": {},
    "requestId": "req_01j7..."
  }
}
```

---

## 2. Core Endpoints Summary

### 2.1 Health & Diagnostics
- `GET /health` — Liveness probe (HTTP 200 OK).
- `GET /health/ready` — Readiness probe verifying PostgreSQL connection.

### 2.2 Auth & Workspace
- `POST /api/v1/auth/register` — Register company and owner user.
- `POST /api/v1/auth/login` — Authenticate and issue JWT access token.
- `GET /api/v1/auth/me` — Return current authenticated user profile and company.

### 2.3 Customers
- `GET /api/v1/customers` — List customers with pagination and search.
- `POST /api/v1/customers` — Create new customer.
- `GET /api/v1/customers/{id}` — Get customer details, aliases, and payment identifiers.
- `POST /api/v1/customers/{id}/aliases` — Add customer alias name.
- `POST /api/v1/customers/{id}/identifiers` — Add customer payment identifier (bank account, UPI).

### 2.4 Invoices
- `GET /api/v1/invoices` — List invoices (filter by status, customer, date).
- `POST /api/v1/invoices` — Create invoice manually.
- `POST /api/v1/invoices/upload` — Upload invoice PDF for asynchronous OCR extraction.
- `GET /api/v1/invoices/{id}` — Get invoice details and allocation history.

### 2.5 Payments & Statements
- `GET /api/v1/payments` — List payments (filter by status, date, amount).
- `POST /api/v1/payments/upload-statement` — Upload bank statement CSV.
- `GET /api/v1/payments/{id}` — Get payment details and candidate matches.

### 2.6 Reconciliation
- `POST /api/v1/reconciliation/run` — Trigger reconciliation batch for unmatched payments.
- `GET /api/v1/reconciliation/decisions/{payment_id}` — Get reconciliation decision, candidate list, and structured evidence.

### 2.7 Review Center
- `GET /api/v1/review/queue` — Get queue of items requiring human review.
- `POST /api/v1/review/{review_id}/approve` — Approve suggested match (atomic transaction).
- `POST /api/v1/review/{review_id}/reject` — Reject suggestion.
- `POST /api/v1/review/{review_id}/manual-allocate` — Apply custom user-defined allocation.

### 2.8 Dashboard
- `GET /api/v1/dashboard/summary` — Operational counts: pending invoices, unmatched payments, outstanding amounts, review queue count.

### 2.9 Audit
- `GET /api/v1/audit/logs` — Query immutable audit trail (filter by entity, actor, date).
