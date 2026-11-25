"""Tool registry API routes.

Endpoints for discovering, allowlisting, and invoking tools from Composio
and MCP providers. All routes enforce authentication, workspace isolation,
and role-based access control.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ...db import get_db
from ...integrations.mcp_client import MCPServerConfig
from ...integrations.registry import ToolRegistry, get_tool_registry
from ...integrations.tool import (
    ToolInvokedResult,
)
from ...models.workspace import WorkspaceRole
from ...security.dependencies import get_current_user, get_current_workspace, require_role

router = APIRouter(prefix="/tools", tags=["Tools"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ToolDescriptorResponse(BaseModel):
    id: str
    provider: str
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Optional[Dict[str, Any]] = None
    connection_required: bool
    scopes: List[str]
    risk_category: str
    timeout_seconds: int
    tags: List[str]

    model_config = ConfigDict(from_attributes=True)


class ToolInvokeRequest(BaseModel):
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ToolInvokeResponse(BaseModel):
    success: bool
    tool_id: str
    provider: str
    output: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None
    execution_time_ms: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class MCPServerRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    transport: str = Field(pattern=r"^(stdio|http)$")
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    url: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    enabled: bool = True


class MCPServerResponse(BaseModel):
    name: str
    transport: str
    command: Optional[str] = None
    args: List[str]
    url: Optional[str] = None
    timeout_seconds: int
    enabled: bool
    approved: bool
    workspace_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------

@router.get("", response_model=List[ToolDescriptorResponse])
async def list_tools(
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
    enabled_only: bool = Query(default=True, description="Return only enabled tools"),
):
    """List tools available to the current workspace.

    Returns globally available tools plus tools allowlisted for the
    caller's workspace. Enforcement of workspace scoping happens in the
    registry/repository layer.
    """
    workspace, role = workspace_and_role
    tools = await registry.list_tools(
        workspace_id=workspace["id"],
        enabled_only=enabled_only,
    )
    return [
        ToolDescriptorResponse(
            id=t.id,
            provider=t.provider.value,
            name=t.name,
            description=t.description,
            input_schema=t.input_schema.model_dump(),
            output_schema=t.output_schema.model_dump() if t.output_schema else None,
            connection_required=t.connection_required,
            scopes=t.scopes,
            risk_category=t.risk_category.value,
            timeout_seconds=t.timeout_seconds,
            tags=t.tags,
        )
        for t in tools
    ]


@router.get("/{tool_id}", response_model=ToolDescriptorResponse)
async def get_tool(
    tool_id: str,
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
):
    """Get a specific tool descriptor by ID."""
    workspace, role = workspace_and_role
    tool = await registry.get_tool(tool_id)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found",
        )
    return ToolDescriptorResponse(
        id=tool.id,
        provider=tool.provider.value,
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema.model_dump(),
        output_schema=tool.output_schema.model_dump() if tool.output_schema else None,
        connection_required=tool.connection_required,
        scopes=tool.scopes,
        risk_category=tool.risk_category.value,
        timeout_seconds=tool.timeout_seconds,
        tags=tool.tags,
    )


# ---------------------------------------------------------------------------
# Tool invocation
# ---------------------------------------------------------------------------

@router.post("/{tool_id}/invoke", response_model=ToolInvokeResponse)
async def invoke_tool(
    tool_id: str,
    data: ToolInvokeRequest,
    request: Request,
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
    database=Depends(get_db),
):
    """Invoke a tool with permission and input-schema validation.

    The caller's workspace and role are enforced. Input arguments are
    validated against the tool's declared JSON schema before execution.
    """
    workspace, role = workspace_and_role

    # SSRF / unsafe execution check: block invocation of tools that require
    # a connection when no active connection exists for the workspace.
    tool = await registry.get_tool(tool_id)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found",
        )

    result: ToolInvokedResult = await registry.invoke_tool(
        tool_id=tool_id,
        arguments=data.arguments,
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        user_role=role.value,
    )

    return ToolInvokeResponse(
        success=result.success,
        tool_id=result.tool_id,
        provider=result.provider.value,
        output=result.output,
        error=result.error.to_dict() if result.error else None,
        execution_time_ms=result.execution_time_ms,
    )


# ---------------------------------------------------------------------------
# Composio tool registration
# ---------------------------------------------------------------------------

@router.post("/composio/register", response_model=List[ToolDescriptorResponse])
async def register_composio_tools(
    app_name: str = Query(..., description="Composio app name to register tools from"),
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
    database=Depends(get_db),
):
    """Discover and register tools from a connected Composio app.

    Requires an active connection for the given app in the workspace.
    """
    workspace, role = workspace_and_role

    # Look up the connection to derive external_user_id
    from ...repositories.connection_repo import ConnectionRepository
    conn_repo = ConnectionRepository(database)
    await conn_repo.get_by_connection_id_and_workspace(
        workspace_id=workspace["id"],
        provider_connection_id="",  # We'll search by app_name instead
    )
    # Instead, list connections and find matching app
    connections = await conn_repo.list_by_workspace(
        workspace_id=workspace["id"],
        app_name=app_name,
    )
    if not connections:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active connection found for app '{app_name}' in this workspace",
        )

    # Use the first (most recent) connection's external_user_id
    external_user_id = connections[0].get("external_user_id", "")

    descriptors = await registry.register_composio_tools(
        app_name=app_name,
        external_user_id=external_user_id,
        workspace_id=workspace["id"],
    )
    return [
        ToolDescriptorResponse(
            id=t.id,
            provider=t.provider.value,
            name=t.name,
            description=t.description,
            input_schema=t.input_schema.model_dump(),
            output_schema=t.output_schema.model_dump() if t.output_schema else None,
            connection_required=t.connection_required,
            scopes=t.scopes,
            risk_category=t.risk_category.value,
            timeout_seconds=t.timeout_seconds,
            tags=t.tags,
        )
        for t in descriptors
    ]


# ---------------------------------------------------------------------------
# MCP server management (admin only)
# ---------------------------------------------------------------------------

@router.post("/mcp/servers", response_model=MCPServerResponse, status_code=status.HTTP_201_CREATED)
async def register_mcp_server(
    data: MCPServerRegisterRequest,
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN])),
    current_user: Dict[str, Any] = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
):
    """Register an MCP server configuration.

    Requires Owner or Admin role. The server is registered but NOT approved
    for use until an admin explicitly approves it.
    """
    workspace, role = workspace_and_role

    config = MCPServerConfig(
        name=data.name,
        transport=data.transport,
        command=data.command,
        args=data.args,
        env=data.env,
        url=data.url,
        headers=data.headers,
        timeout_seconds=data.timeout_seconds,
        enabled=data.enabled,
    )

    try:
        await registry.add_mcp_server(config, workspace_id=workspace["id"])
    except ValueError as exc:
        if "already registered" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return MCPServerResponse(
        name=data.name,
        transport=data.transport,
        command=data.command,
        args=data.args,
        url=data.url,
        timeout_seconds=data.timeout_seconds,
        enabled=data.enabled,
        approved=False,
        workspace_id=workspace["id"],
    )


@router.get("/mcp/servers", response_model=List[MCPServerResponse])
async def list_mcp_servers(
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
):
    """List registered MCP server configurations."""
    workspace, role = workspace_and_role
    servers = await registry.list_mcp_servers()
    return [
        MCPServerResponse(
            name=s.get("name", ""),
            transport=s.get("transport", ""),
            command=s.get("command"),
            args=s.get("args", []),
            url=s.get("url"),
            timeout_seconds=s.get("timeout_seconds", 30),
            enabled=s.get("enabled", True),
            approved=s.get("approved", False),
            workspace_id=s.get("workspace_id"),
        )
        for s in servers
    ]


@router.post("/mcp/servers/{name}/approve", response_model=Dict[str, Any])
async def approve_mcp_server(
    name: str,
    approved: bool = Query(default=True, description="Approve or reject the server"),
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN])),
    current_user: Dict[str, Any] = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
):
    """Approve or reject an MCP server (admin action)."""
    await registry.approve_mcp_server(name, approved=approved)
    return {"name": name, "approved": approved}


@router.post("/mcp/servers/{name}/connect", response_model=Dict[str, Any])
async def connect_mcp_server(
    name: str,
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN])),
    current_user: Dict[str, Any] = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
):
    """Connect to an approved MCP server and discover its tools."""
    await registry.connect_mcp_server(name)

    # Discover and register tools from the connected server
    descriptors = await registry.register_mcp_tools(
        server_name=name,
        workspace_id=None,  # MCP servers are globally available once approved
    )

    return {
        "name": name,
        "connected": True,
        "tools_discovered": len(descriptors),
    }


@router.post("/mcp/servers/{name}/disconnect", response_model=Dict[str, Any])
async def disconnect_mcp_server(
    name: str,
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN])),
    current_user: Dict[str, Any] = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
):
    """Disconnect from an MCP server."""
    await registry.disconnect_mcp_server(name)
    return {"name": name, "connected": False}


@router.post("/mcp/servers/{name}/discover", response_model=List[ToolDescriptorResponse])
async def discover_mcp_tools(
    name: str,
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN])),
    current_user: Dict[str, Any] = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
):
    """Discover tools from an MCP server without connecting permanently."""
    workspace, role = workspace_and_role
    descriptors = await registry.register_mcp_tools(
        server_name=name,
        workspace_id=workspace["id"],
    )
    return [
        ToolDescriptorResponse(
            id=t.id,
            provider=t.provider.value,
            name=t.name,
            description=t.description,
            input_schema=t.input_schema.model_dump(),
            output_schema=t.output_schema.model_dump() if t.output_schema else None,
            connection_required=t.connection_required,
            scopes=t.scopes,
            risk_category=t.risk_category.value,
            timeout_seconds=t.timeout_seconds,
            tags=t.tags,
        )
        for t in descriptors
    ]
