# NexusMCP Threat Model — Phase 10

## 1. Scope

This threat model covers the NexusMCP platform:
- **Frontend:** React + Vite (control/monitoring UI only)
- **Backend:** Python + FastAPI (API/control plane)
- **AI design-time layer:** LangChain/LangGraph workflow generation
- **Trigger layer:** Webhooks, polling, schedules
- **Execution layer:** Temporal workflows + Python activity workers
- **Persistence:** MongoDB (application data), PostgreSQL (Temporal persistence)
- **Integrations:** Composio (OAuth/app connections), MCP (tool interop)

## 2. Threat Inventory & Mitigations

| Threat | Asset(s) Affected | Severity | Mitigation |
|--------|------------------|----------|------------|
| Prompt injection (direct/indirect) | AI-generated workflows, tool metadata | Critical | `sanitize_user_prompt()` + `sanitize_tool_text()` in `ai/safety.py`; structured output validation via Pydantic/JSON Schema; never execute model-generated code |
| Cross-tenant access | Workflows, executions, connections, trigger events | Critical | Workspace-scoped queries on every repository method; `get_current_workspace()` dependency enforces membership; `X-Workspace-ID` header required |
| Webhook spoofing | Webhook ingress endpoints | High | HMAC-SHA256 signature verification (GitHub, Slack, generic); timestamp/replay window; `WebhookVerificationError` rejected at 401 |
| Webhook replay | Trigger events, duplicate executions | High | Idempotency key dedup with TTL (default 7 days) via `DeduplicationService` |
| SSRF via MCP HTTP servers | MCP client, outbound network | High | `MCPClient._validate_http_url()` blocks private/internal IP ranges (10.x, 172.16-31.x, 192.168.x, 127.x, 169.254.x, fc00::/7, fe80::/10, ::1) |
| SSRF via MCP stdio commands | MCP client, local execution | Medium | `MCPClient.ALLOWED_STDIO_COMMANDS` allowlist (npx, node, python, python3, uvx, pipx) |
| Credential leakage | Logs, errors, audit trails | Critical | `redact_sensitive_data()` redacts tokens, passwords, keys; secrets never stored plaintext (Composio manages OAuth); no secrets in `.env` committed |
| Malicious tool outputs | Execution results, broadcast messages | High | `ExecutionEventBroadcaster.broadcast()` calls `redact_sensitive_data()`; tool output schema validation |
| Insecure direct object reference (IDOR) | Workflow/execution/connection endpoints | High | All repository queries filtered by `workspace_id`; authorization check before every data access |
| JWT theft / replay | Authentication sessions | High | Short-lived access tokens (30 min), refresh token rotation, secure cookies, `HttpOnly` |
| Rate limit abuse | Auth endpoints, trigger ingestion | Medium | `SlidingWindowRateLimiter` on auth endpoints (configurable per-minute); connection-level polling quotas |
| Payload size abuse | Webhook ingress, tool execution | High | 2MB webhook max payload; 32KB payload externalization threshold |
| Temporal outage | Background execution | Medium | Executions persisted to MongoDB before Temporal dispatch; worker restart recovery via `recover_unprocessed_events()` |
| MongoDB/Temporal data loss | Application state, execution history | High | Named Docker volumes; backup/restore runbook; RPO/RTO targets |

## 3. Security Boundaries

```
[Client/Browser] ──HTTPS──> [FastAPI API] ──JWT+WS──> [MongoDB]
                              │
                              ├── Webhook ingress ──verification──> [Trigger events]
                              │
                              └── Temporal client ──durable──> [PostgreSQL (Temporal)]
                                     │
                                     └── Activities ──tool invoke──> [Composio/MCP]
```

**Trust boundary:** Everything outside the FastAPI backend (client, providers, MCP servers, webhook sources) is untrusted.

## 4. Security Requirements Coverage

| rules.md Section | Implemented In |
|------------------|----------------|
| §5 Integration/MCP | `mcp_client.py` SSRF guards, stdio allowlist, Composio adapter isolation |
| §6 Always-on triggers | Webhook signature verification, dedup TTL, polling quotas, scheduler timezone safety |
| §7 Execution reliability | Idempotency keys, payload externalization, Temporal retry policies, DLQ |
| §8 Workflow lifecycle | Version pinning, immutable active versions, enable/disable audit |
| §9 Database/data ownership | `workspace_id` on all org records, workspace-scoped queries, indexes |
| §10 Security | Redaction, JWT, bcrypt, CORS, secure cookies, rate limiting |
| §11 API quality | Pydantic validation, correlation IDs, structured logging, consistent errors |
| §12 Frontend | No secrets in browser, workspace-scoped API calls |
| §13 Docker | Named volumes, health checks, restart policies, non-root consideration |
