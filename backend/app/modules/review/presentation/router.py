"""Review Center module presentation router."""

from fastapi import APIRouter

router = APIRouter(prefix="/review", tags=["Review Center"])


@router.get("/status")
def review_module_status() -> dict:
    """Return review module readiness status."""
    return {"module": "review", "status": "initialized"}
