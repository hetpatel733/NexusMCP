"""Canonical Tool Descriptor and tool-related data models.

Defines a unified, provider-agnostic representation of a tool/action that can
be discovered, validated, and invoked across Composio and MCP providers.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class RiskCategory(str, Enum):
    """Risk categories for tool execution.

    Low: read-only, no side effects (e.g. list files, read a sheet).
    Medium: mutating but scoped to the user's own data (e.g. create issue).
    High: destructive, cross-resource, or externally visible (e.g. send email,
    delete resource, publish content).
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolType(str, Enum):
    COMPOSIO = "composio"
    MCP = "mcp"


class ToolSchema(BaseModel):
    """JSON Schema for tool input/output validation."""
    type: str = Field(default="object")
    properties: Dict[str, Any] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)
    additional_properties: bool = Field(default=False)


class ToolDescriptor(BaseModel):
    """Canonical descriptor for a discoverable tool/action.

    This is the contract between providers (Composio, MCP) and the
    tool registry/service layer. All providers normalize their metadata
    into this shape.
    """
    id: str
    provider: ToolType
    name: str
    description: str
    input_schema: ToolSchema
    output_schema: Optional[ToolSchema] = None
    connection_required: bool = False
    scopes: List[str] = Field(default_factory=list)
    risk_category: RiskCategory = RiskCategory.LOW
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    tags: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ToolRegistryEntry(BaseModel):
    """Registry entry linking a tool descriptor to workspace allowlisting."""
    tool_id: str
    workspace_id: Optional[str] = None  # None means globally available
    enabled: bool = True
    allowed_roles: List[str] = Field(default_factory=lambda: ["owner", "admin", "member"])
    created_at: datetime
    updated_at: datetime


class NormalizedToolError(Exception):
    """Normalized error raised when a tool invocation fails.

    Provides a consistent error shape across providers so callers do not
    depend on provider-specific exception types.
    """

    def __init__(
        self,
        message: str,
        tool_id: str,
        provider: ToolType,
        error_code: str = "tool_error",
        details: Optional[Dict[str, Any]] = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.tool_id = tool_id
        self.provider = provider
        self.error_code = error_code
        self.details = details or {}
        self.retryable = retryable

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "tool_id": self.tool_id,
            "provider": self.provider.value,
            "details": self.details,
            "retryable": self.retryable,
        }


class ToolInvokedResult(BaseModel):
    """Normalized result of a tool invocation."""
    success: bool
    tool_id: str
    provider: ToolType
    output: Optional[Any] = None
    error: Optional[NormalizedToolError] = None
    execution_time_ms: Optional[int] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)
