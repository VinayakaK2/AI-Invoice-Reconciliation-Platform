# AI Invoice Reconciliation Platform

A production-grade, multi-tenant financial reconciliation platform designed to ingest bank statements, maintain reliable invoice records, and deterministically match incoming payments against outstanding receivables with explainable confidence scoring and human review.

---

## Core Product Principles

- **Reconciliation Layer, Not ERP**: Sits between bank accounts and invoicing/ERP systems to automate payment matching.
- **Deterministic Decisions, AI Assistance**: Financial allocation and status transitions are 100% deterministic (`Money` exact Decimal arithmetic). AI is strictly an assistant for unstructured narrative parsing, never an authoritative financial decision maker.
- **Strict Multi-Tenancy**: Complete tenant isolation derived from server-side JWT authentication across APIs, use cases, repositories, file storage, and audit logs.
- **Clean Architecture & DDD Lite**: Modular monolith structure strictly separating Domain, Application, Infrastructure, and Presentation layers.

---

## Technology Stack

- **Backend Framework**: Python 3.11+ / FastAPI (REST API with OpenAPI documentation)
- **Database & ORM**: PostgreSQL / SQLAlchemy 2.0 with Alembic linear migrations
- **Validation & Schemas**: Pydantic v2
- **Testing**: Pytest (157 tests, 93% platform coverage)
- **Security**: JWT authentication (Argon2id password hashing), CSRF/CWE-1236 formula injection defenses, path traversal guards, bank account masking

---

## Current Roadmap & Phase Status

| Phase | Module / Capability | Status |
|---|---|---|
| **Phase 8** | Backend Engineering Foundation (Clean Architecture, Shared Types, Database) | **FROZEN** |
| **Phase 9** | Authentication & Company Workspace (JWT, Multi-Tenancy, User Membership) | **FROZEN** |
| **Phase 10** | Customer Management (Identity Layer, Customer Repositories & APIs) | **FROZEN** |
| **Phase 11** | Invoice Management (Invoices, Items, Uploads, Invariants, Archive) | **FROZEN** |
| **Phase 12** | Payment & Bank Statement Ingestion (CSV Parser, Deduplication, Candidates) | **FROZEN** |
| **Phase 13** | Reconciliation Rule Engine (Matching Algorithms & Evidence Scoring) | *Planned* |
| **Phase 14** | Human Review & Exception Workflow (Dispute & Manual Override) | *Planned* |

---

## Repository Structure

```text
├── .agents/rules/        # Engineering agent rules and quality gates
├── Architecture.md       # Authoritative system architecture specification
├── design.md             # Detailed domain and software design document
├── phases.md             # Multi-phase engineering roadmap
├── project-info.md       # Project brief, context, and core requirements
├── security.md           # Security and compliance specification
├── Project-Brain/        # Active system memory, decision ledger, and state
├── docs/                 # API contracts, database schemas, and phase history
└── backend/              # Modular Monolith FastAPI Backend
    ├── alembic/          # Append-only database migration versions
    ├── app/
    │   ├── api/v1/       # API router and health endpoints
    │   ├── core/         # Database, security, config, and logging
    │   ├── modules/      # Domain modules (auth, company, customer, invoice, payment)
    │   └── shared/       # Shared value objects (Money), errors, db types
    └── tests/            # Unit, integration, and security test suites
```

---

## Local Development & Testing

### 1. Environment Setup
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Or on Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

### 2. Database Migrations
```bash
alembic upgrade head
```

### 3. Run Test Suite
```bash
pytest
```
*Current test suite: 157 passing tests, 93% code coverage.*

---

## License

Proprietary — AI Invoice Reconciliation Platform. All rights reserved.
