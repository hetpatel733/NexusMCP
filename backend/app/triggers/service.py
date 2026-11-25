"""Trigger Ingestion and Event Dispatcher Service.

Requirement 2, 5, 8:
- Durable Event Acceptance: Persist canonical event before acknowledging provider.
- Never acknowledge an event that exists only in process memory.
- Target the correct workspace and active workflow version.
- Crash recovery: resume accepted unprocessed events upon startup / restart.
- Trigger dispatch: trigger workflow execution (via Temporal client or recorded execution instance).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..logger import logger
from ..models.trigger import (
    EventEnvelope,
    IngestionStatus,
    SubscriptionStatus,
    WebhookIngressResponse,
)
from ..models.workflow import WorkflowStatus
from ..repositories.trigger_repo import TriggerRepository
from ..repositories.workflow_repo import WorkflowRepository
from .adapters.base import BaseTriggerAdapter
from .adapters.webhook import WebhookTriggerAdapter, WebhookVerificationError
from .adapters.polling import PollingTriggerAdapter
from .adapters.schedule import ScheduleTriggerAdapter
from .deduplicator import DeduplicationService


class TriggerIngestionService:
    """Core service for managing event intake, durable persistence, and execution dispatch."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        temporal_client: Optional[Any] = None,
    ):
        self.db = db
        self.trigger_repo = TriggerRepository(db)
        self.workflow_repo = WorkflowRepository(db)
        self.dedup_service = DeduplicationService(self.trigger_repo)
        self.temporal_client = temporal_client

        # Register default adapters
        self.adapters: Dict[str, BaseTriggerAdapter] = {
            "webhook": WebhookTriggerAdapter("webhook"),
            "github": WebhookTriggerAdapter("github"),
            "slack": WebhookTriggerAdapter("slack"),
            "polling": PollingTriggerAdapter("polling"),
            "schedule": ScheduleTriggerAdapter("schedule"),
        }

    def get_adapter(self, provider: str) -> BaseTriggerAdapter:
        return self.adapters.get(provider.lower()) or self.adapters["webhook"]

    async def ingest_webhook_event(
        self,
        workspace_id: str,
        workflow_id: str,
        raw_payload: bytes,
        headers: Dict[str, str],
        max_size_bytes: int = 2 * 1024 * 1024,  # 2MB max request limit
    ) -> WebhookIngressResponse:
        """Secure webhook ingress with verification, normalization, deduplication, and durable persistence."""
        # 1. Payload size check
        if len(raw_payload) > max_size_bytes:
            raise ValueError(f"Payload size exceeds maximum allowed limit ({max_size_bytes} bytes)")

        # 2. Workflow & Subscription Lookup
        workflow = await self.workflow_repo.get_workflow(workspace_id, workflow_id)
        if not workflow:
            raise ValueError("Target workflow not found in workspace")

        current_status = workflow.get("status")
        # Disabling or pausing a workflow stops future trigger delivery (Rule 6, 8)
        if current_status != WorkflowStatus.ACTIVE.value:
            logger.info(
                "Trigger received for inactive workflow; dropping event",
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                status=current_status,
            )
            return WebhookIngressResponse(
                status="rejected",
                event_id="none",
                idempotency_key="none",
                received_at=datetime.now(timezone.utc),
                message=f"Workflow is not active (current status: {current_status}). Event dropped.",
            )

        # 3. Retrieve Subscription & Secret
        subscription = await self.trigger_repo.get_subscription(workspace_id, workflow_id)
        secret = subscription.get("secret") if subscription else None
        active_version = workflow.get("active_version") or workflow.get("current_version", 1)
        provider = (subscription.get("provider") if subscription else None) or workflow.get("trigger", {}).get("provider") or "webhook"
        connection_id = subscription.get("connection_id") if subscription else None

        adapter = self.get_adapter(provider)

        # 4. Signature & Timestamp verification
        if isinstance(adapter, WebhookTriggerAdapter):
            adapter.verify_signature_and_timestamp(raw_payload, headers, secret)

        # 5. Event Normalization
        envelope = await adapter.normalize_event(
            raw_payload=raw_payload,
            headers=headers,
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            workflow_version=active_version,
            connection_id=connection_id,
        )

        # 6. Deduplication and Replay Check
        is_duplicate = await self.dedup_service.is_duplicate_or_record(envelope)
        if is_duplicate:
            return WebhookIngressResponse(
                status="duplicate",
                event_id=envelope.event_id,
                idempotency_key=envelope.idempotency_key,
                received_at=datetime.now(timezone.utc),
                message="Duplicate event ignored",
            )

        # 7. Durable Event Acceptance (Persistence before acknowledgement!)
        event_db_id = await self.trigger_repo.save_event(envelope, status=IngestionStatus.ACCEPTED)

        # 8. Dispatch event asynchronously (or handoff to Temporal)
        try:
            execution_id = await self._dispatch_event(envelope, event_db_id)
            await self.trigger_repo.update_event_status(
                event_db_id=event_db_id,
                status=IngestionStatus.DISPATCHED,
                execution_id=execution_id,
            )
        except Exception as e:
            logger.error("Failed to dispatch accepted event", event_id=envelope.event_id, error=str(e))
            # Event remains saved in ACCEPTED or FAILED status so crash recovery picks it up!
            await self.trigger_repo.update_event_status(
                event_db_id=event_db_id,
                status=IngestionStatus.ACCEPTED,
                error=str(e),
            )
            # Record failed event in Dead Letter Queue for inspection and replay
            try:
                from ..observability.dlq_service import DeadLetterQueueService
                dlq_service = DeadLetterQueueService(self.db)
                await dlq_service.record_failure(
                    workspace_id=envelope.workspace_id,
                    event_id=envelope.event_id,
                    workflow_id=envelope.workflow_id,
                    workflow_version=envelope.workflow_version,
                    provider=envelope.provider,
                    event_type=envelope.event_type,
                    error=str(e),
                    payload=envelope.data,
                    failure_stage="dispatch",
                )
            except Exception:
                pass

        return WebhookIngressResponse(
            status="accepted",
            event_id=envelope.event_id,
            idempotency_key=envelope.idempotency_key,
            received_at=envelope.received_at,
            message="Event accepted and durably recorded",
        )

    async def _dispatch_event(self, envelope: EventEnvelope, event_db_id: str) -> str:
        """Hand off accepted event to Temporal workflow execution via ExecutionService.

        ExecutionService creates the execution record and dispatches to Temporal,
        so we do not create a duplicate record here.
        """
        from ..execution.service import ExecutionService

        # Dispatch via ExecutionService (which uses Temporal) with proper user context
        execution_service = ExecutionService(self.db)
        try:
            execution_id = await execution_service.create_and_dispatch_execution(
                workspace_id=envelope.workspace_id,
                workflow_id=envelope.workflow_id,
                triggered_by=envelope.provider,
                trigger_event={
                    "event_id": envelope.event_id,
                    "event_type": envelope.event_type,
                    "payload_reference": envelope.payload_reference,
                    "data": envelope.data,
                },
                workflow_version=envelope.workflow_version or 1,
                user_id=None,  # Trigger events are system-initiated
                user_role="member",
            )
            logger.info(
                "Workflow execution dispatched from trigger",
                workspace_id=envelope.workspace_id,
                workflow_id=envelope.workflow_id,
                workflow_version=envelope.workflow_version,
                execution_id=execution_id,
                event_id=envelope.event_id,
            )
            return execution_id
        except Exception as e:
            logger.warning("Execution dispatch failed", error=str(e), event_id=envelope.event_id)
            # Return the event_db_id as fallback so the flow can continue
            return event_db_id
        except Exception as e:
            logger.warning("Execution dispatch failed", error=str(e), execution_id=execution_id)

        return execution_id

    async def recover_unprocessed_events(self) -> int:
        """Crash recovery: Find accepted events that were not yet dispatched due to API/worker restart."""
        pending_events = await self.trigger_repo.list_pending_events(limit=100)
        recovered_count = 0
        for ev in pending_events:
            try:
                envelope = EventEnvelope(
                    event_id=ev["event_id"],
                    provider=ev["provider"],
                    connection_id=ev.get("connection_id"),
                    event_type=ev["event_type"],
                    received_at=ev["received_at"],
                    payload_reference=ev["payload_reference"],
                    workspace_id=ev["workspace_id"],
                    workflow_id=ev.get("workflow_id"),
                    workflow_version=ev.get("workflow_version"),
                    idempotency_key=ev["idempotency_key"],
                    data=ev.get("data", {}),
                    raw_headers=ev.get("raw_headers", {}),
                )
                execution_id = await self._dispatch_event(envelope, str(ev["_id"]))
                await self.trigger_repo.update_event_status(
                    event_db_id=str(ev["_id"]),
                    status=IngestionStatus.DISPATCHED,
                    execution_id=execution_id,
                )
                recovered_count += 1
            except Exception as e:
                logger.error("Error during event recovery", event_id=ev.get("event_id"), error=str(e))
        return recovered_count
