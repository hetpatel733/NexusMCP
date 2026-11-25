"""Sensitive Data Redaction Utility for NexusMCP.

Strictly redacts secrets, tokens, credentials, and sensitive headers from
execution logs, step inputs/outputs, error messages, and audit trails.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set

# Sensitive dictionary keys to redact
SENSITIVE_KEY_SUBSTRINGS: Set[str] = {
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "auth",
    "authorization",
    "api_key",
    "apikey",
    "private_key",
    "client_secret",
    "credential",
    "signature",
    "cookie",
    "set-cookie",
    "session",
    "session_id",
    "pin",
    "ssn",
    "credit_card",
    "cvv",
}

# Regex patterns for sensitive text within string values
REDACTION_PATTERNS: List[re.Pattern] = [
    # Bearer tokens: Bearer <jwt or arbitrary token>
    re.compile(r"Bearer\s+([A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+|[A-Za-z0-9\-_\.]{20,})", re.IGNORECASE),
    # JWT tokens in raw strings: eyJ...
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    # Private Key blocks
    re.compile(r"-----BEGIN[ A-Z0-9_-]+PRIVATE KEY-----[\s\S]+?-----END[ A-Z0-9_-]+PRIVATE KEY-----"),
    # Generic API Keys / Secrets like sk-..., key-..., ghp_...
    re.compile(r"\b(sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{20,}|gho_[a-zA-Z0-9]{20,}|glpat-[a-zA-Z0-9]{20,})\b"),
]

REDACTED_PLACEHOLDER = "[REDACTED]"


def redact_string(val: str) -> str:
    """Scan and redact known secret formats from string values."""
    if not val or not isinstance(val, str):
        return val

    redacted = val
    # Replace regex matched sensitive tokens
    for pattern in REDACTION_PATTERNS:
        redacted = pattern.sub(REDACTED_PLACEHOLDER, redacted)

    return redacted


def redact_sensitive_data(data: Any) -> Any:
    """Recursively scrub sensitive keys and token patterns from dicts, lists, and strings."""
    if isinstance(data, dict):
        result: Dict[str, Any] = {}
        for key, value in data.items():
            key_lower = str(key).lower()
            if any(sensitive in key_lower for sensitive in SENSITIVE_KEY_SUBSTRINGS):
                result[key] = REDACTED_PLACEHOLDER
            else:
                result[key] = redact_sensitive_data(value)
        return result
    elif isinstance(data, list):
        return [redact_sensitive_data(item) for item in data]
    elif isinstance(data, tuple):
        return tuple(redact_sensitive_data(item) for item in data)
    elif isinstance(data, str):
        return redact_string(data)
    return data
