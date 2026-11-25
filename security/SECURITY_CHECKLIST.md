# NexusMCP Security Checklist — Phase 10

## Authentication & Authorization

- [ ] JWT access tokens are short-lived (≤30 minutes) and signed with a strong secret
- [ ] Refresh tokens are rotated and stored hashed (SHA-256) in MongoDB
- [ ] Every protected API requires `get_current_user` + `get_current_workspace` dependencies
- [ ] Workspace membership is verified before any data access (tenant isolation)
- [ ] Role-based access control (OWNER/ADMIN/MEMBER) enforced via `require_role()`
- [ ] Passwords hashed with bcrypt (cost factor ≥12)
- [ ] Auth endpoints rate-limited per IP (sliding window)
- [ ] Secure cookie flags: `HttpOnly`, `SameSite=strict`, `Secure` in production
- [ ] Logout invalidates refresh tokens

## Input Validation & API Security

- [ ] All request bodies validated with Pydantic models
- [ ] `extra="forbid"` on workflow models to reject unknown fields
- [ ] CORS allowlist configured (not wildcard `*` in production)
- [ ] CSRF protection for state-changing operations (cookie-based auth)
- [ ] Request payload size limits enforced (2MB webhook max)
- [ ] Correlation ID middleware for request tracing
- [ ] Global exception handler returns generic messages (no stack traces leaked)

## Webhook Security

- [ ] HMAC-SHA256 signature verification for GitHub, Slack, and generic webhooks
- [ ] Timestamp/replay window check (default 300 seconds)
- [ ] Deduplication via idempotency keys with TTL (default 7 days)
- [ ] Events durably persisted to MongoDB before acknowledging provider
- [ ] Inactive/paused workflows drop events without dispatch

## Secrets & Encryption

- [ ] JWT_SECRET_KEY must be changed from default in production (min 32 chars)
- [ ] No plaintext OAuth tokens stored (Composio manages token lifecycle)
- [ ] Sensitive fields encrypted at rest using Fernet (AES-128 + HMAC)
- [ ] `.env.example` provided with placeholder values only
- [ ] No secrets committed to version control
- [ ] Redaction applied to logs, errors, audit trails, broadcast messages

## MCP Security

- [ ] HTTP MCP server URLs validated against SSRF (private/internal IP ranges blocked)
- [ ] stdio commands restricted to allowlist (npx, node, python, python3, uvx, pipx)
- [ ] MCP server registration administrator-controlled
- [ ] Tool input validated against declared JSON Schema before invocation
- [ ] Tool output redacted for sensitive data

## Execution Reliability

- [ ] Workflow versions pinned immutably at execution start
- [ ] Idempotency keys prevent duplicate step side effects
- [ ] Large payloads externalized (>32KB threshold) with references passed via Temporal
- [ ] Activity timeouts, heartbeat, and bounded retries with exponential backoff
- [ ] Dead letter queue for failed events with controlled replay
- [ ] Worker restart recovery: unprocessed events resumed on startup

## Data Retention

- [ ] Configurable retention for execution logs and payloads
- [ ] Deduplication records TTL (default 7 days)
- [ ] Audit logs retention policy (configurable, default 90 days)
- [ ] DLQ events retention policy (configurable, default 30 days)
- [ ] Soft-delete / archive workflows instead of hard delete where applicable

## Dependency & Supply Chain Security

- [ ] Dependencies pinned to exact versions in `requirements.txt`
- [ ] Container images pinned to specific digests/tags
- [ ] Dependency vulnerability scanning integrated (e.g., `pip-audit` or `safety`)
- [ ] Container image scanning in CI pipeline

## Backup & Recovery

- [ ] MongoDB backup: `mongodump` with named volume persistence
- [ ] Temporal PostgreSQL backup: `pg_dump` with named volume persistence
- [ ] Restore procedure documented and tested
- [ ] RPO (Recovery Point Objective) and RTO (Recovery Time Objective) defined
- [ ] Backup and restore verified in clean environment

## Monitoring & Observability

- [ ] Structured logging with correlation IDs across all layers
- [ ] Health check endpoints for DB, Temporal, and external dependencies
- [ ] Alerting for repeated failures, expired connections, unavailable workers
- [ ] Metrics for execution success/failure rates, latency, retry counts
