"""Correlation ID context and middleware for NexusMCP.

Propagates correlation IDs across HTTP requests, background tasks,
trigger ingestion, Temporal workflows, and workers.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Optional

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Context variable holding correlation ID for the current async task
_correlation_id_ctx: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str:
    """Get the current correlation ID or generate a new one if unset."""
    cid = _correlation_id_ctx.get()
    if not cid:
        cid = f"corr-{uuid.uuid4().hex[:12]}"
        _correlation_id_ctx.set(cid)
    return cid


def set_correlation_id(correlation_id: str) -> None:
    """Explicitly set the correlation ID for the current context."""
    _correlation_id_ctx.set(correlation_id)
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """FastAPI/Starlette middleware to capture or generate correlation IDs."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Check standard headers for incoming correlation ID
        correlation_id = (
            request.headers.get("X-Correlation-ID")
            or request.headers.get("X-Request-ID")
            or f"corr-{uuid.uuid4().hex[:12]}"
        )

        # Set context variable and bind to structlog contextvars
        set_correlation_id(correlation_id)

        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")
