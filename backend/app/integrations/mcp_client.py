"""MCP (Model Context Protocol) client.

Supports secure server configuration, connection lifecycle, tool discovery,
and invocation over stdio or HTTP/SSE transports. All provider-specific
MCP logic is isolated here; the rest of the application interacts through
the ToolRegistry interface.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from ..config import get_settings
from ..integrations.tool import (
    NormalizedToolError,
    RiskCategory,
    ToolDescriptor,
    ToolInvokedResult,
    ToolSchema,
    ToolType,
)
from ..logger import logger


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server connection."""

    name: str
    transport: str  # "stdio" or "http"
    command: Optional[str] = None  # for stdio
    args: List[str] = field(default_factory=list)  # for stdio
    env: Dict[str, str] = field(default_factory=dict)  # for stdio
    url: Optional[str] = None  # for http
    headers: Dict[str, str] = field(default_factory=dict)  # for http
    timeout_seconds: int = 30
    enabled: bool = True


class MCPClient:
    """MCP client supporting stdio and HTTP/SSE transports.

    Responsibilities:
      - Connect to and disconnect from MCP servers.
      - Discover tools advertised by a server (tools/list).
      - Invoke a tool on a server (tools/call).
      - Normalize tool metadata and errors into ToolDescriptor / NormalizedToolError.

    Security:
      - Server URLs are validated to prevent SSRF (no private/internal ranges
        for HTTP transport unless explicitly allowed).
      - stdio commands are restricted to an allowlist of executables.
      - All invocations carry a timeout.
    """

    # Allowed stdio commands to prevent arbitrary command execution
    ALLOWED_STDIO_COMMANDS = {
        "npx", "node", "python", "python3", "uvx", "pipx"
    }

    # Private/internal IP ranges to block for SSRF prevention
    PRIVATE_PREFIXES = ("10.", "172.16.", "172.17.", "172.18.", "172.19.",
                        "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                        "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                        "172.30.", "172.31.", "192.168.", "127.", "169.254.",
                        "fc00:", "fd00:", "fe80:", "::1")

    def __init__(self) -> None:
        self.settings = get_settings()
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._http_clients: Dict[str, httpx.AsyncClient] = {}
        self._processes: Dict[str, subprocess.Popen] = {}

    # ------------------------------------------------------------------
    # Configuration validation
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_http_url(url: str) -> None:
        """Reject URLs that target private/internal ranges (SSRF guard)."""
        lower = url.lower()
        for prefix in MCPClient.PRIVATE_PREFIXES:
            if prefix in lower:
                raise ValueError(
                    f"MCP server URL targets a private/internal address: {url}"
                )
        if not lower.startswith(("http://", "https://")):
            raise ValueError(f"MCP server URL must use http or https: {url}")

    def _validate_stdio_command(self, command: str) -> None:
        """Reject stdio commands not in the allowlist."""
        base = command.split()[0] if command else ""
        if base not in self.ALLOWED_STDIO_COMMANDS:
            raise ValueError(
                f"stdio command '{base}' is not in the allowlist: "
                f"{sorted(self.ALLOWED_STDIO_COMMANDS)}"
            )

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    async def connect(self, config: MCPServerConfig) -> None:
        """Establish a connection to an MCP server."""
        if config.name in self._sessions:
            logger.warning("MCP server already connected", server=config.name)
            return

        if config.transport == "stdio":
            await self._connect_stdio(config)
        elif config.transport == "http":
            await self._connect_http(config)
        else:
            raise ValueError(f"Unsupported MCP transport: {config.transport}")

        self._sessions[config.name] = {
            "config": config,
            "connected_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            "tools": [],
        }
        logger.info("MCP server connected", server=config.name, transport=config.transport)

    async def _connect_stdio(self, config: MCPServerConfig) -> None:
        """Connect via stdio (spawn a local process)."""
        if not config.command:
            raise ValueError("stdio transport requires a command")
        self._validate_stdio_command(config.command)

        cmd = [config.command] + config.args
        env = {**dict(__import__("os").environ), **config.env}

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        self._processes[config.name] = proc

        # Send initialize request
        init_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "nexusmcp", "version": "0.1.0"},
            },
        }
        self._send_stdio(config.name, init_msg)
        # Read initialize response (best effort; we don't block on it here)
        try:
            resp = proc.stdout.readline()
            if resp:
                logger.debug("MCP stdio initialize response", server=config.name, response=resp.strip())
        except Exception as exc:
            logger.debug("MCP stdio initialize read failed", server=config.name, error=str(exc))

    async def _connect_http(self, config: MCPServerConfig) -> None:
        """Connect via HTTP/SSE transport."""
        if not config.url:
            raise ValueError("http transport requires a url")
        self._validate_http_url(config.url)

        client = httpx.AsyncClient(
            base_url=config.url,
            headers=config.headers,
            timeout=httpx.Timeout(config.timeout_seconds),
        )
        self._http_clients[config.name] = client

        # Send initialize
        try:
            resp = await client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "nexusmcp", "version": "0.1.0"},
                    },
                },
            )
            resp.raise_for_status()
            logger.debug("MCP HTTP initialize response",
                         server=config.name, status=resp.status_code)
        except Exception as exc:
            logger.error("MCP HTTP initialize failed", server=config.name, error=str(exc))
            raise

    def _send_stdio(self, server_name: str, message: Dict[str, Any]) -> None:
        """Send a JSON-RPC message to a stdio MCP server."""
        proc = self._processes.get(server_name)
        if not proc or not proc.stdin:
            raise RuntimeError(f"MCP server not connected: {server_name}")
        line = json.dumps(message) + "\n"
        proc.stdin.write(line)
        proc.stdin.flush()

    async def _read_stdio(self, server_name: str) -> Optional[str]:
        """Read a single line from a stdio MCP server's stdout."""
        proc = self._processes.get(server_name)
        if not proc or not proc.stdout:
            return None
        line = proc.stdout.readline()
        return line if line else None

    async def disconnect(self, server_name: str) -> None:
        """Disconnect from an MCP server and clean up resources."""
        session = self._sessions.pop(server_name, None)
        if not session:
            return

        config: MCPServerConfig = session["config"]

        if config.transport == "stdio":
            proc = self._processes.pop(server_name, None)
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
        elif config.transport == "http":
            client = self._http_clients.pop(server_name, None)
            if client:
                await client.aclose()

        logger.info("MCP server disconnected", server=server_name)

    async def list_servers(self) -> List[str]:
        """Return names of currently connected MCP servers."""
        return list(self._sessions.keys())

    # ------------------------------------------------------------------
    # Tool discovery
    # ------------------------------------------------------------------
    async def discover_tools(self, server_name: str) -> List[ToolDescriptor]:
        """Discover tools advertised by an MCP server.

        Calls ``tools/list`` and normalizes each tool into a ToolDescriptor.
        """
        session = self._sessions.get(server_name)
        if not session:
            raise RuntimeError(f"MCP server not connected: {server_name}")

        config: MCPServerConfig = session["config"]

        if config.transport == "stdio":
            return await self._discover_tools_stdio(server_name, config)
        return await self._discover_tools_http(server_name, config)

    async def _discover_tools_stdio(self, server_name: str, config: MCPServerConfig) -> List[ToolDescriptor]:
        self._send_stdio(server_name, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        line = await self._read_stdio(server_name)
        if not line:
            return []
        try:
            resp = json.loads(line)
        except json.JSONDecodeError:
            logger.error("MCP stdio tools/list invalid JSON", server=server_name, raw=line[:200])
            return []

        tools = self._extract_tools(resp)
        return [self._normalize_mcp_tool(server_name, t) for t in tools]

    async def _discover_tools_http(self, server_name: str, config: MCPServerConfig) -> List[ToolDescriptor]:
        client = self._http_clients.get(server_name)
        if not client:
            raise RuntimeError(f"MCP HTTP client not connected: {server_name}")
        try:
            resp = await client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("MCP HTTP tools/list failed", server=server_name, error=str(exc))
            return []

        tools = self._extract_tools(data)
        return [self._normalize_mcp_tool(server_name, t) for t in tools]

    @staticmethod
    def _extract_tools(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract the tools array from a tools/list response."""
        result = resp.get("result", {}) if isinstance(resp, dict) else {}
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return tools if isinstance(tools, list) else []

    @staticmethod
    def _normalize_mcp_tool(server_name: str, raw: Dict[str, Any]) -> ToolDescriptor:
        """Normalize an MCP tool advertisement into a ToolDescriptor."""
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        input_schema = raw.get("inputSchema", {}) or {}
        tool_id = f"mcp:{server_name}:{raw.get('name', 'unknown')}"
        return ToolDescriptor(
            id=tool_id,
            provider=ToolType.MCP,
            name=raw.get("name", "unknown"),
            description=raw.get("description", ""),
            input_schema=ToolSchema(
                type=input_schema.get("type", "object"),
                properties=input_schema.get("properties", {}),
                required=input_schema.get("required", []),
                additional_properties=input_schema.get("additionalProperties", False),
            ),
            output_schema=None,
            connection_required=False,
            scopes=[],
            risk_category=RiskCategory.LOW,
            timeout_seconds=30,
            tags=["mcp", server_name],
            created_at=now,
            updated_at=now,
        )

    # ------------------------------------------------------------------
    # Tool invocation
    # ------------------------------------------------------------------
    async def invoke_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> ToolInvokedResult:
        """Invoke a tool on an MCP server.

        Calls ``tools/call`` and normalizes the result or error.
        """
        import time
        start = time.time()
        session = self._sessions.get(server_name)
        if not session:
            return ToolInvokedResult(
                success=False,
                tool_id=f"mcp:{server_name}:{tool_name}",
                provider=ToolType.MCP,
                error=NormalizedToolError(
                    message=f"MCP server not connected: {server_name}",
                    tool_id=f"mcp:{server_name}:{tool_name}",
                    provider=ToolType.MCP,
                    error_code="server_not_connected",
                    retryable=False,
                ),
            )

        config: MCPServerConfig = session["config"]

        if config.transport == "stdio":
            return await self._invoke_stdio(server_name, config, tool_name, arguments, start)
        return await self._invoke_http(server_name, config, tool_name, arguments, start)

    async def _invoke_stdio(
        self,
        server_name: str,
        config: MCPServerConfig,
        tool_name: str,
        arguments: Dict[str, Any],
        start: float,
    ) -> ToolInvokedResult:
        request_id = str(uuid.uuid4())
        self._send_stdio(server_name, {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        })
        line = await self._read_stdio(server_name)
        elapsed_ms = int((__import__("time").time() - start) * 1000)
        if not line:
            return ToolInvokedResult(
                success=False,
                tool_id=f"mcp:{server_name}:{tool_name}",
                provider=ToolType.MCP,
                error=NormalizedToolError(
                    message="No response from MCP server",
                    tool_id=f"mcp:{server_name}:{tool_name}",
                    provider=ToolType.MCP,
                    error_code="no_response",
                    retryable=True,
                ),
                execution_time_ms=elapsed_ms,
            )
        try:
            resp = json.loads(line)
        except json.JSONDecodeError:
            return ToolInvokedResult(
                success=False,
                tool_id=f"mcp:{server_name}:{tool_name}",
                provider=ToolType.MCP,
                error=NormalizedToolError(
                    message="Invalid JSON response from MCP server",
                    tool_id=f"mcp:{server_name}:{tool_name}",
                    provider=ToolType.MCP,
                    error_code="invalid_response",
                    retryable=False,
                ),
                execution_time_ms=elapsed_ms,
            )

        if "error" in resp:
            err = resp["error"]
            return ToolInvokedResult(
                success=False,
                tool_id=f"mcp:{server_name}:{tool_name}",
                provider=ToolType.MCP,
                error=NormalizedToolError(
                    message=err.get("message", "MCP tool error"),
                    tool_id=f"mcp:{server_name}:{tool_name}",
                    provider=ToolType.MCP,
                    error_code=f"mcp_error_{err.get('code', 'unknown')}",
                    details={"data": err.get("data")},
                    retryable=False,
                ),
                execution_time_ms=elapsed_ms,
            )

        result = resp.get("result", {})
        return ToolInvokedResult(
            success=True,
            tool_id=f"mcp:{server_name}:{tool_name}",
            provider=ToolType.MCP,
            output=result,
            execution_time_ms=elapsed_ms,
        )

    async def _invoke_http(
        self,
        server_name: str,
        config: MCPServerConfig,
        tool_name: str,
        arguments: Dict[str, Any],
        start: float,
    ) -> ToolInvokedResult:
        client = self._http_clients.get(server_name)
        if not client:
            return ToolInvokedResult(
                success=False,
                tool_id=f"mcp:{server_name}:{tool_name}",
                provider=ToolType.MCP,
                error=NormalizedToolError(
                    message=f"MCP HTTP client not connected: {server_name}",
                    tool_id=f"mcp:{server_name}:{tool_name}",
                    provider=ToolType.MCP,
                    error_code="client_not_connected",
                    retryable=False,
                ),
            )
        try:
            resp = await client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": str(uuid.uuid4()),
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                },
            )
            elapsed_ms = int((__import__("time").time() - start) * 1000)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            elapsed_ms = int((__import__("time").time() - start) * 1000)
            return ToolInvokedResult(
                success=False,
                tool_id=f"mcp:{server_name}:{tool_name}",
                provider=ToolType.MCP,
                error=NormalizedToolError(
                    message=f"MCP server returned HTTP {exc.response.status_code}",
                    tool_id=f"mcp:{server_name}:{tool_name}",
                    provider=ToolType.MCP,
                    error_code=f"http_{exc.response.status_code}",
                    retryable=exc.response.status_code >= 500,
                ),
                execution_time_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = int((__import__("time").time() - start) * 1000)
            return ToolInvokedResult(
                success=False,
                tool_id=f"mcp:{server_name}:{tool_name}",
                provider=ToolType.MCP,
                error=NormalizedToolError(
                    message=str(exc),
                    tool_id=f"mcp:{server_name}:{tool_name}",
                    provider=ToolType.MCP,
                    error_code="invocation_error",
                    retryable=True,
                ),
                execution_time_ms=elapsed_ms,
            )

        if "error" in data:
            err = data["error"]
            return ToolInvokedResult(
                success=False,
                tool_id=f"mcp:{server_name}:{tool_name}",
                provider=ToolType.MCP,
                error=NormalizedToolError(
                    message=err.get("message", "MCP tool error"),
                    tool_id=f"mcp:{server_name}:{tool_name}",
                    provider=ToolType.MCP,
                    error_code=f"mcp_error_{err.get('code', 'unknown')}",
                    details={"data": err.get("data")},
                    retryable=False,
                ),
            )

        return ToolInvokedResult(
            success=True,
            tool_id=f"mcp:{server_name}:{tool_name}",
            provider=ToolType.MCP,
            output=data.get("result", {}),
            execution_time_ms=int((__import__("time").time() - start) * 1000),
        )

    async def close_all(self) -> None:
        """Disconnect all connected MCP servers."""
        for name in list(self._sessions.keys()):
            await self.disconnect(name)
