from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.v1 import audit, auth, connections, executions, health, tools, triggers, workflows, workspaces
from .db import close_db, get_db, init_db
from .logger import logger
from .triggers.service import TriggerIngestionService


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
    except Exception as e:
        logger.error("Startup DB initialization failed", error=str(e))
    yield
    logger.info("Shutting down NexusMCP backend")
    await close_db()

app = FastAPI(
    title="NexusMCP Backend",
    version="0.2.0",
    lifespan=lifespan
)

# CORS middleware for development and self-hosted environments
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Workspace-ID"],
)

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
