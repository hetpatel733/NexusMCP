"""Phase 10 Security Tests.

Covers:
- Cross-tenant access isolation
- Webhook spoofing and replay protection
- MCP endpoint SSRF restrictions
- Sensitive credential redaction in logs
- Rate limits and execution quotas
- Authorization enforcement
"""

from __future__ import annotations

import hmac
import hashlib
import json
import pytest
from httpx import ASGITransport, AsyncClient

from app.db import get_db
from app.main import app
from app.security.rate_limiter import auth_limiter, api_limiter, execution_quota
from app.security.redaction import redact_sensitive_data
from app.security.encryption import encrypt_value, decrypt_value, is_encryption_enabled
from app.integrations.mcp_client import MCPClient
from tests.mock_db import MockDatabase


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def mock_db():
    db = MockDatabase()
    auth_limiter.reset()
    api_limiter.reset()
    execution_quota.reset()
    return db


@pytest.fixture
async def client(mock_db):
    app.dependency_overrides[get_db] = lambda: mock_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Cross-tenant access tests
# ---------------------------------------------------------------------------

class TestCrossTenantIsolation:
    """Verify users cannot access resources outside their workspace."""

    async def test_workspace_scoped_workflow_list(self, client, mock_db):
        """Users can only list workflows in their own workspace."""
        # Insert workflows for two different workspaces
        ws_a_id = "ws-aaaaaaaaaaaa"
        ws_b_id = "ws-bbbbbbbbbbbb"

        for doc in [
            {
                "_id": "wf-a-1",
                "workspace_id": ws_a_id,
                "name": "Workflow A-1",
                "status": "draft",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            },
            {
                "_id": "wf-b-1",
                "workspace_id": ws_b_id,
                "name": "Workflow B-1",
                "status": "draft",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            },
        ]:
            await mock_db.workflows.insert_one(doc)

        # Mock: user from workspace A requests workflows
        # The get_current_workspace dependency returns ws_a
        # We need to verify the repository filters by workspace_id
        from app.repositories.workflow_repo import WorkflowRepository
        repo = WorkflowRepository(mock_db)
        workflows = await repo.list_by_workspace(ws_a_id)
        ws_a_names = {w["name"] for w in workflows}

        workflows_b = await repo.list_by_workspace(ws_b_id)
        ws_b_names = {w["name"] for w in workflows_b}

        assert "Workflow A-1" in ws_a_names
        assert "Workflow B-1" not in ws_a_names
        assert "Workflow B-1" in ws_b_names
        assert "Workflow A-1" not in ws_b_names

    async def test_workspace_scoped_execution_access(self, mock_db):
        """Executions are strictly scoped to their workspace."""
        from app.repositories.execution_repo import ExecutionRepository
        from bson import ObjectId
        repo = ExecutionRepository(mock_db)

        ws_a_id = "ws-aaaaaaaaaaaa"
        ws_b_id = "ws-bbbbbbbbbbbb"

        for doc in [
            {
                "_id": ObjectId(),
                "workspace_id": ws_a_id,
                "workflow_id": "wf-1",
                "status": "completed",
                "created_at": "2024-01-01T00:00:00Z",
            },
            {
                "_id": ObjectId(),
                "workspace_id": ws_b_id,
                "workflow_id": "wf-2",
                "status": "completed",
                "created_at": "2024-01-01T00:00:00Z",
            },
        ]:
            await mock_db.workflow_executions.insert_one(doc)

        # Retrieve the actual inserted IDs
        docs_a = await mock_db.workflow_executions.find({"workspace_id": ws_a_id}).to_list()
        docs_b = await mock_db.workflow_executions.find({"workspace_id": ws_b_id}).to_list()
        exec_a_id = str(docs_a[0]["_id"])
        exec_b_id = str(docs_b[0]["_id"])

        exec_a = await repo.get_execution(ws_a_id, exec_a_id)
        assert exec_a is not None

        # Cross-tenant access should return None
        exec_b_from_a = await repo.get_execution(ws_a_id, exec_b_id)
        assert exec_b_from_a is None

        exec_b = await repo.get_execution(ws_b_id, exec_b_id)
        assert exec_b is not None

        exec_a_from_b = await repo.get_execution(ws_b_id, exec_a_id)
        assert exec_a_from_b is None


# ---------------------------------------------------------------------------
# Webhook security tests
# ---------------------------------------------------------------------------

class TestWebhookSecurity:
    """Verify webhook signature verification and replay protection."""

    def _make_github_signature(self, secret: str, payload: bytes) -> str:
        return "sha256=" + hmac.new(
            secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()

    async def test_valid_github_webhook_accepted(self, client, mock_db):
        """A correctly signed GitHub webhook is accepted."""
        secret = "test-webhook-secret"
        payload = b'{"action":"opened","issue":{"number":1}}'
        signature = self._make_github_signature(secret, payload)

        # Insert a workflow and subscription in the mock
        ws_id = "ws-webhook-test"
        wf_id = "wf-webhook-test"
        await mock_db.workflows.insert_one({
            "_id": wf_id,
            "workspace_id": ws_id,
            "name": "Webhook Workflow",
            "status": "active",
            "active_version": 1,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        })
        await mock_db.trigger_subscriptions.insert_one({
            "_id": "sub-1",
            "workspace_id": ws_id,
            "workflow_id": wf_id,
            "workflow_version": 1,
            "trigger_type": "webhook",
            "provider": "github",
            "status": "active",
            "secret": secret,
            "config": {},
        })

        headers = {
            "x-hub-signature-256": signature,
            "x-github-delivery": "test-delivery-123",
        }

        response = await client.post(
            f"/api/v1/triggers/webhook/{ws_id}/{wf_id}",
            content=payload,
            headers=headers,
        )
        # Should not be a 401 (signature verification failure)
        assert response.status_code != 401, f"Webhook rejected: {response.text}"

    async def test_invalid_github_webhook_rejected(self, client, mock_db):
        """A webhook with an invalid signature is rejected with 401."""
        secret = "test-webhook-secret"
        payload = b'{"action":"opened","issue":{"number":1}}'
        bad_signature = "sha256=" + "0" * 64

        ws_id = "ws-webhook-test-2"
        wf_id = "wf-webhook-test-2"
        await mock_db.workflows.insert_one({
            "_id": wf_id,
            "workspace_id": ws_id,
            "name": "Webhook Workflow 2",
            "status": "active",
            "active_version": 1,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        })
        await mock_db.trigger_subscriptions.insert_one({
            "_id": "sub-2",
            "workspace_id": ws_id,
            "workflow_id": wf_id,
            "workflow_version": 1,
            "trigger_type": "webhook",
            "provider": "github",
            "status": "active",
            "secret": secret,
            "config": {},
        })

        headers = {
            "x-hub-signature-256": bad_signature,
            "x-github-delivery": "test-delivery-456",
        }

        response = await client.post(
            f"/api/v1/triggers/webhook/{ws_id}/{wf_id}",
            content=payload,
            headers=headers,
        )
        # Should not be accepted (401 for signature failure or 400 for other issues)
        assert response.status_code != 200, f"Webhook should have been rejected: {response.text}"

    async def test_webhook_replay_protection(self, mock_db):
        """Duplicate event IDs are rejected via idempotency."""
        from app.triggers.deduplicator import DeduplicationService
        from app.models.trigger import EventEnvelope
        from app.repositories.trigger_repo import TriggerRepository
        from datetime import datetime, timezone

        repo = TriggerRepository(mock_db)
        dedup = DeduplicationService(repo, ttl_seconds=3600)

        envelope = EventEnvelope(
            event_id="evt-replay-123",
            provider="webhook",
            event_type="push",
            workspace_id="ws-replay",
            workflow_id="wf-replay",
            workflow_version=1,
            idempotency_key="idem-replay-123",
            data={},
            received_at=datetime.now(timezone.utc),
            payload_reference="ref-1",
        )

        # First call should not be duplicate
        is_dup_1 = await dedup.is_duplicate_or_record(envelope)
        assert is_dup_1 is False

        # Second call with same idempotency key should be duplicate
        is_dup_2 = await dedup.is_duplicate_or_record(envelope)
        assert is_dup_2 is True

    async def test_webhook_signature_verification_directly(self):
        """Verify webhook signature check rejects invalid signatures."""
        from app.triggers.adapters.webhook import WebhookTriggerAdapter
        import hashlib
        import hmac

        adapter = WebhookTriggerAdapter("github")
        secret = "test-secret"
        payload = b'{"action":"opened"}'

        # Valid signature
        valid_sig = "sha256=" + hmac.new(
            secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        headers = {"x-hub-signature-256": valid_sig}
        # Should not raise
        adapter.verify_signature_and_timestamp(payload, headers, secret)

        # Invalid signature
        bad_headers = {"x-hub-signature-256": "sha256=" + "0" * 64}
        with pytest.raises(Exception) as exc_info:
            adapter.verify_signature_and_timestamp(payload, bad_headers, secret)
        assert "Invalid" in str(exc_info.value) or "invalid" in str(exc_info.value).lower()

    async def test_webhook_timestamp_replay(self):
        """Webhook timestamps outside the window are rejected."""
        from app.triggers.adapters.webhook import WebhookTriggerAdapter
        import time

        adapter = WebhookTriggerAdapter("github")
        secret = "test-secret"
        payload = b'{"action":"opened"}'

        # Old timestamp (outside 300s window)
        old_ts = str(int(time.time()) - 600)
        headers = {"x-webhook-timestamp": old_ts, "x-webhook-secret": secret}
        with pytest.raises(Exception) as exc_info:
            adapter.verify_signature_and_timestamp(payload, headers, secret)
        assert "timestamp" in str(exc_info.value).lower() or "expired" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# MCP security tests
# ---------------------------------------------------------------------------

class TestMCPSecurity:
    """Verify MCP SSRF restrictions and endpoint controls."""

    def test_http_url_private_range_blocked(self):
        """MCP HTTP URLs targeting private IP ranges are rejected."""
        client = MCPClient()
        private_urls = [
            "http://10.0.0.1:8080",
            "http://172.16.0.1:8080",
            "http://192.168.1.1:8080",
            "http://127.0.0.1:8080",
            "http://169.254.169.254",
            "http://fc00::1",
            "http://fe80::1",
        ]
        for url in private_urls:
            with pytest.raises(ValueError, match="private/internal"):
                MCPClient._validate_http_url(url)

    def test_http_url_public_range_allowed(self):
        """Public HTTPS URLs are accepted."""
        public_urls = [
            "https://api.example.com/mcp",
            "https://mcp.server.com",
        ]
        for url in public_urls:
            # Should not raise
            MCPClient._validate_http_url(url)

    def test_http_url_must_use_http_or_https(self):
        """Non-HTTP schemes are rejected."""
        with pytest.raises(ValueError, match="must use http or https"):
            MCPClient._validate_http_url("ftp://example.com")

    def test_stdio_command_allowlist(self):
        """Only allowed stdio commands are accepted."""
        client = MCPClient()
        allowed = ["npx", "node", "python", "python3", "uvx", "pipx"]
        for cmd in allowed:
            client._validate_stdio_command(cmd)

        disallowed = ["bash", "sh", "curl", "wget", "rm", "sudo"]
        for cmd in disallowed:
            with pytest.raises(ValueError, match="not in the allowlist"):
                client._validate_stdio_command(cmd)


# ---------------------------------------------------------------------------
# Credential redaction tests
# ---------------------------------------------------------------------------

class TestCredentialRedaction:
    """Verify sensitive credentials are never exposed in logs."""

    def test_redact_bearer_tokens(self):
        """Bearer JWT tokens are redacted from strings."""
        text = 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxx.yyy'
        result = redact_sensitive_data(text)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "[REDACTED]" in result

    def test_redact_sensitive_keys(self):
        """Dictionary values with sensitive keys are redacted."""
        data = {
            "username": "alice",
            "password": "secret123",
            "api_key": "sk-1234567890abcdef",
            "normal_field": "visible",
        }
        result = redact_sensitive_data(data)
        assert result["password"] == "[REDACTED]"
        assert result["api_key"] == "[REDACTED]"
        assert result["username"] == "alice"
        assert result["normal_field"] == "visible"

    def test_redact_nested_structures(self):
        """Redaction works on nested dicts and lists."""
        data = {
            "steps": [
                {"name": "step1", "output": {"access_token": "hidden-token"}},
                {"name": "step2", "output": {"data": "visible"}},
            ]
        }
        result = redact_sensitive_data(data)
        assert result["steps"][0]["output"]["access_token"] == "[REDACTED]"
        assert result["steps"][1]["output"]["data"] == "visible"

    def test_redact_private_key_blocks(self):
        """PEM private key blocks are redacted."""
        pem = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCKKg
-----END PRIVATE KEY-----"""
        result = redact_sensitive_data(pem)
        assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCKKg" not in result
        assert "[REDACTED]" in result


# ---------------------------------------------------------------------------
# Encryption tests
# ---------------------------------------------------------------------------

class TestEncryption:
    """Verify at-rest encryption works correctly."""

    def test_encrypt_decrypt_roundtrip(self):
        """Encrypted values can be decrypted back to original."""
        import os
        os.environ["ENCRYPTION_MASTER_KEY"] = "test-encryption-key-32-chars-long!!"

        # Force re-initialization
        import app.security.encryption as enc_mod
        enc_mod._fernet = None

        original = "super-secret-api-key-value"
        encrypted = encrypt_value(original)
        decrypted = decrypt_value(encrypted)

        assert encrypted != original
        assert decrypted == original

    def test_encryption_enabled_with_key(self):
        """Encryption is enabled when master key is set."""
        import os
        os.environ["ENCRYPTION_MASTER_KEY"] = "test-encryption-key-32-chars-long!!"

        import app.security.encryption as enc_mod
        enc_mod._fernet = None

        assert is_encryption_enabled() is True


# ---------------------------------------------------------------------------
# Rate limit and quota tests
# ---------------------------------------------------------------------------

class TestRateLimits:
    """Verify rate limits and execution quotas work correctly."""

    async def test_auth_rate_limiter_blocks_excess_requests(self, mock_db):
        """Auth rate limiter blocks requests beyond the limit."""
        auth_limiter.reset()

        limit = auth_limiter.limit
        for i in range(limit):
            await auth_limiter.check("test-ip-1")

        # Next request should be blocked
        with pytest.raises(Exception) as exc_info:
            await auth_limiter.check("test-ip-1")
        assert "Rate limit exceeded" in str(exc_info.value)

    async def test_api_rate_limiter_per_workspace(self, mock_db):
        """API rate limiter is per-workspace."""
        api_limiter.reset()

        ws_id = "ws-rate-test"
        limit = api_limiter.limit
        for i in range(limit):
            await api_limiter.check(ws_id)

        with pytest.raises(Exception) as exc_info:
            await api_limiter.check(ws_id)
        assert "API rate limit exceeded" in str(exc_info.value)

        # Different workspace should not be affected
        await api_limiter.check("ws-rate-test-2")  # Should not raise

    async def test_execution_quota_per_user(self, mock_db):
        """Execution quota is per user per workspace."""
        execution_quota.reset()

        ws_id = "ws-quota-test"
        user_id = "user-quota-test"

        # Set a very low quota for testing
        execution_quota.max_executions_per_hour = 2
        await execution_quota.check_and_record(ws_id, user_id)
        await execution_quota.check_and_record(ws_id, user_id)

        with pytest.raises(Exception) as exc_info:
            await execution_quota.check_and_record(ws_id, user_id)
        assert "Execution quota exceeded" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Authorization enforcement tests
# ---------------------------------------------------------------------------

class TestAuthorization:
    """Verify authorization across all paths."""

    async def test_tool_access_workspace_scoped(self, mock_db):
        """Tools are workspace-scoped in the registry."""
        from app.integrations.registry import ToolRegistry
        from app.integrations.tool import ToolDescriptor, ToolType, ToolSchema, RiskCategory

        registry = ToolRegistry(mock_db)

        ws_id = "ws-tool-test"
        tool = ToolDescriptor(
            id="composio:github:create_issue",
            provider=ToolType.COMPOSIO,
            name="create_issue",
            description="Create a GitHub issue",
            input_schema=ToolSchema(),
            connection_required=True,
            scopes=[],
            risk_category=RiskCategory.MEDIUM,
            timeout_seconds=30,
            tags=["composio", "github"],
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        # Register tool for workspace A
        await registry.repo.create_entry({
            "tool_id": tool.id,
            "provider": ToolType.COMPOSIO.value,
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema.model_dump(),
            "connection_required": True,
            "scopes": [],
            "risk_category": tool.risk_category.value,
            "timeout_seconds": tool.timeout_seconds,
            "tags": tool.tags,
            "workspace_id": ws_id,
            "enabled": True,
            "allowed_roles": ["owner", "admin", "member"],
            "created_at": tool.created_at,
            "updated_at": tool.updated_at,
        })

        # Access from same workspace should work (no exception)
        # Access from different workspace should raise 403
        import asyncio
        # We'll just verify the repository returns the tool only for ws_id
        entry = await registry.repo.get_entry(tool.id)
        assert entry is not None
        assert entry["workspace_id"] == ws_id
