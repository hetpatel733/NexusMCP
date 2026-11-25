import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt

from ..config import get_settings


def hash_token(token: str) -> str:
    """Compute SHA-256 hash of a token for safe database persistence."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(user_id: str, email: str, extra_claims: Optional[Dict[str, Any]] = None) -> tuple[str, int]:
    """Generate a short-lived access token."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    expire_time = now + expires_delta

    payload: Dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "type": "access",
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(expire_time.timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)

    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    expires_in_seconds = int(expires_delta.total_seconds())
    return token, expires_in_seconds


def create_refresh_token(user_id: str, email: str) -> tuple[str, str, datetime]:
    """Generate a long-lived refresh token. Returns (token, jti, expire_datetime)."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_delta = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    expire_time = now + expires_delta
    jti = str(uuid.uuid4())

    payload: Dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "type": "refresh",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expire_time.timestamp()),
    }

    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, jti, expire_time


def decode_token(token: str, expected_type: Optional[str] = None) -> Dict[str, Any]:
    """Decode and validate a JWT. Raises jwt.PyJWTError on invalid/expired token."""
    settings = get_settings()
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])

    if expected_type and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"Invalid token type: expected {expected_type}")

    return payload
