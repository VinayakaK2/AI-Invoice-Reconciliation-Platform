# System State — AI Invoice Reconciliation Platform

**Last Updated:** 2026-09-08  
**Current Status:** Phases 8, 9, 10, 11, 12, 13, 14.1, 14.2, 14.3, 14.4 Frozen  
**Active Phase:** Phase 14.5 — Exact Matching (1:1)  

---

## 1. Project Overview

- **Product:** AI Invoice Reconciliation Platform
- **Style:** Modular Monolith + Clean Architecture + DDD Lite
- **Authoritative Datastore:** PostgreSQL 16 (running locally as Windows service)
- **Runtime Environment:** Python 3.11 (`py -3.11`), Node.js v24.15.0, Git 2.53.0
- **Core Principle:** Deterministic systems decide. Evidence explains. AI translates language. Humans resolve uncertainty.

---

## 2. Phase Status Tracking

| Phase | Title | Type | Status | Evidence / Artifact |
|---|---|---|---|---|
| **Phase 0** | Product Definition | Product | **FROZEN** | `project-info.md`, `PRD.md`, `implementation_plan.md` |
| **Phase 1** | Domain & Business Model | Design | **FROZEN** | `docs/domain/domain-model.md`, `docs/domain/business-rules.md` |
| **Phase 2** | System Architecture | Design | **FROZEN** | `Architecture.md` |
| **Phase 3** | Database Architecture | Design | **FROZEN** | `docs/database/schema.md`, `docs/database/ERD.md` |
| **Phase 4** | API Contract Design | Design | **FROZEN** | `docs/api/contracts.md` |
| **Phase 5** | Security Architecture | Design | **FROZEN** | `security.md` |
| **Phase 6** | Error & Reliability Architecture | Design | **FROZEN** | `docs/architecture/error-architecture.md` |
| **Phase 7** | Storage & Integration Architecture | Design | **FROZEN** | `docs/architecture/storage-integration.md` |
| **Phase 8** | Engineering Foundation | Code | **FROZEN** | `docs/phase-history/phase-08-foundation.md` |
| **Phase 9** | Authentication & Company Workspace | Code | **FROZEN** | `docs/phase-history/phase-09-authentication-and-company-workspace.md` |
| **Phase 10** | Customer Management | Code | **FROZEN** | `docs/phase-history/phase-10-customer-management.md` |
| **Phase 11** | Invoice Management | Code | **FROZEN** | `docs/phase-history/phase-11-invoice-management.md` |
| **Phase 12** | OCR & Document Processing | Code | **FROZEN** | `docs/phase-history/phase-12-ocr-document-processing.md` |
| **Phase 13** | Payment & Bank Statement Ingestion | Code | **FROZEN** | `docs/phase-history/phase-13-payment-ingestion.md` |
| **Phase 14.1** | Reconciliation Engine: Payment Intake | Code | **FROZEN** | `docs/phase-history/phase-14-1-payment-intake.md` |
| **Phase 14.2** | Reconciliation Engine: Customer Identification Foundation | Code | **FROZEN** | `docs/phase-history/phase-14-2-customer-identification.md` |
| **Phase 14.3** | Reconciliation Engine: Candidate Invoice Generation | Code | **FROZEN** | `docs/phase-history/phase-14-3-candidate-invoice-generation.md` |
| **Phase 14.4** | Reconciliation Engine: Candidate Filtering | Code | **FROZEN** | `docs/phase-history/phase-14-4-candidate-filtering.md` |
| **Phase 14.5** | Reconciliation Engine: Exact Matching (1:1) | Code | PENDING | — |
| **Phase 14.6** | Reconciliation Engine: Partial Matching | Code | PENDING | — |
| **Phase 14.7** | Reconciliation Engine: Multi-Invoice Matching | Code | PENDING | — |
| **Phase 15** | Review Center | Code | PENDING | — |
| **Phase 16** | Dashboard | Code | PENDING | — |
| **Phase 17** | Audit & Compliance Foundation | Code | PENDING | — |
| **Phase 18** | Settings & Operational Controls | Code | PENDING | — |
| **Phase 19** | Production Hardening | Code | PENDING | — |

---

## 3. Active Invariants

1. **Deterministic Decisions**: No LLM determines payment allocation, invoice matching, or financial state.
2. **Tenant Isolation**: All database queries and commands are tenant-scoped via `company_id`.
3. **Monetary Safety**: Exact `Decimal` / `NUMERIC(14, 2)` representations are enforced. No binary floats.
4. **Balance Conservation**: For all invoices, `paid_amount + outstanding_amount == total_amount` at all times.
5. **Transactional Settlement**: Multi-entity updates (Payment + Invoice + Allocation + Audit) are committed in single ACID transactions with row-level locks.
6. **Idempotent Ingestion**: Invoices and payments enforce composite uniqueness to prevent duplicate records.
