"""Reconciliation engine module presentation router."""

from fastapi import APIRouter

router = APIRouter(prefix="/reconciliation", tags=["Reconciliation"])


@router.get("/status")
def reconciliation_module_status() -> dict:
    """Return reconciliation module readiness status."""
    return {"module": "reconciliation", "status": "initialized"}
