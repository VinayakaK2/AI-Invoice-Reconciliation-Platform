"""Audit and compliance module presentation router."""

from fastapi import APIRouter

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get("/status")
def audit_module_status() -> dict:
    """Return audit module readiness status."""
    return {"module": "audit", "status": "initialized"}
