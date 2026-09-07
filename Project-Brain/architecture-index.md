# Architecture Index — AI Invoice Reconciliation Platform

This index maps system architecture concepts, documents, and boundaries.

---

## 1. Primary Architecture Documents

| Document | Location | Purpose |
|---|---|---|
| **System Architecture** | `Architecture.md` | High-level system structure, components, data flows, scalability |
| **Product Design** | `design.md` | UX specifications, screen inventory, Review Center, uncertainty presentation |
| **Security Architecture** | `security.md` | Threat model, tenant isolation, authentication, AI safety |
| **Implementation Phases** | `phases.md` | 20-phase roadmap, freeze criteria, and dependencies |
| **Master Brief** | `project-info.md` | Core business problem, product boundaries, rules of engagement |
| **Domain Model** | `docs/domain/domain-model.md` | Formal entities, aggregates, value objects, invariants |
| **Business Rules** | `docs/domain/business-rules.md` | State transitions, reconciliation scoring weights, match criteria |
| **Database Schema & ERD** | `docs/database/schema.md`, `docs/database/ERD.md` | Relational schema, indexes, constraints, precision |
| **API Contracts** | `docs/api/contracts.md` | REST endpoints, schemas, authentication, errors |

---

## 2. Module Boundaries & Communication Rules

```text
Presentation Layer (FastAPI Routers)
    ↓ invokes
Application Layer (Use Cases / Commands / Queries)
    ↓ coordinates
Domain Layer (Entities, Invariants, Business Rules)
    ↑ implements
Infrastructure Layer (SQLAlchemy Models, PostgreSQL Repositories, External Ports)
```

- **Module Isolation**: Modules only communicate via Application Service contracts.
- **Persistence Isolation**: No module imports or executes queries directly against another module's SQLAlchemy ORM models.
- **External Services Isolation**: External services (OCR, LLM, Storage) are always behind abstract Ports (interfaces) in the domain/application layer and implemented via Adapters in the infrastructure layer.
