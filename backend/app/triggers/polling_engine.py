"""Centralized, Rate-Limited Polling Engine.

Requirement 6 & 9:
- Centralized, rate-limited polling coordinator.
- Maintain cursors and checkpoints to track previously processed data.
- Prevent uncontrolled infinite polling loops.
- Respect provider API quotas and per-connection limits.
- Safely resume polling after API or worker restarts.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..logger import logger
from ..models.trigger import IngestionCheckpoint, IngestionStatus, SubscriptionStatus
from ..models.workflow import WorkflowStatus
from ..repositories.trigger_repo import TriggerRepository
from ..repositories.workflow_repo import WorkflowRepository
from .adapters.polling import PollingTriggerAdapter
from .service import TriggerIngestionService


class RateLimitExceededError(Exception):
    """Raised when connection or provider quota is reached."""
    pass


class PollingEngine:
    """Coordinates polling for all active workflows with polling triggers."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        ingestion_service: TriggerIngestionService,
        fetcher_registry: Optional[Dict[str, Callable]] = None,
    ):
        self.db = db
        self.trigger_repo = TriggerRepository(db)
        self.workflow_repo = WorkflowRepository(db)
        self.ingestion_service = ingestion_service
        self.adapter = PollingTriggerAdapter()
        # Custom fetchers per provider: (workspace_id, connection_id, config, cursor) -> List[Dict]
        self.fetcher_registry: Dict[str, Callable] = fetcher_registry or {}
        # Per-connection rate limiting tracking: connection_id -> (last_poll_ts, request_count_in_window)
        self._connection_rate_tracker: Dict[str, Dict[str, Any]] = {}

    def register_fetcher(self, provider: str, fetcher_fn: Callable) -> None:
        self.fetcher_registry[provider] = fetcher_fn

    def check_connection_quota(self, connection_id: Optional[str], max_requests_per_minute: int = 60) -> bool:
        """Enforce per-connection API quota."""
        if not connection_id:
            return True
        now = datetime.now(timezone.utc).timestamp()
        tracker = self._connection_rate_tracker.setdefault(connection_id, {"window_start": now, "count": 0})
        if now - tracker["window_start"] > 60:
            tracker["window_start"] = now
            tracker["count"] = 0

        if tracker["count"] >= max_requests_per_minute:
            return False

        tracker["count"] += 1
        return True

    async def poll_workflow(
        self,
        workspace_id: str,
        workflow_id: str,
    ) -> int:
        """Poll a single workflow safely, respecting interval, cursor, and quotas.
        
        Returns:
            Number of newly ingested events.
        """
        # 1. Verify workflow is active
        workflow = await self.workflow_repo.get_workflow(workspace_id, workflow_id)
        if not workflow or workflow.get("status") != WorkflowStatus.ACTIVE.value:
            return 0

        sub = await self.trigger_repo.get_subscription(workspace_id, workflow_id)
        if not sub or sub.get("status") != SubscriptionStatus.ACTIVE.value:
            return 0

        config = sub.get("config", {})
        interval_seconds = config.get("interval_seconds", 60)
        connection_id = sub.get("connection_id")
        provider = sub.get("provider", "polling")
        cursor_field = config.get("cursor_field", "id")

        # 2. Check existing checkpoint & interval
        checkpoint_dict = await self.trigger_repo.get_checkpoint(workspace_id, workflow_id)
        now = datetime.now(timezone.utc)
        if checkpoint_dict and checkpoint_dict.get("last_polled_at"):
            last_polled = checkpoint_dict["last_polled_at"]
            if hasattr(last_polled, "tzinfo") and last_polled.tzinfo is None:
                last_polled = last_polled.replace(tzinfo=timezone.utc)
            elapsed = (now - last_polled).total_seconds()
            if elapsed < interval_seconds:
                # Interval has not elapsed yet; skip
                return 0

        checkpoint = IngestionCheckpoint(**checkpoint_dict) if checkpoint_dict else IngestionCheckpoint(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            connection_id=connection_id,
            provider=provider,
        )

        # 3. Check connection rate limit
        quota_allowed = self.check_connection_quota(connection_id)
        if not quota_allowed:
            logger.warning(
                "Polling skipped due to connection rate limit quota",
                connection_id=connection_id,
                workflow_id=workflow_id,
            )
            return 0

        # 4. Fetch raw items from provider / registered fetcher
        items: List[Dict[str, Any]] = []
        fetcher = self.fetcher_registry.get(provider)
        try:
            if fetcher:
                items = await fetcher(workspace_id, connection_id, config, checkpoint.cursor)
            else:
                # Default mock/noop fetcher for polling when provider has no external items
                items = []
        except Exception as e:
            logger.error("Polling fetcher failed", provider=provider, workflow_id=workflow_id, error=str(e))
            checkpoint.consecutive_failures += 1
            checkpoint.last_polled_at = now
            await self.trigger_repo.save_checkpoint(checkpoint)
            return 0

        # 5. Extract new items using cursor
        new_items, new_cursor = self.adapter.extract_new_items(items, checkpoint, cursor_field=cursor_field)

        # Update checkpoint
        checkpoint.last_polled_at = now
        checkpoint.poll_count += 1
        checkpoint.consecutive_failures = 0
        if new_cursor is not None:
            checkpoint.cursor = new_cursor
        if new_items:
            checkpoint.last_event_at = now
        await self.trigger_repo.save_checkpoint(checkpoint)

        # 6. Normalize and ingest new items
        ingested_count = 0
        active_version = workflow.get("active_version") or workflow.get("current_version", 1)

        for item in new_items:
            raw_bytes = json.dumps(item).encode("utf-8")
            envelope = await self.adapter.normalize_event(
                raw_payload=raw_bytes,
                headers={"X-Polling-Provider": provider},
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                workflow_version=active_version,
                connection_id=connection_id,
            )

            # Deduplication
            is_dup = await self.ingestion_service.dedup_service.is_duplicate_or_record(envelope)
            if not is_dup:
                event_db_id = await self.trigger_repo.save_event(envelope, status=IngestionStatus.ACCEPTED)
                execution_id = await self.ingestion_service._dispatch_event(envelope, event_db_id)
                await self.trigger_repo.update_event_status(
                    event_db_id=event_db_id,
                    status=IngestionStatus.DISPATCHED,
                    execution_id=execution_id,
                )
                ingested_count += 1

        return ingested_count

    async def run_single_cycle(self) -> int:
        """Run one pass of polling across all active polling subscriptions."""
        subs = await self.trigger_repo.list_active_subscriptions(trigger_type="polling")
        total_ingested = 0
        for sub in subs:
            try:
                ingested = await self.poll_workflow(sub["workspace_id"], sub["workflow_id"])
                total_ingested += ingested
            except Exception as e:
                logger.error("Error during polling cycle for workflow", workflow_id=sub.get("workflow_id"), error=str(e))
        return total_ingested
