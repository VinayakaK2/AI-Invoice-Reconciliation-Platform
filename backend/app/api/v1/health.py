"""Health check and diagnostic endpoints for container orchestration and uptime monitoring."""

from fastapi import APIRouter, Response, status
from app.config import settings
from app.core.database import check_database_connection

router = APIRouter(tags=["Health"])


@router.get("/health", status_code=status.HTTP_200_OK)
def liveness_check() -> dict:
    """Basic liveness probe confirming the HTTP server is responsive."""
    return {
        "status": "ok",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.APP_ENV,
    }


@router.get("/health/live", status_code=status.HTTP_200_OK)
def live_probe() -> dict:
    """Kubernetes / container runtime liveness probe."""
    return {"status": "alive"}


@router.get("/health/ready")
def readiness_check(response: Response) -> dict:
    """Readiness probe checking database connectivity."""
    db_healthy = check_database_connection()
    if not db_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "message": "Database readiness probe failed.",
        }

    return {
        "status": "ready",
        "database": "connected",
    }
