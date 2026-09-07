"""API version 1 consolidated router.

Aggregates diagnostic endpoints and decoupled business module presentation routers.
"""

from fastapi import APIRouter
from app.api.v1.health import router as health_router
from app.modules.auth.presentation.router import router as auth_router
from app.modules.company.presentation.router import router as company_router
from app.modules.customer.presentation.router import router as customer_router
from app.modules.invoice.presentation.router import router as invoice_router
from app.modules.payment.presentation.router import router as payment_router
from app.modules.reconciliation.presentation.router import router as reconciliation_router
from app.modules.review.presentation.router import router as review_router
from app.modules.dashboard.presentation.router import router as dashboard_router
from app.modules.audit.presentation.router import router as audit_router

api_v1_router = APIRouter()

# Register core health & diagnostic routes
api_v1_router.include_router(health_router)

# Register business module routers
api_v1_router.include_router(auth_router)
api_v1_router.include_router(company_router)
api_v1_router.include_router(customer_router)
api_v1_router.include_router(invoice_router)
api_v1_router.include_router(payment_router)
api_v1_router.include_router(reconciliation_router)
api_v1_router.include_router(review_router)
api_v1_router.include_router(dashboard_router)
api_v1_router.include_router(audit_router)
