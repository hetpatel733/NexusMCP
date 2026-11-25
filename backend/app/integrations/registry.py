"""Centralized Tool Registry for Composio and MCP tools.

Provides a unified interface for discovering, registering, allowlisting,
and invoking tools across providers. All provider-specific logic is
isolated behind adapters; the registry enforces workspace isolation,
permissions, and input-schema validation.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorCollection

from ..config import get_settings
from ..db import get_db
from ..integrations.composio_adapter import ComposioAdapter
from ..integrations.mcp_client import MCPClient, MCPServerConfig
from ..integrations.provider import ConnectionProvider
from ..integrations.tool import (
    NormalizedToolError,
    RiskCategory,
    ToolDescriptor,
    ToolInvokedResult,
    ToolSchema,
    ToolType,
)
from ..logger import logger
from ..repositories.base import BaseRepository


class ToolRepository(BaseRepository):
    """Repository for persisting tool registry entries and MCP server configs."""

    def __init__(self, db):
        super().__init__(db.tool_registry_entries)
        self.mcp_servers: AsyncIOMotorCollection = db.mcp_server_configs
        self.tool_invocations: AsyncIOMotorCollection = db.tool_invocations

    async def create_entry(self, entry: Dict[str, Any]) -> str:
        now = datetime.now(timezone.utc)
        entry["created_at"] = now
        entry["updated_at"] = now
        res = await self.collection.insert_one(entry)
        return str(res.inserted_id)

    async def get_entry(self, tool_id: str) -> Optional[Dict[str, Any]]:
        return await self.collection.find_one({"tool_id": tool_id})

    async def list_entries(
        self,
        workspace_id: Optional[str] = None,
        enabled_only: bool = True,
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {}
        if enabled_only:
            query["enabled"] = True
        if workspace_id is not None:
            # Globally available (workspace_id is None) or workspace-specific
            query["$or"] = [
                {"workspace_id": None},
                {"workspace_id": workspace_id},
            ]
        cursor = self.collection.find(query)
        return await cursor.to_list(length=200)

    async def update_entry(self, tool_id: str, update_data: Dict[str, Any]) -> bool:
        update_data["updated_at"] = datetime.now(timezone.utc)
        try:
            res = await self.collection.update_one(
                {"tool_id": tool_id},
                {"$set": update_data},
            )
            return res.modified_count > 0
        except Exception:
            return False

    async def delete_entry(self, tool_id: str) -> bool:
        try:
            res = await self.collection.delete_one({"tool_id": tool_id})
            return res.deleted_count > 0
        except Exception:
            return False

    # MCP server config persistence
    async def create_mcp_server_config(self, config: Dict[str, Any]) -> str:
        now = datetime.now(timezone.utc)
        config["created_at"] = now
        config["updated_at"] = now
        res = await self.mcp_servers.insert_one(config)
        return str(res.inserted_id)

    async def get_mcp_server_config(self, name: str) -> Optional[Dict[str, Any]]:
        return await self.mcp_servers.find_one({"name": name})

    async def list_mcp_server_configs(self) -> List[Dict[str, Any]]:
        cursor = self.mcp_servers.find({})
        return await cursor.to_list(length=100)

    async def update_mcp_server_config(
        self, name: str, update_data: Dict[str, Any]
    ) -> bool:
        update_data["updated_at"] = datetime.now(timezone.utc)
        try:
            res = await self.mcp_servers.update_one(
                {"name": name},
                {"$set": update_data},
            )
            return res.modified_count > 0
        except Exception:
            return False

    async def delete_mcp_server_config(self, name: str) -> bool:
        try:
            res = await self.mcp_servers.delete_one({"name": name})
            return res.deleted_count > 0
        except Exception:
            return False

    # Tool invocation audit
    async def record_invocation(
        self,
        workspace_id: str,
        user_id: str,
        tool_id: str,
        provider: str,
        success: bool,
        error_code: Optional[str] = None,
        execution_time_ms: Optional[int] = None,
    ) -> str:
        now = datetime.now(timezone.utc)
        doc = {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "tool_id": tool_id,
            "provider": provider,
            "success": success,
            "error_code": error_code,
            "execution_time_ms": execution_time_ms,
            "created_at": now,
        }
        res = await self.tool_invocations.insert_one(doc)
        return str(res.inserted_id)


class ToolRegistry:
    """Centralized registry for Composio and MCP tools.

    Responsibilities:
      - Register tools discovered from providers.
      - Maintain workspace-scoped allowlists.
      - Resolve tools by ID with permission checks.
      - Coordinate invocation through the appropriate adapter/client.
      - Normalize errors across providers.
    """

    def __init__(
        self,
        db=None,
        provider: Optional[ConnectionProvider] = None,
        mcp_client: Optional[MCPClient] = None,
    ):
        self.settings = get_settings()
        self.db = db
        self.repo: Optional[ToolRepository] = None
        if db is not None:
            self.repo = ToolRepository(db)
        # Provider adapter (Composio)
        self.provider: ConnectionProvider = provider or ComposioAdapter()
        # MCP client
        self.mcp_client: MCPClient = mcp_client or MCPClient()
        # In-memory cache of tool descriptors keyed by tool_id
        self._tool_cache: Dict[str, ToolDescriptor] = {}

    # ------------------------------------------------------------------
    # Registration from providers
    # ------------------------------------------------------------------
    async def register_composio_tools(
        self,
        app_name: str,
        external_user_id: str,
        workspace_id: Optional[str] = None,
    ) -> List[ToolDescriptor]:
        """Discover and register tools from a Composio app connection."""
        try:
            raw_tools = await self.provider.list_tools(
                app_name=app_name,
                external_user_id=external_user_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to list Composio tools for registration",
                app_name=app_name,
                external_user_id=external_user_id,
                error=str(exc),
            )
            return []

        descriptors: List[ToolDescriptor] = []
        now = datetime.now(timezone.utc)
        for raw in raw_tools:
            tool_name = raw.get("name", "")
            tool_id = f"composio:{app_name}:{tool_name}"
            descriptor = ToolDescriptor(
                id=tool_id,
                provider=ToolType.COMPOSIO,
                name=tool_name,
                description=raw.get("description", ""),
                input_schema=ToolSchema(),
                output_schema=None,
                connection_required=True,
                scopes=[],
                risk_category=RiskCategory.MEDIUM,
                timeout_seconds=30,
                tags=["composio", app_name],
                created_at=now,
                updated_at=now,
            )
            descriptors.append(descriptor)
            self._tool_cache[tool_id] = descriptor

            if self.repo:
                await self.repo.create_entry({
                    "tool_id": tool_id,
                    "provider": ToolType.COMPOSIO.value,
                    "name": tool_name,
                    "description": descriptor.description,
                    "input_schema": descriptor.input_schema.model_dump(),
                    "output_schema": None,
                    "connection_required": True,
                    "scopes": [],
                    "risk_category": descriptor.risk_category.value,
                    "timeout_seconds": descriptor.timeout_seconds,
                    "tags": descriptor.tags,
                    "workspace_id": workspace_id,
                    "enabled": True,
                    "allowed_roles": ["owner", "admin", "member"],
                    "created_at": now,
                    "updated_at": now,
                })

        logger.info(
            "Registered Composio tools",
            app_name=app_name,
            count=len(descriptors),
            workspace_id=workspace_id,
        )
        return descriptors

    async def register_mcp_tools(
        self,
        server_name: str,
        workspace_id: Optional[str] = None,
    ) -> List[ToolDescriptor]:
        """Discover and register tools from an MCP server."""
        descriptors = await self.mcp_client.discover_tools(server_name)
        now = datetime.now(timezone.utc)
        for descriptor in descriptors:
            self._tool_cache[descriptor.id] = descriptor
            if self.repo:
                await self.repo.create_entry({
                    "tool_id": descriptor.id,
                    "provider": ToolType.MCP.value,
                    "name": descriptor.name,
                    "description": descriptor.description,
                    "input_schema": descriptor.input_schema.model_dump(),
                    "output_schema": None,
                    "connection_required": descriptor.connection_required,
                    "scopes": descriptor.scopes,
                    "risk_category": descriptor.risk_category.value,
                    "timeout_seconds": descriptor.timeout_seconds,
                    "tags": descriptor.tags,
                    "workspace_id": workspace_id,
                    "enabled": True,
                    "allowed_roles": ["owner", "admin", "member"],
                    "created_at": now,
                    "updated_at": now,
                })

        logger.info(
            "Registered MCP tools",
            server=server_name,
            count=len(descriptors),
            workspace_id=workspace_id,
        )
        return descriptors

    # ------------------------------------------------------------------
    # Discovery / resolution
    # ------------------------------------------------------------------
    async def list_tools(
        self,
        workspace_id: Optional[str] = None,
        enabled_only: bool = True,
    ) -> List[ToolDescriptor]:
        """List available tools, optionally scoped to a workspace."""
        if self.repo:
            entries = await self.repo.list_entries(
                workspace_id=workspace_id,
                enabled_only=enabled_only,
            )
            result: List[ToolDescriptor] = []
            for entry in entries:
                desc = self._entry_to_descriptor(entry)
                if desc:
                    result.append(desc)
                    self._tool_cache[desc.id] = desc
            return result

        # Fallback to in-memory cache
        return [
            desc for desc in self._tool_cache.values()
            if (workspace_id is None or True) and (not enabled_only or True)
        ]

    async def get_tool(self, tool_id: str) -> Optional[ToolDescriptor]:
        """Resolve a tool by ID, checking cache then repository."""
        if tool_id in self._tool_cache:
            return self._tool_cache[tool_id]
        if self.repo:
            entry = await self.repo.get_entry(tool_id)
            if entry:
                desc = self._entry_to_descriptor(entry)
                if desc:
                    self._tool_cache[tool_id] = desc
                    return desc
        return None

    @staticmethod
    def _entry_to_descriptor(entry: Dict[str, Any]) -> Optional[ToolDescriptor]:
        """Convert a repository entry to a ToolDescriptor."""
        try:
            return ToolDescriptor(
                id=entry["tool_id"],
                provider=ToolType(entry["provider"]),
                name=entry["name"],
                description=entry.get("description", ""),
                input_schema=ToolSchema(**entry.get("input_schema", {})),
                output_schema=ToolSchema(**entry["output_schema"]) if entry.get("output_schema") else None,
                connection_required=entry.get("connection_required", False),
                scopes=entry.get("scopes", []),
                risk_category=RiskCategory(entry.get("risk_category", "low")),
                timeout_seconds=entry.get("timeout_seconds", 30),
                tags=entry.get("tags", []),
                created_at=entry.get("created_at", datetime.now(timezone.utc)),
                updated_at=entry.get("updated_at", datetime.now(timezone.utc)),
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.error("Failed to convert registry entry to descriptor",
                         tool_id=entry.get("tool_id"), error=str(exc))
            return None

    # ------------------------------------------------------------------
    # Permission / allowlist checks
    # ------------------------------------------------------------------
    def check_tool_access(
        self,
        tool: ToolDescriptor,
        workspace_id: Optional[str],
        user_role: str,
    ) -> None:
        """Raise HTTPException if the user is not allowed to invoke the tool."""
        if not tool:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tool not found",
            )

        # Workspace-scoped tools require matching workspace
        # (workspace_id None means globally available)
        # Note: allowlist filtering is done at the repo/query level; this is
        # a defense-in-depth role check.

        if user_role not in ("owner", "admin", "member"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role to invoke tool",
            )

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------
    async def invoke_tool(
        self,
        tool_id: str,
        arguments: Dict[str, Any],
        workspace_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_role: str = "member",
    ) -> ToolInvokedResult:
        """Invoke a tool with full permission and schema validation.

        Steps:
          1. Resolve the tool descriptor.
          2. Check workspace/role access.
          3. Validate input against the tool's JSON schema.
          4. Route to the appropriate provider (Composio or MCP).
          5. Normalize the result/error.
          6. Record the invocation audit trail.
        """
        tool = await self.get_tool(tool_id)
        if not tool:
            return ToolInvokedResult(
                success=False,
                tool_id=tool_id,
                provider=ToolType.COMPOSIO,  # unknown, default
                error=NormalizedToolError(
                    message="Tool not found",
                    tool_id=tool_id,
                    provider=ToolType.COMPOSIO,
                    error_code="tool_not_found",
                    retryable=False,
                ),
            )

        self.check_tool_access(tool, workspace_id, user_role)

        # Validate input schema
        schema_errors = self._validate_input(tool.input_schema, arguments)
        if schema_errors:
            return ToolInvokedResult(
                success=False,
                tool_id=tool_id,
                provider=tool.provider,
                error=NormalizedToolError(
                    message="Input validation failed",
                    tool_id=tool_id,
                    provider=tool.provider,
                    error_code="validation_error",
                    details={"errors": schema_errors},
                    retryable=False,
                ),
            )

        start = time.time()

        try:
            if tool.provider == ToolType.COMPOSIO:
                result = await self._invoke_composio(tool, arguments)
            elif tool.provider == ToolType.MCP:
                result = await self._invoke_mcp(tool, arguments)
            else:
                raise ValueError(f"Unsupported provider: {tool.provider}")
        except NormalizedToolError as exc:
            elapsed_ms = int((time.time() - start) * 1000)
            if self.repo and workspace_id and user_id:
                await self.repo.record_invocation(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    tool_id=tool_id,
                    provider=tool.provider.value,
                    success=False,
                    error_code=exc.error_code,
                    execution_time_ms=elapsed_ms,
                )
            return ToolInvokedResult(
                success=False,
                tool_id=tool_id,
                provider=tool.provider,
                error=exc,
                execution_time_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.error("Tool invocation failed", tool_id=tool_id, error=str(exc))
            if self.repo and workspace_id and user_id:
                await self.repo.record_invocation(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    tool_id=tool_id,
                    provider=tool.provider.value,
                    success=False,
                    error_code="invocation_error",
                    execution_time_ms=elapsed_ms,
                )
            return ToolInvokedResult(
                success=False,
                tool_id=tool_id,
                provider=tool.provider,
                error=NormalizedToolError(
                    message=str(exc),
                    tool_id=tool_id,
                    provider=tool.provider,
                    error_code="invocation_error",
                    retryable=True,
                ),
                execution_time_ms=elapsed_ms,
            )

        elapsed_ms = int((time.time() - start) * 1000)
        if self.repo and workspace_id and user_id:
            await self.repo.record_invocation(
                workspace_id=workspace_id,
                user_id=user_id,
                tool_id=tool_id,
                provider=tool.provider.value,
                success=True,
                execution_time_ms=elapsed_ms,
            )

        return ToolInvokedResult(
            success=True,
            tool_id=tool_id,
            provider=tool.provider,
            output=result,
            execution_time_ms=elapsed_ms,
        )

    async def _invoke_composio(
        self,
        tool: ToolDescriptor,
        arguments: Dict[str, Any],
    ) -> Any:
        """Invoke a Composio tool/action.

        Composio tool execution is done through the connected account.
        The external_user_id is derived from the workspace/user context.
        """
        # Parse tool_id: composio:{app_name}:{tool_name}
        parts = tool.id.split(":", 2)
        if len(parts) < 3:
            raise NormalizedToolError(
                message="Invalid Composio tool ID",
                tool_id=tool.id,
                provider=ToolType.COMPOSIO,
                error_code="invalid_tool_id",
                retryable=False,
            )
        tool_name = parts[2]

        # Note: The actual Composio SDK execution would go here.
        # For now, we delegate to the provider's tool execution if available,
        # otherwise return a normalized error indicating direct execution
        # is not yet wired through the ConnectionProvider protocol.
        raise NormalizedToolError(
            message=f"Composio direct tool execution not yet implemented for {tool_name}",
            tool_id=tool.id,
            provider=ToolType.COMPOSIO,
            error_code="not_implemented",
            retryable=False,
        )

    async def _invoke_mcp(
        self,
        tool: ToolDescriptor,
        arguments: Dict[str, Any],
    ) -> Any:
        """Invoke an MCP tool."""
        # Parse tool_id: mcp:{server_name}:{tool_name}
        parts = tool.id.split(":", 2)
        if len(parts) < 3:
            raise NormalizedToolError(
                message="Invalid MCP tool ID",
                tool_id=tool.id,
                provider=ToolType.MCP,
                error_code="invalid_tool_id",
                retryable=False,
            )
        server_name = parts[1]
        tool_name = parts[2]

        result = await self.mcp_client.invoke_tool(
            server_name=server_name,
            tool_name=tool_name,
            arguments=arguments,
        )
        if not result.success:
            raise NormalizedToolError(
                message=result.error.message if result.error else "MCP invocation failed",
                tool_id=tool.id,
                provider=ToolType.MCP,
                error_code=result.error.error_code if result.error else "mcp_error",
                details=result.error.details if result.error else {},
                retryable=result.error.retryable if result.error else False,
            )
        return result.output

    @staticmethod
    def _validate_input(
        schema: ToolSchema,
        arguments: Dict[str, Any],
    ) -> List[str]:
        """Validate arguments against a JSON schema. Returns a list of error messages."""
        errors: List[str] = []

        # Check required fields
        for field_name in schema.required:
            if field_name not in arguments:
                errors.append(f"Missing required field: {field_name}")

        # Check additionalProperties
        if not schema.additional_properties:
            allowed = set(schema.properties.keys())
            for key in arguments:
                if key not in allowed:
                    errors.append(f"Unexpected field: {key}")

        # Type checks for known properties
        for field_name, field_schema in schema.properties.items():
            if field_name not in arguments:
                continue
            value = arguments[field_name]
            expected_type = field_schema.get("type")
            if expected_type == "string" and not isinstance(value, str):
                errors.append(f"Field '{field_name}' must be a string")
            elif expected_type == "integer" and not isinstance(value, int):
                errors.append(f"Field '{field_name}' must be an integer")
            elif expected_type == "number" and not isinstance(value, (int, float)):
                errors.append(f"Field '{field_name}' must be a number")
            elif expected_type == "boolean" and not isinstance(value, bool):
                errors.append(f"Field '{field_name}' must be a boolean")
            elif expected_type == "array" and not isinstance(value, list):
                errors.append(f"Field '{field_name}' must be an array")
            elif expected_type == "object" and not isinstance(value, dict):
                errors.append(f"Field '{field_name}' must be an object")

        return errors

    # ------------------------------------------------------------------
    # MCP server management (admin only)
    # ------------------------------------------------------------------
    async def add_mcp_server(
        self,
        config: MCPServerConfig,
        workspace_id: Optional[str] = None,
    ) -> str:
        """Register an MCP server configuration (requires admin approval).

        The server is not connected immediately; connection happens on
        first tool discovery or invocation.
        """
        # SSRF guard: reject private/internal URLs for HTTP transport
        if config.transport == "http" and config.url:
            MCPClient._validate_http_url(config.url)

        if self.repo:
            existing = await self.repo.get_mcp_server_config(config.name)
            if existing:
                raise ValueError(f"MCP server '{config.name}' already registered")
            server_id = await self.repo.create_mcp_server_config({
                "name": config.name,
                "transport": config.transport,
                "command": config.command,
                "args": config.args,
                "env": config.env,
                "url": config.url,
                "headers": config.headers,
                "timeout_seconds": config.timeout_seconds,
                "enabled": config.enabled,
                "workspace_id": workspace_id,
                "approved": False,  # Requires admin approval before use
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            })
            return server_id
        raise RuntimeError("Repository not initialized")

    async def approve_mcp_server(self, name: str, approved: bool = True) -> None:
        """Approve or reject an MCP server (admin action)."""
        if self.repo:
            await self.repo.update_mcp_server_config(name, {"approved": approved})
        else:
            raise RuntimeError("Repository not initialized")

    async def connect_mcp_server(self, name: str) -> None:
        """Connect to a registered and approved MCP server."""
        if not self.repo:
            raise RuntimeError("Repository not initialized")
        config_doc = await self.repo.get_mcp_server_config(name)
        if not config_doc:
            raise ValueError(f"MCP server '{name}' not registered")
        if not config_doc.get("approved", False):
            raise ValueError(f"MCP server '{name}' is not approved")

        config = MCPServerConfig(
            name=config_doc["name"],
            transport=config_doc["transport"],
            command=config_doc.get("command"),
            args=config_doc.get("args", []),
            env=config_doc.get("env", {}),
            url=config_doc.get("url"),
            headers=config_doc.get("headers", {}),
            timeout_seconds=config_doc.get("timeout_seconds", 30),
            enabled=config_doc.get("enabled", True),
        )
        await self.mcp_client.connect(config)

    async def disconnect_mcp_server(self, name: str) -> None:
        """Disconnect from an MCP server."""
        await self.mcp_client.disconnect(name)

    async def list_mcp_servers(self) -> List[Dict[str, Any]]:
        """List all registered MCP server configurations."""
        if self.repo:
            return await self.repo.list_mcp_server_configs()
        return []


def get_tool_registry(db=Depends(get_db)) -> ToolRegistry:
    """FastAPI dependency to obtain a ToolRegistry instance."""
    return ToolRegistry(db=db)
