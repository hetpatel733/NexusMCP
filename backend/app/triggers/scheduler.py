"""Schedule Trigger Engine for NexusMCP.

Requirement 7:
- Schedule triggers using deliberate time calculations and Temporal schedule support.
- Accurate timezone and daylight-saving transition handling via zoneinfo.
- Centralized evaluation loop avoiding uncontrolled background threads.
"""

from __future__ import annotations

import asyncio
import zoneinfo
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..logger import logger
from ..models.trigger import IngestionCheckpoint, IngestionStatus, SubscriptionStatus
from ..models.workflow import WorkflowStatus
from ..repositories.trigger_repo import TriggerRepository
from ..repositories.workflow_repo import WorkflowRepository
from .adapters.schedule import ScheduleTriggerAdapter
from .service import TriggerIngestionService


class ScheduleEngine:
    """Evaluates and fires schedule-based triggers."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        ingestion_service: TriggerIngestionService,
    ):
        self.db = db
        self.trigger_repo = TriggerRepository(db)
        self.workflow_repo = WorkflowRepository(db)
        self.ingestion_service = ingestion_service
        self.adapter = ScheduleTriggerAdapter()

    async def evaluate_workflow_schedule(
        self,
        workspace_id: str,
        workflow_id: str,
        now: Optional[datetime] = None,
    ) -> bool:
        """Evaluate if scheduled workflow is due to run. If due, fire schedule tick event.
        
        Returns:
            True if executed, False if not due or inactive.
        """
        current_time = now or datetime.now(timezone.utc)

        # Verify workflow is active
        workflow = await self.workflow_repo.get_workflow(workspace_id, workflow_id)
        if not workflow or workflow.get("status") != WorkflowStatus.ACTIVE.value:
            return False

        sub = await self.trigger_repo.get_subscription(workspace_id, workflow_id)
        if not sub or sub.get("status") != SubscriptionStatus.ACTIVE.value:
            return False

        config = sub.get("config", {})
        checkpoint_dict = await self.trigger_repo.get_checkpoint(workspace_id, workflow_id)

        # Check last run
        last_run = checkpoint_dict.get("last_polled_at") if checkpoint_dict else None
        if last_run and hasattr(last_run, "tzinfo") and last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)

        # Determine next due time based on last_run or created_at
        start_reference = last_run or sub.get("created_at") or current_time
        if hasattr(start_reference, "tzinfo") and start_reference.tzinfo is None:
            start_reference = start_reference.replace(tzinfo=timezone.utc)

        next_due = self.adapter.calculate_next_run(config, after_dt=start_reference)

        if current_time < next_due:
            # Not due yet
            return False

        # Is due! Fire canonical event envelope
        active_version = workflow.get("active_version") or workflow.get("current_version", 1)
        envelope = await self.adapter.normalize_event(
            raw_payload=b"",
            headers={"X-Schedule-Trigger": "true"},
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            workflow_version=active_version,
            connection_id=sub.get("connection_id"),
        )

        # Deduplication and durable recording
        is_dup = await self.ingestion_service.dedup_service.is_duplicate_or_record(envelope)
        if not is_dup:
            event_db_id = await self.trigger_repo.save_event(envelope, status=IngestionStatus.ACCEPTED)
            execution_id = await self.ingestion_service._dispatch_event(envelope, event_db_id)
            await self.trigger_repo.update_event_status(
                event_db_id=event_db_id,
                status=IngestionStatus.DISPATCHED,
                execution_id=execution_id,
            )

        # Update checkpoint
        checkpoint = IngestionCheckpoint(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            provider="schedule",
            last_polled_at=current_time,
            last_event_at=current_time,
            poll_count=(checkpoint_dict.get("poll_count", 0) + 1) if checkpoint_dict else 1,
            cursor=current_time.isoformat(),
        )
        await self.trigger_repo.save_checkpoint(checkpoint)
        return True

    async def run_single_cycle(self, now: Optional[datetime] = None) -> int:
        """Run one pass of schedule checks across all active schedule subscriptions."""
        subs = await self.trigger_repo.list_active_subscriptions(trigger_type="schedule")
        fired_count = 0
        for sub in subs:
            try:
                fired = await self.evaluate_workflow_schedule(
                    sub["workspace_id"],
                    sub["workflow_id"],
                    now=now,
                )
                if fired:
                    fired_count += 1
            except Exception as e:
                logger.error("Error evaluating schedule for workflow", workflow_id=sub.get("workflow_id"), error=str(e))
        return fired_count
