"""Dashboard and operational summary module presentation router."""

from fastapi import APIRouter

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/status")
def dashboard_module_status() -> dict:
    """Return dashboard module readiness status."""
    return {"module": "dashboard", "status": "initialized"}
