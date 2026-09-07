"""FastAPI application entrypoint and middleware orchestration.

Configures CORS, correlation IDs, centralized exception handling, and API routing.
"""

import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.v1.router import api_v1_router
from app.config import settings
from app.core.logging import logger
from app.shared.exceptions import AppError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and graceful shutdown lifecycle manager."""
    logger.info(f"Starting {settings.PROJECT_NAME} v{settings.VERSION} [{settings.APP_ENV}]")
    yield
    logger.info(f"Shutting down {settings.PROJECT_NAME}")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Intelligent financial reconciliation layer for invoices and bank statements.",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG or settings.APP_ENV != "production" else None,
    redoc_url="/redoc" if settings.DEBUG or settings.APP_ENV != "production" else None,
)

# Cross-Origin Resource Sharing (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlation_id_and_timing_middleware(request: Request, call_next):
    """Inject a unique Request ID into the context and compute latency."""
    request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
    request.state.request_id = request_id

    start_time = time.time()
    response = await call_next(request)
    process_time_ms = (time.time() - start_time) * 1000

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = f"{process_time_ms:.2f}"
    return response


# Global Exception Handlers
@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handle domain, validation, authorization, and business rule errors."""
    request_id = getattr(request.state, "request_id", "unknown")
    logger.warning(
        f"Handled error [{exc.code}] on {request.method} {request.url.path}: {exc.message}",
        extra={"request_id": request_id},
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "requestId": request_id,
            },
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler preventing stack trace leakage to API clients."""
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(
        f"Unhandled server error on {request.method} {request.url.path}: {exc}",
        exc_info=True,
        extra={"request_id": request_id},
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred. Please try again later.",
                "details": {},
                "requestId": request_id,
            },
        },
    )


# Mount versioned API routes
app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)

# Also mount health router directly at root /health for cloud health checks
from app.api.v1.health import router as health_router
app.include_router(health_router)
