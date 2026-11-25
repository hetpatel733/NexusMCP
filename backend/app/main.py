import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.v1 import audit, auth, connections, executions, health, observability, tools, triggers, workflows, workspaces
from .config import get_settings
from .db import close_db, get_db, init_db
from .logger import logger
from .observability.correlation import CorrelationIdMiddleware
from .security.middleware import (
    CSRFProtectionMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from .services.retention_service import DataRetentionService
from .triggers.service import TriggerIngestionService

# Background retention sweep task handle
_retention_task: asyncio.Task | None = None

RETENTION_SWEEP_INTERVAL_SECONDS = 24 * 60 * 60  # 24 hours


async def _run_periodic_retention_sweep(db):
    """Run a full retention sweep periodically while the app is running."""
    while True:
        try:
            await asyncio.sleep(RETENTION_SWEEP_INTERVAL_SECONDS)
            retention_service = DataRetentionService(db)
            results = await retention_service.run_full_retention_sweep()
            total = sum(results.values())
            if total > 0:
                logger.info("Periodic retention sweep complete", total_deleted=total, details=results)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Periodic retention sweep failed", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up NexusMCP backend")
    try:
        await init_db()
        db = await get_db()
        # Crash recovery for durable event ingestion
        ingestion_service = TriggerIngestionService(db)
        recovered = await ingestion_service.recover_unprocessed_events()
        if recovered > 0:
            logger.info("Recovered unprocessed trigger events", count=recovered)
        # Start periodic data retention sweep
        global _retention_task
        _retention_task = asyncio.create_task(_run_periodic_retention_sweep(db))
    except Exception as e:
        logger.error("Startup DB initialization failed", error=str(e))
    yield
    logger.info("Shutting down NexusMCP backend")
    if _retention_task is not None:
        _retention_task.cancel()
        try:
            await _retention_task
        except asyncio.CancelledError:
            pass
    await close_db()

app = FastAPI(
    title="NexusMCP Backend",
    version="0.2.0",
    lifespan=lifespan
)

settings = get_settings()

# Parse CORS allowlist from configuration
allow_origins = [o.strip() for o in settings.CORS_ALLOW_ORIGINS.split(",") if o.strip()]

# CORS middleware with allowlist (not wildcard in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Workspace-ID", "X-Correlation-ID", "X-CSRF-Token"],
)

# Security headers middleware (HSTS, CSP, X-Frame-Options, etc.)
app.add_middleware(SecurityHeadersMiddleware)

# CSRF protection for state-changing requests (production/secure cookies only)
app.add_middleware(
    CSRFProtectionMiddleware,
    exempt_paths=[
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
    ],
)

# Request body size limit
app.add_middleware(RequestSizeLimitMiddleware)

app.add_middleware(CorrelationIdMiddleware)

# Global exception handler to prevent leaking raw tracebacks to users
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled internal server error", error=str(exc), path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error occurred. Please try again later."}
    )

# Include routers
app.include_router(health.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(workspaces.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
app.include_router(workflows.router, prefix="/api/v1")
app.include_router(connections.router, prefix="/api/v1")
app.include_router(tools.router, prefix="/api/v1")
app.include_router(triggers.router, prefix="/api/v1")
app.include_router(executions.router, prefix="/api/v1")
app.include_router(observability.router, prefix="/api/v1")
