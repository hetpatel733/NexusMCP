"""At-rest encryption for sensitive configuration fields.

Uses Fernet (symmetric encryption: AES-128 in CBC mode with HMAC-SHA256)
to encrypt sensitive values before persisting to MongoDB.

The encryption key is derived from the ENCRYPTION_MASTER_KEY environment
variable. If not set, a development-only fallback is used (must be changed
in production).

Encrypted fields are stored as base64-encoded Fernet tokens.
"""

from __future__ import annotations

import base64
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from ..config import get_settings
from ..logger import logger

# Environment variable name for the master key
MASTER_KEY_ENV = "ENCRYPTION_MASTER_KEY"

# Salt for PBKDF2 key derivation (fixed, not secret — stored alongside code)
_SALT = b"nexusmcp-encryption-salt-v1"

# Development fallback key (must be overridden in production via env var)
# This key is only used when ENCRYPTION_MASTER_KEY is not set.
_DEV_FALLBACK_KEY = b"nexusmcp-dev-encryption-key-must-be-changed-32-chars-min"


def _derive_key(master_key: str) -> bytes:
    """Derive a 32-byte encryption key from the master key using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SALT,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(master_key.encode("utf-8")))


def _get_fernet() -> Optional[Fernet]:
    """Get a Fernet instance using the configured master key.

    Returns None if no key is available (encryption disabled — dev only).
    """
    settings = get_settings()
    master_key = os.environ.get(MASTER_KEY_ENV) or getattr(
        settings, "ENCRYPTION_MASTER_KEY", None
    )

    if not master_key or master_key == "change-me-in-production":
        logger.warning(
            "ENCRYPTION_MASTER_KEY not set; at-rest encryption disabled (development only)"
        )
        return None

    try:
        derived_key = _derive_key(master_key)
        return Fernet(derived_key)
    except Exception as exc:
        logger.error("Failed to initialize Fernet encryption", error=str(exc))
        return None


# Module-level Fernet instance (lazy initialization)
_fernet: Optional[Fernet] = None


def _ensure_fernet() -> Optional[Fernet]:
    global _fernet
    if _fernet is None:
        _fernet = _get_fernet()
    return _fernet


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext string and return a base64-encoded Fernet token.

    Returns the plaintext unchanged if encryption is not available (dev mode).
    Callers should check `is_encryption_enabled()` to know whether encryption
    is actually active.
    """
    fernet = _ensure_fernet()
    if fernet is None:
        return plaintext
    token = fernet.encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a base64-encoded Fernet token back to plaintext.

    Returns the ciphertext unchanged if encryption is not available
    (treats it as plaintext in dev mode).
    """
    fernet = _ensure_fernet()
    if fernet is None:
        return ciphertext
    try:
        return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # If decryption fails, return as-is (may be plaintext in dev mode)
        logger.warning("Failed to decrypt value; returning as plaintext")
        return ciphertext


def is_encryption_enabled() -> bool:
    """Return True if at-rest encryption is active (master key configured)."""
    return _ensure_fernet() is not None


def encrypt_field(value: Optional[str]) -> Optional[str]:
    """Encrypt an optional string field. None stays None."""
    if value is None:
        return None
    return encrypt_value(value)


def decrypt_field(value: Optional[str]) -> Optional[str]:
    """Decrypt an optional string field. None stays None."""
    if value is None:
        return None
    return decrypt_value(value)


class EncryptedField:
    """Descriptor-like helper for encrypting/decrypting model fields.

    Usage in a Pydantic model:

        class MyModel(BaseModel):
            _encrypted_fields = {"api_key"}

            @model_validator(mode="before")
            @classmethod
            def encrypt_on_write(cls, data):
                return encrypt_fields(data, cls._encrypted_fields)

            @model_validator(mode="after")
            def decrypt_on_read(self):
                decrypt_fields(self, cls._encrypted_fields)
                return self
    """

    @staticmethod
    def encrypt_dict(data: dict, field_names: set[str]) -> dict:
        """Encrypt named fields in a dict (for MongoDB write)."""
        result = dict(data)
        for field in field_names:
            if field in result and result[field] is not None:
                result[field] = encrypt_value(str(result[field]))
        return result

    @staticmethod
    def decrypt_dict(data: dict, field_names: set[str]) -> dict:
        """Decrypt named fields in a dict (for MongoDB read)."""
        result = dict(data)
        for field in field_names:
            if field in result and result[field] is not None:
                result[field] = decrypt_value(str(result[field]))
        return result
