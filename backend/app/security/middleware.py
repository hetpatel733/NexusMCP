"""Security middleware for NexusMCP FastAPI backend.

Provides:
- CORS allowlist (from configuration, not wildcard in production)
- HTTPS enforcement / Strict-Transport-Security header
- Secure cookie handling
- CSRF protection for state-changing operations
- Security headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy, CSP)
- Request size limits
"""

from __future__ import annotations

import re
from typing import Set

from fastapi import Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ..config import get_settings


# Methods that require CSRF protection when cookie-based auth is used
CSRF_PROTECTED_METHODS: Set[str] = {"POST", "PUT", "PATCH", "DELETE"}

# CSRF token header name
CSRF_HEADER_NAME = "X-CSRF-Token"

# Maximum request body size (default 10MB; webhooks have their own 2MB limit)
MAX_REQUEST_BODY_BYTES = 10 * 1024 * 1024


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        settings = get_settings()
        secure = settings.ENV == "production" or settings.SECURE_COOKIES

        # HSTS - enforce HTTPS in production
        if secure:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        # Prevent MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Clickjacking protection
        response.headers["X-Frame-Options"] = "DENY"

        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Content Security Policy (restrictive)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )

        # Permissions policy
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), "
            "payment=(), usb=(), magnetometer=(), gyroscope=()"
        )

        return response


class CSRFProtectionMiddleware(BaseHTTPMiddleware):
    """CSRF protection for state-changing requests.

    Requires a CSRF token in the X-CSRF-Token header for POST/PUT/PATCH/DELETE
    when the request includes a session cookie (cookie-based auth).
    The token is double-submitted: it must match the `nexus_csrf_token` cookie.
    """

    def __init__(
        self,
        app: ASGIApp,
        exempt_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.exempt_paths = set(exempt_paths or [])
        # Webhook endpoints are exempt (they use HMAC signatures instead)
        self.webhook_prefixes = {"/api/v1/triggers/webhook"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Exempt webhook paths (HMAC-verified instead)
        if any(path.startswith(p) for p in self.webhook_prefixes):
            return await call_next(request)

        # Exempt configured paths
        if path in self.exempt_paths:
            return await call_next(request)

        # Only protect state-changing methods
        if request.method not in CSRF_PROTECTED_METHODS:
            return await call_next(request)

        settings = get_settings()
        secure = settings.ENV == "production" or settings.SECURE_COOKIES

        # Only enforce CSRF when cookie-based auth is possible
        # (i.e., secure cookies enabled / production)
        if not secure:
            return await call_next(request)

        # Read CSRF token from cookie and header
        csrf_cookie = request.cookies.get("nexus_csrf_token")
        csrf_header = request.headers.get(CSRF_HEADER_NAME)

        if not csrf_cookie or not csrf_header:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token missing",
            )

        if not _constant_time_compare(csrf_cookie, csrf_header):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token mismatch",
            )

        return await call_next(request)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects requests with body larger than the configured limit."""

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
            except ValueError:
                size = 0
            if size > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Request body too large",
                )
        return await call_next(request)


def _constant_time_compare(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


def generate_csrf_token() -> str:
    """Generate a new CSRF token."""
    import secrets

    return secrets.token_urlsafe(32)


def setup_security_middleware(fastapi_app) -> None:
    """Attach all security middleware to the FastAPI app.

    Order matters: CORS -> SecurityHeaders -> CSRF -> SizeLimit -> CorrelationId
    """
    settings = get_settings()

    # Parse CORS allowlist
    allow_origins = [
        o.strip() for o in settings.CORS_ALLOW_ORIGINS.split(",") if o.strip()
    ]

    # Add CORS middleware with allowlist (not wildcard in production)
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Workspace-ID", "X-Correlation-ID", CSRF_HEADER_NAME],
    )

    # Add security headers
    fastapi_app.add_middleware(SecurityHeadersMiddleware)

    # Add CSRF protection
    fastapi_app.add_middleware(
        CSRFProtectionMiddleware,
        exempt_paths=[
            "/api/v1/auth/register",
            "/api/v1/auth/login",
            "/api/v1/auth/refresh",
        ],
    )

    # Add request size limit
    fastapi_app.add_middleware(RequestSizeLimitMiddleware)
