"""Dead Letter Queue Service for Failed-Event Inspection and Controlled Replay.

Provides:
- Failed event recording across ingestion, dispatch, and execution failure boundaries
- Event payload inspection with secret redaction
- Controlled, idempotent replay with replay_idempotency_key protection
- Replay version resolution: respects immutable workflow versions
- Status transition and audit trail integration
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..logger import logger
from ..repositories.dlq_repo import DeadLetterQueueRepository, DLQEventStatus
from ..repositories.workflow_repo import WorkflowRepository
from ..execution.service import ExecutionService
from ..security.redaction import redact_sensitive_data


class DeadLetterQueueService:
    """Service for managing DLQ events and executing safe replaying."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.dlq_repo = DeadLetterQueueRepository(db)
        self.workflow_repo = WorkflowRepository(db)
        self.execution_service = ExecutionService(db)

    async def list_failed_events(
        self,
        workspace_id: str,
        workflow_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        """List failed events in DLQ for the workspace."""
        return await self.dlq_repo.list_dlq_events(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            status=status,
            limit=limit,
            skip=skip,
        )

    async def get_failed_event(
        self,
        workspace_id: str,
        dlq_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve single failed event with redacted payload."""
        return await self.dlq_repo.get_dlq_event(workspace_id=workspace_id, dlq_id=dlq_id)

    async def record_failure(
        self,
        workspace_id: str,
        event_id: str,
        workflow_id: Optional[str],
        workflow_version: Optional[int],
        provider: str,
        event_type: str,
        error: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        execution_id: Optional[str] = None,
        failure_stage: str = "dispatch",
    ) -> str:
        """Record an event into the DLQ."""
        return await self.dlq_repo.record_failed_event(
            workspace_id=workspace_id,
            event_id=event_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            provider=provider,
            event_type=event_type,
            error=error,
            payload=payload,
            headers=headers,
            execution_id=execution_id,
            failure_stage=failure_stage,
        )

    async def replay_failed_event(
        self,
        workspace_id: str,
        dlq_id: str,
        idempotency_key: Optional[str] = None,
        use_latest_version: bool = False,
    ) -> Dict[str, Any]:
        """Execute a controlled, idempotent replay of a DLQ event."""
        # 1. Fetch DLQ event
        dlq_event = await self.dlq_repo.get_dlq_event(workspace_id, dlq_id)
        if not dlq_event:
            raise ValueError(f"DLQ event '{dlq_id}' not found in workspace")

        workflow_id = dlq_event.get("workflow_id")
        if not workflow_id:
            raise ValueError("DLQ event does not have an associated workflow_id")

        # 2. Idempotency Key protection: prevent double replay on network retry
        replay_key = idempotency_key or f"replay-{dlq_id}-{dlq_event.get('replay_count', 0) + 1}"
        cached_replay = await self.dlq_repo.get_replay_by_idempotency_key(workspace_id, replay_key)
        if cached_replay:
            logger.info("DLQ replay already processed with idempotency key", replay_key=replay_key)
            return {
                "dlq_id": dlq_id,
                "replay_idempotency_key": replay_key,
                "execution_id": cached_replay.get("dispatched_execution_id"),
                "status": "cached",
                "message": "Replay already executed for this idempotency key",
            }

        # 3. Resolve version: either pinned original version or current active version
        workflow = await self.workflow_repo.get_workflow(workspace_id, workflow_id)
        if not workflow:
            raise ValueError(f"Associated workflow '{workflow_id}' not found")

        if use_latest_version:
            target_version = workflow.get("active_version") or workflow.get("current_version", 1)
        else:
            target_version = dlq_event.get("workflow_version") or workflow.get("active_version", 1)

        # 4. Construct trigger event payload
        trigger_event = {
            "event_id": f"dlq-replay-{dlq_event['event_id']}-{uuid.uuid4().hex[:6]}",
            "event_type": dlq_event.get("event_type", "replay_trigger"),
            "provider": dlq_event.get("provider", "dlq_replay"),
            "data": dlq_event.get("payload", {}),
            "replayed_from_dlq": dlq_id,
            "replayed_at": datetime.now(timezone.utc).isoformat(),
        }

        # Update DLQ event status to REPLAYING
        await self.dlq_repo.update_dlq_status(workspace_id, dlq_id, DLQEventStatus.REPLAYING)

        try:
            # 5. Dispatch execution via ExecutionService
            dispatch_res = await self.execution_service.create_and_dispatch_execution(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                triggered_by=f"dlq_replay ({dlq_event.get('provider')})",
                trigger_event=trigger_event,
                workflow_version=target_version,
            )
            dispatched_execution_id = dispatch_res["execution_id"]

            # Record replay attempt
            await self.dlq_repo.record_replay_attempt(
                workspace_id=workspace_id,
                dlq_id=dlq_id,
                replay_idempotency_key=replay_key,
                dispatched_execution_id=dispatched_execution_id,
                status="dispatched",
            )

            # Mark DLQ event resolved or replayed
            await self.dlq_repo.update_dlq_status(
                workspace_id,
                dlq_id,
                DLQEventStatus.RESOLVED,
                resolution_notes=f"Replayed into execution {dispatched_execution_id} (version {target_version})",
            )

            return {
                "dlq_id": dlq_id,
                "replay_idempotency_key": replay_key,
                "execution_id": dispatched_execution_id,
                "workflow_version": target_version,
                "status": "dispatched",
                "message": "Replay successfully dispatched to execution engine",
            }

        except Exception as exc:
            err_msg = str(exc)
            logger.error("Failed to replay DLQ event", dlq_id=dlq_id, error=err_msg)
            await self.dlq_repo.record_replay_attempt(
                workspace_id=workspace_id,
                dlq_id=dlq_id,
                replay_idempotency_key=replay_key,
                dispatched_execution_id=None,
                status="failed",
                error=err_msg,
            )
            await self.dlq_repo.update_dlq_status(
                workspace_id,
                dlq_id,
                DLQEventStatus.FAILED,
                resolution_notes=f"Replay attempt failed: {err_msg}",
            )
            raise ValueError(f"Failed to replay event: {err_msg}")

    async def discard_failed_event(
        self,
        workspace_id: str,
        dlq_id: str,
        notes: Optional[str] = None,
    ) -> bool:
        """Mark a DLQ event as discarded."""
        return await self.dlq_repo.update_dlq_status(
            workspace_id=workspace_id,
            dlq_id=dlq_id,
            status=DLQEventStatus.DISCARDED,
            resolution_notes=notes or "Discarded by operator",
        )
