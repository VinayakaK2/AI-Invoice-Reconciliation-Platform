# Phase 9 History — Authentication & Company Workspace

**Phase:** Phase 9  
**Status:** COMPLETE & FROZEN  
**Date:** 2026-09-05  

---

## 1. Objective
Establish production-grade authentication, multi-tenant workspace isolation, user roles (`OWNER`, `ACCOUNTANT`), company workspace registration, credential validation, JWT access and refresh token lifecycle, password reset, and user invitation workflows for the AI Invoice Reconciliation Platform.

---

## 2. Scope & Implementation Delivered

### 2.1 Domain Layer (`app/modules/auth/domain/`, `app/modules/company/domain/`)
1. **Roles & Invariants**:
   - `UserRole`: Authoritative role enum with `OWNER` and `ACCOUNTANT`.
   - `User`: Core domain entity with UUID `id`, `company_id`, `email`, `hashed_password`, `full_name`, `role`, `is_active`, `is_verified`, timestamps, and role check methods (`is_owner()`, `belongs_to()`).
   - `Company`: Tenant root entity isolating all financial datasets (`id`, `name`, `base_currency`, `is_active`, timestamps).
   - `UserInvitation`: Entity representing an Owner invitation with 7-day expiration check (`is_expired()`).
   - `PasswordResetToken`: Entity with 1-hour expiration and single-use validity check (`is_valid()`).
   - Defensive datetime handling: normalizes offset-naive and offset-aware datetimes to UTC to guarantee cross-database dialect stability.

### 2.2 Persistence & Database Architecture (`app/modules/auth/infrastructure/`, `app/modules/company/infrastructure/`)
1. **SQLAlchemy 2.0 ORM Models**:
   - `CompanyModel`: Persistent model for `companies` table.
   - `UserModel`: Persistent model with composite unique constraint `(company_id, email)` and FK to `companies(id)`.
   - `UserInvitationModel`: Persistent model with unique `invitation_token` index and cascade FK to `companies(id)`.
   - `PasswordResetTokenModel`: Persistent model with indexed `token_hash` and cascade FK to `users(id)`.
   - `GUID` TypeDecorator in `app/shared/infrastructure/db_types.py`: Seamlessly supports native PostgreSQL `UUID` in production and `CHAR(36)` in test environments.
2. **Tenant-Scoped Repositories**:
   - `CompanyRepository`: `create`, `get_by_id`, `update`.
   - `UserRepository`: `create`, `get_by_id` (with tenant scoping), `get_by_email_and_company`, `get_by_email`, `list_by_company`, `update_password`.
   - `InvitationRepository`: `create`, `get_by_token`, `mark_accepted`.
   - `PasswordResetRepository`: `create`, `get_by_token_hash`, `mark_used`.
3. **Database Migration**:
   - Created initial Alembic migration revision `backend/alembic/versions/0001_phase_09_auth_and_company.py` with complete DDL, foreign keys, indexes, and unique constraints.

### 2.3 Application Use Cases (`app/modules/auth/application/`)
1. `RegisterCompanyAndOwnerUseCase`: Atomically registers company workspace and creates initial founding `OWNER` user; issues initial access and refresh tokens.
2. `LoginUseCase`: Validates credentials, verifies company and user active statuses, issues JWT access and refresh tokens.
3. `RefreshTokenUseCase`: Validates refresh token and issues fresh access token.
4. `InviteUserUseCase`: Enforces that caller is `OWNER`, validates user not already in company, creates secure invitation token with 7-day expiry.
5. `AcceptInvitationUseCase`: Validates token, creates `ACCOUNTANT` user attached to company, marks invitation accepted, returns session tokens.
6. `RequestPasswordResetUseCase`: Generates SHA-256 hashed token with 1-hour expiry, returns token for secure email transmission.
7. `ConfirmPasswordResetUseCase`: Validates token, hashes new password with bcrypt, updates password, and invalidates token.
8. `ListCompanyUsersUseCase`: Lists all team members strictly within the caller's tenant.

### 2.4 Presentation Layer & API Contracts (`app/modules/auth/presentation/`, `app/modules/company/presentation/`)
1. **REST API Endpoints**:
   - `POST /api/v1/auth/register` (201 Created)
   - `POST /api/v1/auth/login` (200 OK)
   - `POST /api/v1/auth/refresh` (200 OK)
   - `POST /api/v1/auth/logout` (200 OK)
   - `GET /api/v1/auth/me` (200 OK)
   - `POST /api/v1/auth/password-reset/request` (200 OK)
   - `POST /api/v1/auth/password-reset/confirm` (200 OK)
   - `POST /api/v1/auth/invitations/accept` (201 Created)
   - `GET /api/v1/company/current` (200 OK)
   - `POST /api/v1/company/users/invite` (201 Created - Owner only)
   - `GET /api/v1/company/users` (200 OK - Tenant scoped)
   - `GET /api/v1/auth/status` & `GET /api/v1/company/status` (Health/diagnostics preserved)
2. **FastAPI Dependencies**:
   - `get_current_user`: Validates JWT Bearer token, fetches user, enforces active account check.
   - `get_current_company`: Validates tenant workspace existence and active status.
   - `require_role(role)`: Role-based access control guard rejecting unauthorized actions with `403 Forbidden`.

---

## 3. Verification Evidence

### Automated Test Suite Execution
Executed against clean in-memory database with Pytest and coverage profiling:

```text
============================= test session starts =============================
platform win32 -- Python 3.11.8, pytest-9.1.1, pluggy-1.6.0
rootdir: H:\AI Invoice Reconciliation Platform\backend
configfile: pyproject.toml
plugins: anyio-4.15.1, asyncio-1.4.0, cov-7.1.0
collected 57 items

tests/integration/test_auth_integration.py .............                 [ 22%]
tests/integration/test_health.py ......                                  [ 33%]
tests/integration/test_tenant_security.py ......                         [ 43%]
tests/unit/test_auth_unit.py ......                                      [ 54%]
tests/unit/test_config.py .....                                          [ 63%]
tests/unit/test_exceptions.py ...                                        [ 68%]
tests/unit/test_logging.py ...                                           [ 73%]
tests/unit/test_money.py ............                                    [ 94%]
tests/unit/test_security.py ...                                          [100%]

=============================== tests coverage ================================
TOTAL: 1011 stmts, 92% coverage
======================== 57 passed, 1 warning in 9.99s ========================
```

### Coverage Highlights:
- `app/modules/auth/presentation/router.py`: **100%**
- `app/modules/auth/infrastructure/models.py`: **100%**
- `app/modules/auth/infrastructure/repositories.py`: **100%**
- `app/modules/auth/domain/entities.py`: **100%**
- `app/modules/company/domain/entities.py`: **100%**
- `app/modules/company/infrastructure/models.py`: **100%**
- `app/modules/company/infrastructure/repositories.py`: **97%**
- `app/modules/company/presentation/router.py`: **97%**
- `app/modules/auth/application/use_cases.py`: **90%**

---

## 4. Security & Multi-Tenant Audit Review

- **Multi-Tenant Isolation**:
  - Validated by `test_cross_tenant_isolation`: Users from Company A cannot view or access users, workspaces, or records belonging to Company B.
  - All database queries for company resources strictly enforce `company_id` matching.
- **Role-Based Access Control (RBAC)**:
  - Validated by `test_accountant_forbidden_from_inviting_users`: Non-owner roles (`ACCOUNTANT`) attempting to invite users or access privileged administrative actions receive `403 Forbidden`.
- **Token Security & Tamper Resistance**:
  - Validated by `test_tampered_jwt_token_rejected`: Tampered tokens or invalid signatures are rejected cryptographically with `401 Unauthorized`.
  - Password reset tokens are stored as SHA-256 hashes; raw tokens are never persisted in plaintext.
- **Account State Verification**:
  - Validated by `test_deactivated_user_cannot_access_api`: Deactivated user accounts are immediately denied API access on all protected routes.
- **Password Strength & Verification**:
  - Minimum 8-character passwords enforced via Pydantic v2 validation.
  - Passwords hashed using bcrypt with salt rounds.

---

## 5. Known Limitations & Next Steps
- Real SMTP email transmission is abstracted for local/test execution; in production, an email delivery adapter (e.g. Resend, SendGrid, or AWS SES) will plug into the email dispatch interface.
- Ready for **Phase 10: Customer Management** (customer profiles, tax IDs/GSTIN, aliases, and known bank account/UPI identifiers).
