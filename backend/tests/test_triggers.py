"""Unit and Integration Tests for Phase 7 Trigger Management and Event Ingestion.

Tests:
1. Webhook signature verification (valid GitHub HMAC-SHA256, Slack v0, generic, secret header, and invalid signatures).
2. Timestamp and replay window enforcement (expired timestamp rejection).
3. Deduplication and replay protection (duplicate events ignored, non-duplicate processed).
4. Durable event acceptance (events persist to database before provider ACK, survival on crash/restart).
5. Polling triggers (checkpoint cursor advancement, interval enforcement, per-connection quota limit).
6. Schedule triggers (cron and interval next run calculation, timezone and DST transition support).
7. Subscription lifecycle (registration on activation, pausing on workflow pause, unregistering on archive/deletion).
8. Workflow status enforcement (inactive/paused/draft workflows drop/reject incoming triggers).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import zoneinfo
from datetime import datetime, timedelta, timezone

import pytest

from app.models.trigger import (
    EventEnvelope,
    IngestionCheckpoint,
    IngestionStatus,
    SubscriptionStatus,
)
from app.models.workflow import TriggerConfig, TriggerType, WorkflowStatus
from app.repositories.trigger_repo import TriggerRepository
from app.repositories.workflow_repo import WorkflowRepository
from app.triggers.adapters.polling import PollingTriggerAdapter
from app.triggers.adapters.schedule import ScheduleTriggerAdapter
from app.triggers.adapters.webhook import WebhookTriggerAdapter, WebhookVerificationError
from app.triggers.deduplicator import DeduplicationService
from app.triggers.lifecycle import SubscriptionLifecycleManager
from app.triggers.polling_engine import PollingEngine
from app.triggers.scheduler import ScheduleEngine
from app.triggers.service import TriggerIngestionService
from tests.mock_db import MockDatabase


@pytest.fixture
def mock_db():
    return MockDatabase()


# -------------------------------------------------------------------------
# 1. Webhook Signature & Security Tests
# -------------------------------------------------------------------------

def test_webhook_signature_github_valid():
    adapter = WebhookTriggerAdapter(provider_name="github")
    secret = "my-secret-key-123"
    body = b'{"action": "opened", "issue": {"id": 42}}'
    sig = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    
    headers = {"X-Hub-Signature-256": sig}
    # Should succeed without exception
    adapter.verify_signature_and_timestamp(body, headers, secret)


def test_webhook_signature_github_invalid():
    adapter = WebhookTriggerAdapter(provider_name="github")
    secret = "my-secret-key-123"
    body = b'{"action": "opened", "issue": {"id": 42}}'
    
    headers = {"X-Hub-Signature-256": "sha256=invalidhexsignature0000"}
    with pytest.raises(WebhookVerificationError, match="Invalid GitHub HMAC-SHA256 signature"):
        adapter.verify_signature_and_timestamp(body, headers, secret)


def test_webhook_signature_slack_valid():
    adapter = WebhookTriggerAdapter(provider_name="slack")
    secret = "slack-signing-secret"
    body = b'{"type": "message", "text": "hello"}'
    ts = str(int(time.time()))
    base = f"v0:{ts}:{body.decode('utf-8')}".encode("utf-8")
    sig = "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()

    headers = {
        "X-Slack-Signature": sig,
        "X-Slack-Request-Timestamp": ts,
    }
    adapter.verify_signature_and_timestamp(body, headers, secret)


def test_webhook_timestamp_replay_rejection():
    adapter = WebhookTriggerAdapter(provider_name="github")
    secret = "my-secret-key"
    body = b'{"data": "test"}'
    
    # Timestamp from 10 minutes ago (> 300s)
    old_ts = str(int(time.time() - 600))
    headers = {
        "X-Timestamp": old_ts,
        "X-Webhook-Secret": secret,
    }
    with pytest.raises(WebhookVerificationError, match="Webhook timestamp expired"):
        adapter.verify_signature_and_timestamp(body, headers, secret, max_age_seconds=300)


def test_webhook_secret_header_valid_and_invalid():
    adapter = WebhookTriggerAdapter(provider_name="webhook")
    secret = "super-secret-token"
    body = b'{"event": "ping"}'

    # Valid
    adapter.verify_signature_and_timestamp(body, {"X-Webhook-Secret": secret}, secret)

    # Invalid
    with pytest.raises(WebhookVerificationError, match="Invalid X-Webhook-Secret token"):
        adapter.verify_signature_and_timestamp(body, {"X-Webhook-Secret": "wrong"}, secret)


# -------------------------------------------------------------------------
# 2. Event Normalization & Envelope Tests
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_normalization():
    adapter = WebhookTriggerAdapter(provider_name="github")
    raw_payload = json.dumps({"action": "opened", "issue": {"number": 101}}).encode("utf-8")
    headers = {
        "X-GitHub-Delivery": "delivery-uuid-999",
        "X-GitHub-Event": "issues",
    }
    envelope = await adapter.normalize_event(
        raw_payload=raw_payload,
        headers=headers,
        workspace_id="ws_1",
        workflow_id="wf_1",
        workflow_version=2,
    )

    assert envelope.event_id == "delivery-uuid-999"
    assert envelope.provider == "github"
    assert envelope.event_type == "issues"
    assert envelope.workspace_id == "ws_1"
    assert envelope.workflow_id == "wf_1"
    assert envelope.workflow_version == 2
    assert envelope.payload_reference.startswith("sha256:")
    assert envelope.data["issue"]["number"] == 101
    assert envelope.idempotency_key is not None


# -------------------------------------------------------------------------
# 3. Deduplication and Replay Protection Tests
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deduplication_service(mock_db):
    repo = TriggerRepository(mock_db)
    dedup = DeduplicationService(repo)

    envelope = EventEnvelope(
        event_id="evt_unique_1",
        provider="github",
        event_type="push",
        payload_reference="sha256:abc",
        workspace_id="ws_1",
        workflow_id="wf_1",
        workflow_version=1,
        idempotency_key="fp_stable_123",
        data={"ref": "main"},
    )

    # First attempt: not duplicate
    is_dup1 = await dedup.is_duplicate_or_record(envelope)
    assert is_dup1 is False

    # Second attempt: must be flagged as duplicate
    is_dup2 = await dedup.is_duplicate_or_record(envelope)
    assert is_dup2 is True


# -------------------------------------------------------------------------
# 4. Durable Event Acceptance & Webhook Ingestion Service
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_webhook_ingestion_and_durable_acceptance(mock_db):
    workflow_repo = WorkflowRepository(mock_db)
    trigger_repo = TriggerRepository(mock_db)
    service = TriggerIngestionService(mock_db)

    # Setup active workflow
    wf_id = await workflow_repo.create({
        "workspace_id": "ws_test",
        "name": "GitHub Test Workflow",
        "status": WorkflowStatus.ACTIVE.value,
        "current_version": 1,
        "active_version": 1,
        "trigger": {"type": "webhook", "provider": "github"},
    })

    # Setup subscription with secret
    secret = "gh_secret_test"
    await trigger_repo.save_subscription(
        adapter_sub := (await WebhookTriggerAdapter("github").register_subscription(
            workspace_id="ws_test",
            workflow_id=wf_id,
            workflow_version=1,
            trigger_config=TriggerConfig(type=TriggerType.WEBHOOK, provider="github", parameters={"secret": secret}),
        ))
    )

    body = b'{"action": "created", "comment": {"id": 555}}'
    sig = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Delivery": "delivery-first-001",
        "X-GitHub-Event": "issue_comment",
    }

    # 1. Ingest event
    res = await service.ingest_webhook_event(
        workspace_id="ws_test",
        workflow_id=wf_id,
        raw_payload=body,
        headers=headers,
    )

    assert res.status == "accepted"
    assert res.event_id == "delivery-first-001"

    # Confirm event is durably stored in database
    saved_event = await trigger_repo.get_event("ws_test", "delivery-first-001")
    assert saved_event is not None
    assert saved_event["status"] == IngestionStatus.DISPATCHED.value
    assert saved_event["data"]["comment"]["id"] == 555

    # Confirm execution was recorded and pinned to version 1
    executions = await workflow_repo.list_executions("ws_test", workflow_id=wf_id)
    assert len(executions) == 1
    assert executions[0]["workflow_version"] == 1

    # 2. Send duplicate event (same delivery ID & payload)
    dup_res = await service.ingest_webhook_event(
        workspace_id="ws_test",
        workflow_id=wf_id,
        raw_payload=body,
        headers=headers,
    )
    assert dup_res.status == "duplicate"
    # Execution count must still be 1 (duplicate not executed)
    executions_after = await workflow_repo.list_executions("ws_test", workflow_id=wf_id)
    assert len(executions_after) == 1


@pytest.mark.asyncio
async def test_inactive_workflow_drops_trigger(mock_db):
    workflow_repo = WorkflowRepository(mock_db)
    service = TriggerIngestionService(mock_db)

    # Workflow is in DRAFT
    wf_id = await workflow_repo.create({
        "workspace_id": "ws_test",
        "name": "Draft Workflow",
        "status": WorkflowStatus.DRAFT.value,
        "current_version": 1,
        "active_version": None,
        "trigger": {"type": "webhook"},
    })

    res = await service.ingest_webhook_event(
        workspace_id="ws_test",
        workflow_id=wf_id,
        raw_payload=b'{"test": 1}',
        headers={},
    )
    assert res.status == "rejected"
    assert "not active" in res.message


# -------------------------------------------------------------------------
# 5. Crash Recovery for Accepted Unprocessed Events
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_crash_recovery_unprocessed_events(mock_db):
    trigger_repo = TriggerRepository(mock_db)
    workflow_repo = WorkflowRepository(mock_db)
    service = TriggerIngestionService(mock_db)

    # Simulate an accepted event that crashed before dispatch
    wf_id = await workflow_repo.create({
        "workspace_id": "ws_test",
        "name": "Recovery WF",
        "status": WorkflowStatus.ACTIVE.value,
        "current_version": 1,
        "active_version": 1,
    })

    envelope = EventEnvelope(
        event_id="crash_evt_1",
        provider="webhook",
        event_type="test",
        payload_reference="sha256:xyz",
        workspace_id="ws_test",
        workflow_id=wf_id,
        workflow_version=1,
        idempotency_key="crash_key_1",
        data={"recovered": True},
    )
    # Saved with status ACCEPTED
    await trigger_repo.save_event(envelope, status=IngestionStatus.ACCEPTED)

    # Run crash recovery
    recovered_count = await service.recover_unprocessed_events()
    assert recovered_count == 1

    # Check updated status
    db_evt = await trigger_repo.get_event("ws_test", "crash_evt_1")
    assert db_evt["status"] == IngestionStatus.DISPATCHED.value
    assert db_evt.get("execution_id") is not None


# -------------------------------------------------------------------------
# 6. Polling Engine and Checkpoints Tests
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_polling_engine_cursor_and_quota(mock_db):
    workflow_repo = WorkflowRepository(mock_db)
    trigger_repo = TriggerRepository(mock_db)
    service = TriggerIngestionService(mock_db)
    engine = PollingEngine(mock_db, service)

    wf_id = await workflow_repo.create({
        "workspace_id": "ws_poll",
        "name": "Polling WF",
        "status": WorkflowStatus.ACTIVE.value,
        "current_version": 1,
        "active_version": 1,
        "trigger": {"type": "polling", "provider": "mock_feed", "interval_seconds": 10},
    })

    # Register subscription
    adapter = PollingTriggerAdapter("mock_feed")
    sub = await adapter.register_subscription(
        workspace_id="ws_poll",
        workflow_id=wf_id,
        workflow_version=1,
        trigger_config=TriggerConfig(
            type=TriggerType.POLLING,
            provider="mock_feed",
            interval_seconds=10,
            parameters={"cursor_field": "id"},
        ),
        connection_id="conn_abc",
    )
    await trigger_repo.save_subscription(sub)

    # Mock fetcher returning 3 items with increasing IDs
    items_store = [
        {"id": "001", "title": "First issue"},
        {"id": "002", "title": "Second issue"},
        {"id": "003", "title": "Third issue"},
    ]

    async def mock_fetcher(ws_id, conn_id, config, cursor):
        return items_store

    engine.register_fetcher("mock_feed", mock_fetcher)

    # 1. First poll cycle: all 3 items ingested
    ingested_1 = await engine.poll_workflow("ws_poll", wf_id)
    assert ingested_1 == 3

    # Checkpoint cursor should now be "003"
    checkpoint = await trigger_repo.get_checkpoint("ws_poll", wf_id)
    assert checkpoint["cursor"] == "003"
    assert checkpoint["poll_count"] == 1

    # 2. Second poll cycle immediately: interval (10s) has NOT elapsed -> 0 items
    ingested_2 = await engine.poll_workflow("ws_poll", wf_id)
    assert ingested_2 == 0

    # 3. Simulate elapsed interval + add 1 new item ("004")
    await trigger_repo.checkpoints_collection.update_one(
        {"workspace_id": "ws_poll", "workflow_id": wf_id},
        {"$set": {"last_polled_at": datetime.now(timezone.utc) - timedelta(seconds=20)}},
    )
    items_store.append({"id": "004", "title": "Fourth issue"})

    ingested_3 = await engine.poll_workflow("ws_poll", wf_id)
    assert ingested_3 == 1

    # Check updated cursor
    chk_after = await trigger_repo.get_checkpoint("ws_poll", wf_id)
    assert chk_after["cursor"] == "004"

    # 4. Connection Quota check
    for _ in range(70):
        engine.check_connection_quota("conn_quota_test", max_requests_per_minute=5)
    # Should be throttled
    allowed = engine.check_connection_quota("conn_quota_test", max_requests_per_minute=5)
    assert allowed is False


# -------------------------------------------------------------------------
# 7. Schedule Engine & Timezone / DST Handling Tests
# -------------------------------------------------------------------------

def test_schedule_adapter_cron_and_timezone():
    adapter = ScheduleTriggerAdapter()

    # Daily at 9:00 AM New York time
    config = {
        "cron": "0 9 * * *",
        "timezone": "America/New_York",
    }
    # Current time: 8:00 AM UTC
    ref_dt = datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
    next_run = adapter.calculate_next_run(config, after_dt=ref_dt)

    # In June (EDT), New York is UTC-4. 9:00 AM EDT = 13:00 UTC.
    ny_tz = zoneinfo.ZoneInfo("America/New_York")
    local_next = next_run.astimezone(ny_tz)
    assert local_next.hour == 9
    assert local_next.minute == 0


def test_schedule_adapter_dst_transition():
    adapter = ScheduleTriggerAdapter()
    config = {
        "cron": "0 9 * * *",
        "timezone": "America/New_York",
    }
    # In January (EST, UTC-5): 9:00 AM EST = 14:00 UTC
    ref_winter = datetime(2026, 1, 15, 8, 0, 0, tzinfo=timezone.utc)
    next_winter = adapter.calculate_next_run(config, after_dt=ref_winter)
    ny_tz = zoneinfo.ZoneInfo("America/New_York")
    assert next_winter.astimezone(ny_tz).hour == 9
    assert next_winter.hour == 14  # 14:00 UTC in standard time

    # In July (EDT, UTC-4): 9:00 AM EDT = 13:00 UTC
    ref_summer = datetime(2026, 7, 15, 8, 0, 0, tzinfo=timezone.utc)
    next_summer = adapter.calculate_next_run(config, after_dt=ref_summer)
    assert next_summer.astimezone(ny_tz).hour == 9
    assert next_summer.hour == 13  # 13:00 UTC in daylight saving time


@pytest.mark.asyncio
async def test_schedule_engine_execution(mock_db):
    workflow_repo = WorkflowRepository(mock_db)
    trigger_repo = TriggerRepository(mock_db)
    service = TriggerIngestionService(mock_db)
    scheduler = ScheduleEngine(mock_db, service)

    wf_id = await workflow_repo.create({
        "workspace_id": "ws_sched",
        "name": "Scheduled WF",
        "status": WorkflowStatus.ACTIVE.value,
        "current_version": 1,
        "active_version": 1,
        "trigger": {"type": "schedule", "interval_seconds": 60},
    })

    sub = await ScheduleTriggerAdapter().register_subscription(
        workspace_id="ws_sched",
        workflow_id=wf_id,
        workflow_version=1,
        trigger_config=TriggerConfig(
            type=TriggerType.SCHEDULE,
            interval_seconds=60,
        ),
    )
    # Set created_at in the past so it is due
    sub.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    await trigger_repo.save_subscription(sub)

    # Evaluate schedule
    fired = await scheduler.evaluate_workflow_schedule("ws_sched", wf_id)
    assert fired is True

    # Check execution was dispatched
    executions = await workflow_repo.list_executions("ws_sched", workflow_id=wf_id)
    assert len(executions) == 1
    assert executions[0]["triggered_by"] == "schedule"


# -------------------------------------------------------------------------
# 8. Subscription Lifecycle Tests
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscription_lifecycle(mock_db):
    trigger_repo = TriggerRepository(mock_db)
    lifecycle = SubscriptionLifecycleManager(mock_db)

    # 1. Register subscription
    sub = await lifecycle.register_workflow_trigger(
        workspace_id="ws_life",
        workflow_id="wf_life",
        workflow_version=1,
        trigger_config_dict={"type": "webhook", "provider": "github"},
    )
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.webhook_url is not None

    saved = await trigger_repo.get_subscription("ws_life", "wf_life")
    assert saved is not None
    assert saved["status"] == SubscriptionStatus.ACTIVE.value

    # 2. Deactivate (Workflow paused)
    await lifecycle.deactivate_workflow_trigger("ws_life", "wf_life")
    saved_paused = await trigger_repo.get_subscription("ws_life", "wf_life")
    assert saved_paused["status"] == SubscriptionStatus.PAUSED.value

    # 3. Resume (Workflow resumed)
    await lifecycle.resume_workflow_trigger("ws_life", "wf_life")
    saved_active = await trigger_repo.get_subscription("ws_life", "wf_life")
    assert saved_active["status"] == SubscriptionStatus.ACTIVE.value

    # 4. Unregister (Workflow archived or deleted)
    await lifecycle.unregister_workflow_trigger("ws_life", "wf_life")
    saved_deleted = await trigger_repo.get_subscription("ws_life", "wf_life")
    assert saved_deleted is None


# -------------------------------------------------------------------------
# 9. API Ingress Endpoint HTTP Integration Tests
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_webhook_ingress_endpoint(client, mock_db):
    from app.security.rate_limiter import auth_limiter
    auth_limiter.reset()

    # Register user
    user_res = await client.post("/api/v1/auth/register", json={
        "email": "trigger-tester@example.com",
        "password": "Password123!",
        "full_name": "Trigger Tester",
    })
    token = user_res.json()["access_token"]
    ws_res = await client.get("/api/v1/workspaces", headers={"Authorization": f"Bearer {token}"})
    workspace_id = ws_res.json()[0]["id"]

    # Create & activate a workflow
    create_res = await client.post(
        "/api/v1/workflows",
        json={
            "name": "Live Webhook Workflow",
            "trigger": {"type": "webhook", "provider": "generic", "parameters": {"secret": "super_token"}},
        },
        headers={"Authorization": f"Bearer {token}", "X-Workspace-ID": workspace_id},
    )
    wf_id = create_res.json()["id"]

    # Activate workflow
    await client.post(
        f"/api/v1/workflows/{wf_id}/activate",
        json={"approved": True},
        headers={"Authorization": f"Bearer {token}", "X-Workspace-ID": workspace_id},
    )

    # Ingest event via POST /api/v1/triggers/webhook/{workspace_id}/{workflow_id}
    webhook_payload = json.dumps({"event": "order_created", "order_id": 9999}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Secret": "super_token",
    }
    ingress_res = await client.post(
        f"/api/v1/triggers/webhook/{workspace_id}/{wf_id}",
        content=webhook_payload,
        headers=headers,
    )
    assert ingress_res.status_code == 200
    ingress_data = ingress_res.json()
    assert ingress_data["status"] == "accepted"

    # List events via GET /api/v1/triggers/events
    events_res = await client.get(
        f"/api/v1/triggers/events?workflow_id={wf_id}",
        headers={"Authorization": f"Bearer {token}", "X-Workspace-ID": workspace_id},
    )
    assert events_res.status_code == 200
    events = events_res.json()
    assert len(events) >= 1
    assert events[0]["data"]["order_id"] == 9999
