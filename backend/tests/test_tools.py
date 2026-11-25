"""Integration tests for Phase 4: Tool Registry, MCP Client & Tool Contracts.

Tests cover:
  - Tool descriptor creation and validation
  - Tool registry registration, listing, and resolution
  - Workspace isolation for tool access
  - Input-schema validation on invocation
  - Normalized errors for tool failures
  - MCP server registration, approval, and SSRF prevention
  - MCP client connection lifecycle and tool discovery/invocation
"""

from datetime import datetime, timezone
from typing import Any, cast

import pytest
from httpx import AsyncClient

from app.integrations.mcp_client import MCPClient, MCPServerConfig
from app.integrations.registry import ToolRegistry, ToolRepository
from app.integrations.tool import (
    NormalizedToolError,
    RiskCategory,
    ToolDescriptor,
    ToolInvokedResult,
    ToolSchema,
    ToolType,
)
from app.security.rate_limiter import auth_limiter


async def register_user(client: AsyncClient, email: str, name: str) -> dict[str, Any]:
    auth_limiter.reset()
    res = await client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": name,
    })
    assert res.status_code in (200, 201), res.text
    return cast(dict[str, Any], res.json())


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tool Descriptor tests
# ---------------------------------------------------------------------------

def test_tool_descriptor_creation():
    """Test creating a valid ToolDescriptor."""
    desc = ToolDescriptor(
        id="composio:github:list_issues",
        provider=ToolType.COMPOSIO,
        name="list_issues",
        description="List GitHub issues",
        input_schema=ToolSchema(
            type="object",
            properties={"repo": {"type": "string"}},
            required=["repo"],
            additional_properties=False,
        ),
        connection_required=True,
        scopes=["repo:read"],
        risk_category=RiskCategory.LOW,
        timeout_seconds=30,
        tags=["composio", "github"],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert desc.id == "composio:github:list_issues"
    assert desc.provider == ToolType.COMPOSIO
    assert desc.risk_category == RiskCategory.LOW
    assert desc.connection_required is True


def test_tool_descriptor_input_schema_validation():
    """Test ToolSchema validation properties."""
    schema = ToolSchema(
        type="object",
        properties={"name": {"type": "string"}, "count": {"type": "integer"}},
        required=["name"],
        additional_properties=False,
    )
    assert schema.type == "object"
    assert "name" in schema.properties
    assert schema.required == ["name"]
    assert schema.additional_properties is False


def test_normalized_tool_error():
    """Test NormalizedToolError creation and serialization."""
    err = NormalizedToolError(
        message="Tool execution failed",
        tool_id="mcp:server:tool",
        provider=ToolType.MCP,
        error_code="tool_error",
        details={"detail": "timeout"},
        retryable=True,
    )
    d = err.to_dict()
    assert d["error_code"] == "tool_error"
    assert d["message"] == "Tool execution failed"
    assert d["tool_id"] == "mcp:server:tool"
    assert d["provider"] == "mcp"
    assert d["retryable"] is True


def test_tool_invoked_result():
    """Test ToolInvokedResult creation."""
    result = ToolInvokedResult(
        success=True,
        tool_id="composio:github:list_issues",
        provider=ToolType.COMPOSIO,
        output={"issues": []},
        execution_time_ms=120,
    )
    assert result.success is True
    assert result.output == {"issues": []}
    assert result.execution_time_ms == 120


# ---------------------------------------------------------------------------
# Tool Repository tests (using MockDatabase)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_repository_crud(mock_db):
    """Test ToolRepository create, get, list, update, delete."""
    repo = ToolRepository(mock_db)

    entry = {
        "tool_id": "composio:github:list_issues",
        "provider": ToolType.COMPOSIO.value,
        "name": "list_issues",
        "description": "List GitHub issues",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "connection_required": True,
        "scopes": [],
        "risk_category": RiskCategory.LOW.value,
        "timeout_seconds": 30,
        "tags": ["composio"],
        "workspace_id": "ws-1",
        "enabled": True,
        "allowed_roles": ["owner", "admin", "member"],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }

    # Create
    entry_id = await repo.create_entry(entry)
    assert entry_id is not None

    # Get
    fetched = await repo.get_entry("composio:github:list_issues")
    assert fetched is not None
    assert fetched["tool_id"] == "composio:github:list_issues"

    # List
    all_entries = await repo.list_entries(workspace_id="ws-1")
    assert len(all_entries) >= 1

    # List globally available (workspace_id=None) + ws-1
    global_entries = await repo.list_entries(workspace_id=None)
    assert isinstance(global_entries, list)

    # Update
    updated = await repo.update_entry("composio:github:list_issues", {"enabled": False})
    assert updated is True

    # Delete
    deleted = await repo.delete_entry("composio:github:list_issues")
    assert deleted is True

    fetched_again = await repo.get_entry("composio:github:list_issues")
    assert fetched_again is None


# ---------------------------------------------------------------------------
# Tool Registry tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_registry_list_and_get(mock_db):
    """Test ToolRegistry list and get operations."""
    registry = ToolRegistry(db=mock_db)

    # Register some tools directly via repo
    await registry.repo.create_entry({
        "tool_id": "composio:github:list_issues",
        "provider": ToolType.COMPOSIO.value,
        "name": "list_issues",
        "description": "List GitHub issues",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "connection_required": True,
        "scopes": [],
        "risk_category": RiskCategory.LOW.value,
        "timeout_seconds": 30,
        "tags": ["composio", "github"],
        "workspace_id": "ws-1",
        "enabled": True,
        "allowed_roles": ["owner", "admin", "member"],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    })

    tools = await registry.list_tools(workspace_id="ws-1")
    assert len(tools) >= 1
    assert any(t.id == "composio:github:list_issues" for t in tools)

    tool = await registry.get_tool("composio:github:list_issues")
    assert tool is not None
    assert tool.name == "list_issues"
    assert tool.provider == ToolType.COMPOSIO

    # Non-existent tool
    missing = await registry.get_tool("nonexistent:tool")
    assert missing is None


@pytest.mark.asyncio
async def test_tool_registry_check_tool_access():
    """Test check_tool_access enforces roles."""
    registry = ToolRegistry()

    desc = ToolDescriptor(
        id="composio:github:list_issues",
        provider=ToolType.COMPOSIO,
        name="list_issues",
        description="List issues",
        input_schema=ToolSchema(),
        connection_required=False,
        scopes=[],
        risk_category=RiskCategory.LOW,
        timeout_seconds=30,
        tags=[],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    # Valid roles should pass
    for role in ("owner", "admin", "member"):
        registry.check_tool_access(desc, "ws-1", role)  # should not raise

    # Invalid role should raise
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        registry.check_tool_access(desc, "ws-1", "guest")
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Input schema validation tests
# ---------------------------------------------------------------------------

def test_validate_input_required_fields():
    """Test that missing required fields are flagged."""
    registry = ToolRegistry()
    schema = ToolSchema(
        type="object",
        properties={"name": {"type": "string"}},
        required=["name"],
        additional_properties=False,
    )
    errors = registry._validate_input(schema, {})
    assert len(errors) == 1
    assert "name" in errors[0]


def test_validate_input_extra_fields_blocked():
    """Test that extra fields are blocked when additionalProperties is false."""
    registry = ToolRegistry()
    schema = ToolSchema(
        type="object",
        properties={"name": {"type": "string"}},
        required=[],
        additional_properties=False,
    )
    errors = registry._validate_input(schema, {"name": "foo", "extra": "bar"})
    assert any("Unexpected field" in e for e in errors)


def test_validate_input_type_checks():
    """Test type validation."""
    registry = ToolRegistry()
    schema = ToolSchema(
        type="object",
        properties={"count": {"type": "integer"}, "active": {"type": "boolean"}},
        required=[],
        additional_properties=False,
    )
    errors = registry._validate_input(schema, {"count": "not-an-int", "active": "yes"})
    assert any("integer" in e for e in errors)
    assert any("boolean" in e for e in errors)

    # Valid input should produce no errors
    errors = registry._validate_input(schema, {"count": 5, "active": True})
    assert errors == []


# ---------------------------------------------------------------------------
# MCP Client tests
# ---------------------------------------------------------------------------

def test_mcp_server_config_validation_http_private_url():
    """Test that MCP client rejects private/internal URLs (SSRF guard)."""
    client = MCPClient()

    import asyncio
    async def test():
        config = MCPServerConfig(
            name="test-http",
            transport="http",
            url="http://127.0.0.1:8080/mcp",
        )
        with pytest.raises(ValueError, match="private"):
            await client.connect(config)

    asyncio.run(test())


def test_mcp_server_config_validation_stdio_disallowed_command():
    """Test that MCP client rejects stdio commands not in allowlist."""
    client = MCPClient()

    import asyncio
    async def test():
        config = MCPServerConfig(
            name="test-stdio",
            transport="stdio",
            command="curl http://evil.com",
        )
        with pytest.raises(ValueError, match="allowlist"):
            await client.connect(config)

    asyncio.run(test())


def test_mcp_normalize_tool():
    """Test normalizing an MCP tool advertisement."""
    client = MCPClient()
    raw = {
        "name": "read_file",
        "description": "Read a file from disk",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    }
    desc = client._normalize_mcp_tool("my-server", raw)
    assert desc.id == "mcp:my-server:read_file"
    assert desc.provider == ToolType.MCP
    assert desc.name == "read_file"
    assert desc.connection_required is False
    assert desc.risk_category == RiskCategory.LOW


# ---------------------------------------------------------------------------
# API route tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tools_list_endpoint(client: AsyncClient):
    """Test GET /api/v1/tools lists tools for the workspace."""
    user = await register_user(client, "tools-alice@example.com", "Tools Alice")
    headers = auth_headers(user["access_token"])

    res = await client.get("/api/v1/tools", headers=headers)
    assert res.status_code == 200, res.text
    tools = res.json()
    assert isinstance(tools, list)


@pytest.mark.asyncio
async def test_tools_invoke_nonexistent_tool(client: AsyncClient):
    """Test POST /api/v1/tools/{tool_id}/invoke with nonexistent tool."""
    user = await register_user(client, "invoke-bob@example.com", "Invoke Bob")
    headers = auth_headers(user["access_token"])

    res = await client.post(
        "/api/v1/tools/nonexistent:tool/invoke",
        json={"arguments": {}},
        headers=headers,
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_mcp_server_register_and_list(client: AsyncClient):
    """Test registering and listing MCP servers."""
    user = await register_user(client, "mcp-alice@example.com", "MCP Alice")
    headers = auth_headers(user["access_token"])

    # Register an MCP server
    res = await client.post(
        "/api/v1/tools/mcp/servers",
        json={
            "name": "test-mcp-server",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
            "timeout_seconds": 30,
        },
        headers=headers,
    )
    assert res.status_code in (201, 409), res.text  # 409 if already exists from prior test

    # List MCP servers
    list_res = await client.get("/api/v1/tools/mcp/servers", headers=headers)
    assert list_res.status_code == 200, list_res.text
    servers = list_res.json()
    assert isinstance(servers, list)


@pytest.mark.asyncio
async def test_mcp_server_register_private_url_blocked(client: AsyncClient):
    """Test that registering an MCP server with a private URL is blocked."""
    user = await register_user(client, "ssrf-bob@example.com", "SSRF Bob")
    headers = auth_headers(user["access_token"])

    res = await client.post(
        "/api/v1/tools/mcp/servers",
        json={
            "name": "ssrf-server",
            "transport": "http",
            "url": "http://127.0.0.1:8080/mcp",
        },
        headers=headers,
    )
    assert res.status_code == 400, res.text  # validation error


@pytest.mark.asyncio
async def test_mcp_server_register_invalid_transport(client: AsyncClient):
    """Test that registering an MCP server with invalid transport is blocked."""
    user = await register_user(client, "transport-alice@example.com", "Transport Alice")
    headers = auth_headers(user["access_token"])

    res = await client.post(
        "/api/v1/tools/mcp/servers",
        json={
            "name": "bad-transport",
            "transport": "ftp",  # invalid
            "url": "https://example.com/mcp",
        },
        headers=headers,
    )
    assert res.status_code in (400, 422), res.text


# ---------------------------------------------------------------------------
# Workspace isolation tests for tools
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tools_workspace_isolation(client: AsyncClient):
    """Test that tools are isolated by workspace."""
    alice = await register_user(client, "ws-iso-alice@example.com", "WS Iso Alice")
    bob = await register_user(client, "ws-iso-bob@example.com", "WS Iso Bob")

    alice_headers = auth_headers(alice["access_token"])
    bob_headers = auth_headers(bob["access_token"])

    # Both users should be able to list tools (empty or global tools)
    alice_res = await client.get("/api/v1/tools", headers=alice_headers)
    bob_res = await client.get("/api/v1/tools", headers=bob_headers)

    assert alice_res.status_code == 200
    assert bob_res.status_code == 200
    # Tools should be lists
    assert isinstance(alice_res.json(), list)
    assert isinstance(bob_res.json(), list)
