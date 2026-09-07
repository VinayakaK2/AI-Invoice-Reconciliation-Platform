# Database Schema Specification

# AI Invoice Reconciliation Platform

**Version:** 1.0  
**Database:** PostgreSQL 16  
**Status:** Frozen  

---

## 1. Schema Conventions
- Primary Keys: `UUID` (`gen_random_uuid()`).
- Timestamps: `created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP`, `updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP`.
- Monetary Amounts: `NUMERIC(14, 2) NOT NULL`.
- Foreign Keys: Explicit `ON DELETE RESTRICT` for financial tables to preserve auditability.
- Multi-Tenancy: `company_id UUID NOT NULL` indexed on all business tables.

---

## 2. Table DDL Definitions

```sql
-- 1. COMPANIES (Tenant Root)
CREATE TABLE companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    base_currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 2. USERS
CREATE TYPE user_role AS ENUM ('OWNER', 'ACCOUNTANT');

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    email VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    role user_role NOT NULL DEFAULT 'ACCOUNTANT',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_users_company_email UNIQUE (company_id, email)
);
CREATE INDEX idx_users_company ON users(company_id);

-- 3. CUSTOMERS
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    name VARCHAR(255) NOT NULL,
    tax_id VARCHAR(50),
    email VARCHAR(255),
    phone VARCHAR(50),
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_customers_company_name UNIQUE (company_id, name)
);
CREATE INDEX idx_customers_company ON customers(company_id);

-- 4. CUSTOMER ALIASES
CREATE TABLE customer_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    alias_name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_customer_aliases_company_alias UNIQUE (company_id, alias_name)
);
CREATE INDEX idx_customer_aliases_lookup ON customer_aliases(company_id, alias_name);

-- 5. CUSTOMER PAYMENT IDENTIFIERS
CREATE TYPE identifier_type AS ENUM ('BANK_ACCOUNT', 'VIRTUAL_ACCOUNT', 'UPI_VPA');

CREATE TABLE customer_payment_identifiers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    identifier_type identifier_type NOT NULL,
    identifier_value VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_customer_identifiers UNIQUE (company_id, identifier_type, identifier_value)
);
CREATE INDEX idx_customer_identifiers_lookup ON customer_payment_identifiers(company_id, identifier_value);

-- 6. INVOICES
CREATE TYPE invoice_status AS ENUM ('PENDING', 'PARTIALLY_PAID', 'PAID', 'CANCELLED');

CREATE TABLE invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    invoice_number VARCHAR(100) NOT NULL,
    issue_date DATE NOT NULL,
    due_date DATE NOT NULL,
    total_amount NUMERIC(14, 2) NOT NULL CHECK (total_amount > 0),
    paid_amount NUMERIC(14, 2) NOT NULL DEFAULT 0.00 CHECK (paid_amount >= 0),
    outstanding_amount NUMERIC(14, 2) NOT NULL CHECK (outstanding_amount >= 0),
    currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    status invoice_status NOT NULL DEFAULT 'PENDING',
    document_url VARCHAR(1024),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_invoices_company_number UNIQUE (company_id, invoice_number),
    CONSTRAINT chk_invoice_balance CHECK (paid_amount + outstanding_amount = total_amount)
);
CREATE INDEX idx_invoices_company_status ON invoices(company_id, status);
CREATE INDEX idx_invoices_customer ON invoices(company_id, customer_id);

-- 7. PAYMENTS
CREATE TYPE payment_status AS ENUM ('UNMATCHED', 'SUGGESTED', 'PARTIALLY_ALLOCATED', 'ALLOCATED', 'IGNORED');

CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    statement_id UUID,
    transaction_date DATE NOT NULL,
    amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0),
    allocated_amount NUMERIC(14, 2) NOT NULL DEFAULT 0.00 CHECK (allocated_amount >= 0),
    unallocated_amount NUMERIC(14, 2) NOT NULL CHECK (unallocated_amount >= 0),
    currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    narration TEXT NOT NULL,
    reference_number VARCHAR(255),
    bank_account_number VARCHAR(100),
    status payment_status NOT NULL DEFAULT 'UNMATCHED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_payments_dedup UNIQUE (company_id, reference_number, transaction_date, amount),
    CONSTRAINT chk_payment_balance CHECK (allocated_amount + unallocated_amount = amount)
);
CREATE INDEX idx_payments_company_status ON payments(company_id, status);

-- 8. PAYMENT ALLOCATIONS (Authoritative Join)
CREATE TABLE payment_allocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    payment_id UUID NOT NULL REFERENCES payments(id) ON DELETE RESTRICT,
    invoice_id UUID NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT,
    allocated_amount NUMERIC(14, 2) NOT NULL CHECK (allocated_amount > 0),
    allocated_by_user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_payment_invoice_allocation UNIQUE (payment_id, invoice_id)
);
CREATE INDEX idx_allocations_company ON payment_allocations(company_id);
CREATE INDEX idx_allocations_invoice ON payment_allocations(invoice_id);

-- 9. RECONCILIATION DECISIONS
CREATE TYPE decision_status AS ENUM ('MATCH_SUGGESTED', 'AUTO_ELIGIBLE', 'REVIEW_REQUIRED', 'AMBIGUOUS', 'NO_MATCH');

CREATE TABLE reconciliation_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    payment_id UUID NOT NULL REFERENCES payments(id) ON DELETE CASCADE,
    status decision_status NOT NULL,
    confidence_score NUMERIC(5, 2) NOT NULL CHECK (confidence_score >= 0 AND confidence_score <= 100),
    algorithm_version VARCHAR(50) NOT NULL,
    evidence_data JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_recon_decisions_payment ON reconciliation_decisions(payment_id);

-- 10. REVIEW ITEMS
CREATE TYPE review_status AS ENUM ('PENDING', 'APPROVED', 'REJECTED', 'MANUALLY_RESOLVED');

CREATE TABLE review_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    payment_id UUID NOT NULL REFERENCES payments(id) ON DELETE CASCADE,
    decision_id UUID NOT NULL REFERENCES reconciliation_decisions(id) ON DELETE CASCADE,
    status review_status NOT NULL DEFAULT 'PENDING',
    assigned_to UUID REFERENCES users(id) ON DELETE SET NULL,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_review_items_company_status ON review_items(company_id, status);

-- 11. AUDIT LOGS (Append-Only)
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(100) NOT NULL,
    entity_type VARCHAR(100) NOT NULL,
    entity_id UUID NOT NULL,
    before_state JSONB,
    after_state JSONB,
    ip_address VARCHAR(45),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_audit_logs_company ON audit_logs(company_id, created_at DESC);
CREATE INDEX idx_audit_logs_entity ON audit_logs(entity_type, entity_id);
```
