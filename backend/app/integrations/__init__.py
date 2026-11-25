"""NexusMCP integrations package.

Contains provider abstractions, adapters, connection management, MCP client,
tool registry, and canonical tool descriptors for third-party integrations.
"""

from .mcp_client import MCPClient, MCPServerConfig
from .provider import ConnectionProvider
from .registry import ToolRegistry, ToolRepository, get_tool_registry
from .tool import (
    NormalizedToolError,
    RiskCategory,
    ToolDescriptor,
    ToolInvokedResult,
    ToolRegistryEntry,
    ToolSchema,
    ToolType,
)

__all__ = [
    "MCPClient",
    "MCPServerConfig",
    "ConnectionProvider",
    "ToolRegistry",
    "ToolRepository",
    "get_tool_registry",
    "NormalizedToolError",
    "RiskCategory",
    "ToolDescriptor",
    "ToolInvokedResult",
    "ToolRegistryEntry",
    "ToolSchema",
    "ToolType",
]
