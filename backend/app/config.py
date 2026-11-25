import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT_DIR = Path(__file__).resolve().parent.parent.parent
_ROOT_ENV = _ROOT_DIR / ".env"
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_BACKEND_ENV = _BACKEND_DIR / ".env"
_CUSTOM_ENV = Path(os.environ["ENV_FILE"]).resolve() if "ENV_FILE" in os.environ else None

_ENV_FILES = tuple(
    str(f) for f in [_CUSTOM_ENV, _ROOT_ENV, _BACKEND_ENV, Path(".env").resolve()] if f is not None
)


class Settings(BaseSettings):
    ENV: str = Field(default="development", description="Application environment")
    LOG_LEVEL: str = Field(default="info", description="Logging level")
    PORT: int = Field(default=8000, description="Port to listen on")
    HOST: str = Field(default="0.0.0.0", description="Host to listen on")

    # MongoDB
    MONGO_URI: str = Field(
        default="mongodb://root:example@localhost:27017/nexusmcp?authSource=admin",
        description="MongoDB connection URI",
    )
    MONGO_DB_NAME: str = Field(default="nexusmcp", description="MongoDB database name")

    # Temporal
    TEMPORAL_HOST: str = Field(default="localhost:7233", description="Temporal server address")
    TEMPORAL_NAMESPACE: str = Field(default="default", description="Temporal namespace")

    # Security & Auth
    JWT_SECRET_KEY: str = Field(
        default="nexusmcp-dev-secret-key-must-be-changed-in-production-min-32-chars",
        description="JWT secret key"
    )
    JWT_ALGORITHM: str = Field(default="HS256", description="JWT signing algorithm")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, description="Access token expiration in minutes")
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, description="Refresh token expiration in days")
    AUTH_RATE_LIMIT_PER_MINUTE: int = Field(default=10, description="Max auth requests per minute per IP")
    SECURE_COOKIES: bool = Field(default=False, description="Set secure cookie flag")
    ENCRYPTION_MASTER_KEY: Optional[str] = Field(
        default=None,
        description="Master key for at-rest encryption of sensitive fields",
    )
    CORS_ALLOW_ORIGINS: str = Field(
        default="*",
        description="Comma-separated list of allowed CORS origins (use specific origins in production)",
    )
    API_RATE_LIMIT_PER_MINUTE: int = Field(
        default=60,
        description="Max API requests per minute per user/workspace",
    )
    EXECUTION_QUOTA_PER_USER_PER_HOUR: int = Field(
        default=1000,
        description="Max workflow executions per user per hour",
    )
    PAYLOAD_MAX_BYTES: int = Field(
        default=2 * 1024 * 1024,
        description="Maximum webhook payload size in bytes (default 2MB)",
    )
    PAYLOAD_EXTERNALIZATION_THRESHOLD_BYTES: int = Field(
        default=32 * 1024,
        description="Payload size above which data is externalized (default 32KB)",
    )
    # Data retention (in days)
    RETENTION_DEDUPLICATION_DAYS: int = Field(
        default=7, description="Deduplication record retention in days"
    )
    RETENTION_DLQ_DAYS: int = Field(
        default=30, description="Dead letter queue event retention in days"
    )
    RETENTION_AUDIT_LOGS_DAYS: int = Field(
        default=90, description="Audit log retention in days"
    )
    RETENTION_EXECUTION_LOGS_DAYS: int = Field(
        default=90, description="Execution log retention in days"
    )
    RETENTION_STEP_LOGS_DAYS: int = Field(
        default=90, description="Step log retention in days"
    )
    RETENTION_PAYLOADS_DAYS: int = Field(
        default=30, description="Externalized payload retention in days"
    )

    # Composio Integration
    COMPOSIO_API_KEY: Optional[str] = Field(default=None, description="Composio API key for managed OAuth")
    COMPOSIO_ENTITY_ID: Optional[str] = Field(default=None, description="Composio entity/user identifier")
    COMPOSIO_BASE_URL: str = Field(default="https://api.composio.dev", description="Composio API base URL")
    COMPOSIO_CALLBACK_URL: Optional[str] = Field(default=None, description="Composio OAuth callback URL")
    COMPOSIO_ENTITY_TYPE: str = Field(default="user", description="Composio entity type (user, workspace, organization)")
    COMPOSIO_ENABLED_APPS: str = Field(default="", description="Comma-separated list of enabled Composio apps")

    # MCP Integration
    MCP_ENABLED: bool = Field(default=False, description="Enable MCP server integration")
    MCP_ALLOWED_TRANSPORTS: str = Field(default="stdio,http", description="Comma-separated allowed MCP transports")
    MCP_DEFAULT_TIMEOUT_SECONDS: int = Field(default=30, description="Default MCP tool invocation timeout")
    MCP_ALLOWED_STDIO_COMMANDS: str = Field(
        default="npx,node,python,python3,uvx,pipx",
        description="Comma-separated allowlist of stdio commands for MCP servers",
    )

    # AI Workflow Generation
    LLM_PROVIDER: str = Field(default="mock", description="LLM provider: mock or openai")
    OPENAI_API_KEY: Optional[str] = Field(default=None, description="OpenAI API key")
    LLM_MODEL: str = Field(default="gpt-4o", description="Default LLM model name")
    LLM_TEMPERATURE: float = Field(default=0.0, description="LLM generation temperature")

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

@lru_cache()
def get_settings() -> Settings:
    return Settings()
