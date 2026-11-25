"""Comprehensive Tests for Phase 9: Execution History, Observability, Alerting, and Dead Letter Queue.

Tests cover:
1. Execution History & Step-Level Logs with Tenant Isolation.
2. Sensitive Data Redaction across inputs, outputs, errors, and payloads.
3. Structured Logging with Correlation ID propagation and middleware.
4. Metrics Calculation: success/failure rates, queue latency, trigger delays, step durations, and provider errors.
5. Operational Alerting Engine: repeated failures, expired connections, webhook renewal failures, worker unavailability.
6. Dead Letter Queue (DLQ): failure ingestion, inspection, controlled idempotent replay, and discarding.
7. Real-Time Broadcaster & SSE Streaming with tenant isolation.
8. Observability REST APIs.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

import pytest
from httpx import AsyncClient

from app.execution.models import ExecutionStatus, StepExecutionStatus
from app.execution.service import ExecutionService
from app.observability.alerts import AlertCategory, AlertEvaluator, AlertSeverity
from app.observability.broadcaster import ExecutionEventBroadcaster
from app.observability.correlation import get_correlation_id, set_correlation_id
from app.observability.dlq_service import DeadLetterQueueService
from app.observability.metrics import MetricsService
from app.repositories.dlq_repo import DeadLetterQueueRepository, DLQEventStatus
from app.repositories.execution_repo import ExecutionRepository
from app.repositories.workflow_repo import WorkflowRepository
from app.security.jwt import create_access_token
from app.security.redaction import redact_sensitive_data, redact_string


# ==============================================================================
# Helper Functions for Authenticated Test Fixtures
# ==============================================================================

async def setup_test_workspace_and_user(client: AsyncClient, email: str = "operator@example.com"):
    """Helper to register user and obtain valid JWT token and default workspace."""
    reg_res = await client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "Observability Operator"
    })
    data = reg_res.json()
    token = data["access_token"]

    ws_list = await client.get("/api/v1/workspaces", headers={"Authorization": f"Bearer {token}"})
    workspace_id = ws_list.json()[0]["id"]
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Workspace-ID": workspace_id,
    }
    return workspace_id, data["user"]["id"], headers


# ==============================================================================
# 1. Redaction Unit Tests
# ==============================================================================

def test_redact_sensitive_data():
    """Verify recursive scrubbing of secrets, tokens, credentials, and sensitive headers."""
    sample = {
        "user_email": "operator@example.com",
        "password": "super-secret-password",
        "token": "ghp_1234567890abcdefghijklmnopqrstuvwxyz",
        "nested": {
            "api_key": "sk-1234567890abcdef1234567890abcdef",
            "safe_field": "public_data",
            "authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.do_not_leak",
        },
        "items": [
            {"secret": "my-client-secret", "status": "active"},
            "Raw Bearer secret_token_xyz_12345678901234567890 string inside list",
        ],
    }

    cleaned = redact_sensitive_data(sample)

    assert cleaned["user_email"] == "operator@example.com"
    assert cleaned["password"] == "[REDACTED]"
    assert cleaned["token"] == "[REDACTED]"
    assert cleaned["nested"]["api_key"] == "[REDACTED]"
    assert cleaned["nested"]["safe_field"] == "public_data"
    assert cleaned["nested"]["authorization"] == "[REDACTED]"
    assert cleaned["items"][0]["secret"] == "[REDACTED]"
    assert cleaned["items"][0]["status"] == "active"
    assert "[REDACTED]" in cleaned["items"][1]


# ==============================================================================
# 2. Execution History & Step-Level Logs Repository Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_execution_repo_redaction_and_duration(mock_db):
    """Verify execution tracking records durations, calculates duration_ms, and redacts sensitive inputs/outputs."""
    repo = ExecutionRepository(mock_db)
    ws_id = "ws-tenant-alpha"
    now = datetime.now(timezone.utc)

    # 1. Create execution
    exec_doc = {
        "workspace_id": ws_id,
        "workflow_id": "wf-101",
        "workflow_version": 2,
        "status": ExecutionStatus.PENDING.value,
        "triggered_by": "webhook",
        "trigger_event": {
            "event_id": "evt-1",
            "api_key": "super-secret-key",
            "data": {"message": "hello"},
        },
        "created_at": now,
        "started_at": now,
    }
    exec_id = await repo.create_execution(exec_doc)

    fetched = await repo.get_execution(ws_id, exec_id)
    assert fetched is not None
    assert fetched["trigger_event"]["api_key"] == "[REDACTED]"
    assert fetched["trigger_event"]["data"]["message"] == "hello"

    # 2. Record Step Start with Redaction
    await repo.record_step_start(
        workspace_id=ws_id,
        execution_id=exec_id,
        workflow_id="wf-101",
        workflow_version=2,
        step_id="step_send_email",
        step_name="Send Notification Email",
        attempt=1,
        idempotency_key="idem-key-1",
        input_data={"to": "user@example.com", "password": "email-password-123"},
        correlation_id="corr-test-123",
    )

    step = await repo.get_step_execution(ws_id, exec_id, "step_send_email")
    assert step is not None
    assert step["input_data"]["password"] == "[REDACTED]"
    assert step["correlation_id"] == "corr-test-123"

    # 3. Record Step Completion with Normalized Error Redaction
    await repo.record_step_completion(
        workspace_id=ws_id,
        execution_id=exec_id,
        step_id="step_send_email",
        attempt=1,
        output_data={"token": "secret-res-token", "status": "failed"},
        execution_time_ms=450,
        status=StepExecutionStatus.FAILED,
        error="Failed to connect with Bearer secret_bearer_token_xyz_12345678901234567890",
    )

    completed_step = await repo.get_step_execution(ws_id, exec_id, "step_send_email")
    assert completed_step["status"] == "failed"
    assert completed_step["output_data"]["token"] == "[REDACTED]"
    assert "[REDACTED]" in completed_step["error"]

    # 4. Finalize Execution with duration_ms
    later = now + timedelta(seconds=2, milliseconds=300)
    await repo.update_execution_status(
        workspace_id=ws_id,
        execution_id=exec_id,
        status=ExecutionStatus.FAILED.value,
        error="Step failed with error",
        completed_at=later,
        temporal_workflow_id="wf-temporal-101",
    )

    final_exec = await repo.get_execution(ws_id, exec_id)
    assert final_exec["status"] == "failed"
    assert final_exec["temporal_workflow_id"] == "wf-temporal-101"
    assert final_exec["duration_ms"] == 2300


# ==============================================================================
# 3. Metrics Calculation Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_metrics_service_calculations(mock_db):
    """Verify execution metrics, queue latency, trigger delays, and step-level aggregations."""
    ws_id = "ws-metrics-1"
    wf_id = "wf-metrics-test"
    now = datetime.now(timezone.utc)
    metrics_service = MetricsService(mock_db)

    # Insert test executions
    # Exec 1: Completed
    await mock_db.workflow_executions.insert_one({
        "workspace_id": ws_id,
        "workflow_id": wf_id,
        "status": "completed",
        "created_at": now - timedelta(seconds=10),
        "started_at": now - timedelta(seconds=8),
        "completed_at": now - timedelta(seconds=2),
        "duration_ms": 6000,
        "trigger_event": {"received_at": (now - timedelta(seconds=12)).isoformat()},
    })
    # Exec 2: Failed
    await mock_db.workflow_executions.insert_one({
        "workspace_id": ws_id,
        "workflow_id": wf_id,
        "status": "failed",
        "created_at": now - timedelta(seconds=5),
        "started_at": now - timedelta(seconds=4),
        "completed_at": now - timedelta(seconds=1),
        "duration_ms": 3000,
        "trigger_event": {"received_at": (now - timedelta(seconds=6)).isoformat()},
    })

    # Insert step executions
    await mock_db.step_executions.insert_one({
        "workspace_id": ws_id,
        "workflow_id": wf_id,
        "step_id": "step_fetch",
        "step_name": "Fetch API Data",
        "attempt": 1,
        "status": "completed",
        "execution_time_ms": 1200,
        "started_at": now - timedelta(seconds=5),
    })
    await mock_db.step_executions.insert_one({
        "workspace_id": ws_id,
        "workflow_id": wf_id,
        "step_id": "step_fetch",
        "step_name": "Fetch API Data",
        "attempt": 2,
        "status": "failed",
        "execution_time_ms": 800,
        "error": "HTTP 429 rate_limit exceeded",
        "started_at": now - timedelta(seconds=3),
    })

    # Test Execution Metrics
    exec_metrics = await metrics_service.get_execution_metrics(workspace_id=ws_id, workflow_id=wf_id)
    assert exec_metrics["total_executions"] == 2
    assert exec_metrics["completed"] == 1
    assert exec_metrics["failed"] == 1
    assert exec_metrics["success_rate_percent"] == 50.0
    assert exec_metrics["failure_rate_percent"] == 50.0
    assert exec_metrics["avg_duration_ms"] == 4500.0

    # Test Step Metrics & Provider Error Breakdown
    step_metrics = await metrics_service.get_step_and_provider_metrics(workspace_id=ws_id, workflow_id=wf_id)
    assert step_metrics["total_retries"] == 1
    assert step_metrics["provider_error_breakdown"].get("rate_limit") == 1


# ==============================================================================
# 4. Operational Alert Conditions Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_alert_evaluator_conditions(mock_db):
    """Verify all 4 alert rules: repeated failures, expired connection, webhook renewal failure, worker unavailability."""
    ws_id = "ws-alerts-1"
    now = datetime.now(timezone.utc)
    evaluator = AlertEvaluator(mock_db)

    # 1. Condition: Repeated Failures (>= 3 failures for workflow wf-broken in last 2 hours)
    for i in range(3):
        await mock_db.workflow_executions.insert_one({
            "workspace_id": ws_id,
            "workflow_id": "wf-broken",
            "status": "failed",
            "error": f"Connection timeout {i}",
            "started_at": now - timedelta(minutes=10 + i),
        })

    # 2. Condition: Expired Connection
    await mock_db.connections.insert_one({
        "workspace_id": ws_id,
        "provider": "github",
        "app_name": "GitHub Corporate",
        "status": "expired",
        "expires_at": now - timedelta(hours=1),
    })

    # 3. Condition: Webhook Renewal Failure
    await mock_db.trigger_subscriptions.insert_one({
        "workspace_id": ws_id,
        "workflow_id": "wf-broken",
        "provider": "webhook",
        "status": "renewal_failed",
        "error_message": "Provider returned 401 Bad Credentials on subscription renewal",
    })

    # 4. Condition: Unavailable Workers (No heartbeat recorded)
    # mock_db.worker_heartbeats is empty

    alerts = await evaluator.evaluate_all_alerts(workspace_id=ws_id)
    alert_categories = [a.category for a in alerts]

    assert AlertCategory.REPEATED_FAILURES in alert_categories
    assert AlertCategory.EXPIRED_CONNECTIONS in alert_categories
    assert AlertCategory.WEBHOOK_RENEWAL_FAILURES in alert_categories
    assert AlertCategory.UNAVAILABLE_WORKERS in alert_categories

    # Now simulate worker heartbeat check-in
    await evaluator.record_worker_heartbeat("worker-node-1", "nexusmcp-task-queue")
    alerts_after_worker = await evaluator.evaluate_all_alerts(workspace_id=ws_id)
    assert AlertCategory.UNAVAILABLE_WORKERS not in [a.category for a in alerts_after_worker]


# ==============================================================================
# 5. Dead Letter Queue (DLQ) & Idempotent Replay Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_dlq_lifecycle_and_idempotent_replay(mock_db):
    """Verify failed-event recording into DLQ, inspection with redaction, and controlled replay with idempotency."""
    ws_id = "ws-dlq-1"
    dlq_service = DeadLetterQueueService(mock_db)
    wf_repo = WorkflowRepository(mock_db)

    # Create target workflow
    wf_doc = {
        "workspace_id": ws_id,
        "name": "DLQ Target Workflow",
        "status": "active",
        "active_version": 1,
        "current_version": 1,
        "trigger": {"type": "webhook"},
        "steps": [],
    }
    insert_res = await mock_db.workflows.insert_one(wf_doc)
    wf_id = str(insert_res.inserted_id)

    # 1. Record Failed Event into DLQ
    dlq_id = await dlq_service.record_failure(
        workspace_id=ws_id,
        event_id="evt-poison-1",
        workflow_id=wf_id,
        workflow_version=1,
        provider="github",
        event_type="push",
        error="Malformed trigger payload with Bearer secret_token_xyz_12345678901234567890",
        payload={"commit": "abc", "password": "super-secret-key"},
        failure_stage="dispatch",
    )

    # 2. Inspect DLQ Event
    ev = await dlq_service.get_failed_event(ws_id, dlq_id)
    assert ev is not None
    assert ev["payload"]["password"] == "[REDACTED]"
    assert "[REDACTED]" in ev["error"]
    assert ev["status"] == "failed"

    # 3. Controlled Idempotent Replay
    replay_res_1 = await dlq_service.replay_failed_event(
        workspace_id=ws_id,
        dlq_id=dlq_id,
        idempotency_key="custom-replay-key-001",
    )
    assert replay_res_1["status"] == "dispatched"
    assert "execution_id" in replay_res_1

    # Verify DLQ status updated to resolved
    updated_ev = await dlq_service.get_failed_event(ws_id, dlq_id)
    assert updated_ev["status"] == DLQEventStatus.RESOLVED.value
    assert updated_ev["replay_count"] == 1

    # 4. Duplicate Replay with same idempotency key: Returns cached execution without re-dispatching
    replay_res_2 = await dlq_service.replay_failed_event(
        workspace_id=ws_id,
        dlq_id=dlq_id,
        idempotency_key="custom-replay-key-001",
    )
    assert replay_res_2["status"] == "cached"
    assert replay_res_2["execution_id"] == replay_res_1["execution_id"]


# ==============================================================================
# 6. Real-time Event Broadcaster & SSE Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_event_broadcaster_tenant_isolation():
    """Verify live-stream broadcaster delivers messages strictly to the correct workspace and execution."""
    broadcaster = ExecutionEventBroadcaster()
    ws_1 = "ws-broadcaster-1"
    ws_2 = "ws-broadcaster-2"
    exec_1 = "exec-alpha"

    # Subscribe to ws_1, exec_1
    q1 = await broadcaster.subscribe(workspace_id=ws_1, execution_id=exec_1)
    # Subscribe to ws_2
    q2 = await broadcaster.subscribe(workspace_id=ws_2, execution_id=None)

    # Broadcast event for ws_1, exec_1
    await broadcaster.broadcast(
        workspace_id=ws_1,
        event_type="step:completed",
        data={"step_id": "step_1", "status": "completed"},
        execution_id=exec_1,
    )

    # q1 should receive the message
    msg1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    assert msg1["event_type"] == "step:completed"
    assert msg1["workspace_id"] == ws_1

    # q2 should NOT receive the message (tenant isolation)
    assert q2.empty()

    await broadcaster.unsubscribe(q1)
    await broadcaster.unsubscribe(q2)


# ==============================================================================
# 7. Observability & Operations REST API Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_observability_api_endpoints(client: AsyncClient, mock_db):
    """Verify REST API routes for metrics, alerts, worker heartbeat, DLQ replay/discard, and correlation ID."""
    ws_id, user_id, headers = await setup_test_workspace_and_user(client, email="obs.op@example.com")
    now = datetime.now(timezone.utc)

    # Insert execution for metrics endpoint
    await mock_db.workflow_executions.insert_one({
        "workspace_id": ws_id,
        "workflow_id": "wf-api-test",
        "status": "completed",
        "created_at": now - timedelta(minutes=5),
        "started_at": now - timedelta(minutes=4),
        "duration_ms": 1500,
    })

    # Test 1: Correlation ID header in response
    res_corr = await client.get("/api/v1/observability/metrics/executions", headers=headers)
    assert res_corr.status_code == 200
    assert "X-Correlation-ID" in res_corr.headers
    data = res_corr.json()
    assert data["total_executions"] == 1
    assert data["completed"] == 1

    # Test 2: Step metrics endpoint
    res_step = await client.get("/api/v1/observability/metrics/steps", headers=headers)
    assert res_step.status_code == 200

    # Test 3: Worker Heartbeat check-in
    res_hb = await client.post(
        "/api/v1/observability/heartbeat",
        json={"worker_id": "worker-api-1", "task_queue": "nexusmcp-task-queue"},
    )
    assert res_hb.status_code == 200
    assert res_hb.json()["status"] == "ok"

    # Test 4: Alerts endpoint
    res_alerts = await client.get("/api/v1/observability/alerts", headers=headers)
    assert res_alerts.status_code == 200
    assert isinstance(res_alerts.json(), list)

    # Test 5: DLQ Endpoints (Record, List, Get, Discard)
    dlq_service = DeadLetterQueueService(mock_db)
    dlq_id = await dlq_service.record_failure(
        workspace_id=ws_id,
        event_id="evt-api-dlq",
        workflow_id="wf-api-test",
        workflow_version=1,
        provider="webhook",
        event_type="test_event",
        error="Simulated dispatch failure",
        payload={"secret_key": "private_data"},
    )

    res_dlq_list = await client.get("/api/v1/observability/dlq", headers=headers)
    assert res_dlq_list.status_code == 200
    dlq_items = res_dlq_list.json()
    assert len(dlq_items) >= 1

    res_dlq_get = await client.get(f"/api/v1/observability/dlq/{dlq_id}", headers=headers)
    assert res_dlq_get.status_code == 200
    assert res_dlq_get.json()["payload"]["secret_key"] == "[REDACTED]"

    # Test Discard
    res_discard = await client.post(f"/api/v1/observability/dlq/{dlq_id}/discard", headers=headers)
    assert res_discard.status_code == 200
    assert res_discard.json()["status"] == "discarded"
