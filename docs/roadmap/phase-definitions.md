# Authoritative Phase Definitions & Taxonomy

# AI Invoice Reconciliation Platform

**Version:** 1.0  
**Status:** Authoritative Source of Truth for Engineering Phases  

---

## 1. Single Authoritative Phase System

To avoid ambiguity between high-level product discussions and software engineering milestones, the project adopts this **single, canonical 20-phase engineering taxonomy**.

### Phase Classification:
1. **Design & Architecture Phases (Phases 0–7)**: Define requirements, domain invariants, system architecture, database schema, API contracts, security, and reliability before writing code.
2. **Implementation Phases (Phases 8–19)**: Build the modular monolith layer by layer, with tests, verification, and freeze gates.
3. **Post-MVP Integration Phases (Phases F1–F7)**: External communications, live accounting systems, and bank integrations.

---

## 2. Canonical Engineering Phases

| Phase | Category | Title | Scope Summary |
|---|---|---|---|
| **Phase 0** | Design | Product Definition | PRD, core problem, personas, MVP boundaries, non-goals |
| **Phase 1** | Design | Domain & Business Model | Entities, Value Objects, financial invariants, state machines |
| **Phase 2** | Design | System Architecture | Modular Monolith, Clean Architecture, module decoupling |
| **Phase 3** | Design | Database Architecture | PostgreSQL 16 schema, NUMERIC precision, indexes, ERD |
| **Phase 4** | Design | API Contract Design | REST /api/v1 endpoints, request/response envelopes |
| **Phase 5** | Design | Security Architecture | Multi-tenant isolation, bcrypt/JWT, AI trust boundaries |
| **Phase 6** | Design | Error & Reliability Architecture | Classified error taxonomy, idempotency, failure states |
| **Phase 7** | Design | Storage & Integration Architecture | Ports & Adapters for S3, OCR, and LLM |
| **Phase 8** | Code | Backend Engineering Foundation ⭐ | FastAPI bootstrap, config, logging, database connectivity, exceptions, Money value object, Pytest suite |
| **Phase 9** | Code | Authentication & Company Workspace | User & Company models, registration, login, tenant middleware |
| **Phase 10** | Code | Customer Management | Customer CRUD, aliases, payment identifiers, search |
| **Phase 11** | Code | Invoice Management | Invoice CRUD, balance calculation, status transitions, PDF link |
| **Phase 12** | Code | OCR & Document Processing | PDF text extraction port/adapter, normalized data review |
| **Phase 13** | Code | Payment & Statement Management | Bank statement CSV import, transaction parsing, deduplication |
| **Phase 14** | Code | Reconciliation Engine ⭐⭐⭐⭐⭐ | Deterministic matching, bounded subset-sum, evidence scoring, decision policy |
| **Phase 15** | Code | Review Center | Human exception queue, one-click approve, manual allocation, transactional commit |
| **Phase 16** | Code | Dashboard | Operational summary counts, aging, review queue metrics |
| **Phase 17** | Code | Audit & Compliance | Append-only audit logging for financial mutations |
| **Phase 18** | Code | Settings & Preferences | Company settings, user management, profile |
| **Phase 19** | Code | Production Hardening | End-to-end stress testing, concurrency verification, security audit |

---

## 3. Disambiguation Notes

1. **Reconciliation is Phase 14, NOT Phase 8**:
   - Phase 8 is strictly the *Backend Architecture & Project Foundation*. It contains zero reconciliation algorithms or business entities.
   - Phase 14 is the *Reconciliation Engine*. It implements payer identification, candidate generation, combination matching, and evidence scoring.
2. **`match_score` vs Probability**:
   - The reconciliation score (0–100) is a deterministic heuristic `match_score` based on structured evidence weights. It is NOT a statistical probability.
3. **`AUTO_ELIGIBLE` vs `AUTO_APPLIED`**:
   - `AUTO_ELIGIBLE` indicates that a match satisfies top-tier evidence criteria with zero conflicts, highlighting it for expedited human review.
   - `AUTO_APPLIED` (silent autonomous database mutation) is strictly forbidden in MVP.
4. **Subset-Sum Bounds**:
   - Search parameters ($k \le 4$, candidate limit $n \le 30$, 100ms timeout) are initial engineering policies subject to empirical benchmark validation.
