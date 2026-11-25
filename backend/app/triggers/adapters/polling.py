"""Polling Trigger Adapter for NexusMCP.

Requirement 6 & 9:
- Centralized, rate-limited polling adapter.
- Cursors/checkpoints tracking for incremental item detection.
- Respect provider API quotas and per-connection limits.
- Canonical EventEnvelope generation.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from ...logger import logger
from ...models.trigger import EventEnvelope, IngestionCheckpoint, TriggerSubscription
from ...models.workflow import TriggerConfig
from .base import BaseTriggerAdapter


class PollingTriggerAdapter(BaseTriggerAdapter):
    """Adapter for polling-based triggers with checkpointing and quota awareness."""

    def __init__(
        self,
        provider_name: str = "polling",
        min_interval_seconds: int = 15,
        default_interval_seconds: int = 60,
    ):
        super().__init__(provider_name=provider_name)
        self.min_interval_seconds = min_interval_seconds
        self.default_interval_seconds = default_interval_seconds

    @property
    def supported_types(self) -> List[str]:
        return ["polling"]

    async def register_subscription(
        self,
        workspace_id: str,
        workflow_id: str,
        workflow_version: int,
        trigger_config: TriggerConfig,
        connection_id: Optional[str] = None,
        webhook_base_url: Optional[str] = None,
    ) -> TriggerSubscription:
        """Register a polling trigger subscription with configured interval and initial cursor."""
        interval = trigger_config.interval_seconds or self.default_interval_seconds
        interval = max(interval, self.min_interval_seconds)

        sub = TriggerSubscription(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            trigger_type="polling",
            provider=trigger_config.provider or self.provider_name,
            connection_id=connection_id,
            config={
                "event": trigger_config.event,
                "interval_seconds": interval,
                "parameters": trigger_config.parameters,
                "cursor_field": trigger_config.parameters.get("cursor_field", "id"),
            },
        )
        return sub

    async def unregister_subscription(
        self,
        subscription: TriggerSubscription,
    ) -> bool:
        """Unregister polling trigger."""
        return True

    async def normalize_event(
        self,
        raw_payload: bytes,
        headers: Dict[str, str],
        workspace_id: str,
        workflow_id: Optional[str] = None,
        workflow_version: Optional[int] = None,
        connection_id: Optional[str] = None,
    ) -> EventEnvelope:
        """Normalize polled item into canonical EventEnvelope."""
        data: Dict[str, Any] = {}
        if raw_payload:
            try:
                parsed = json.loads(raw_payload.decode("utf-8"))
                if isinstance(parsed, dict):
                    data = parsed
                else:
                    data = {"items": parsed}
            except Exception:
                data = {"raw": raw_payload.decode("utf-8", errors="replace")}

        provider_event_id = str(data.get("id") or data.get("event_id") or data.get("timestamp") or uuid.uuid4().hex)
        event_type = f"{self.provider_name}.polled_event"

        idempotency_key = self.compute_idempotency_key(
            provider=self.provider_name,
            provider_event_id=provider_event_id,
            event_type=event_type,
            raw_payload=raw_payload,
        )

        return EventEnvelope(
            event_id=provider_event_id,
            provider=self.provider_name,
            connection_id=connection_id,
            event_type=event_type,
            received_at=datetime.now(timezone.utc),
            payload_reference=self.generate_payload_reference(raw_payload),
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            idempotency_key=idempotency_key,
            data=data,
            raw_headers=headers,
        )

    def extract_new_items(
        self,
        items: List[Dict[str, Any]],
        checkpoint: Optional[IngestionCheckpoint],
        cursor_field: str = "id",
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """Filter items newer than the cursor and determine the new cursor value.
        
        Assumes items are ordered or sortable by cursor_field.
        """
        if not items:
            return [], checkpoint.cursor if checkpoint else None

        last_cursor = checkpoint.cursor if checkpoint else None
        new_items: List[Dict[str, Any]] = []
        highest_cursor = last_cursor

        for item in items:
            val = str(item.get(cursor_field, ""))
            if not val:
                continue
            if last_cursor is None or val > last_cursor:
                new_items.append(item)
                if highest_cursor is None or val > highest_cursor:
                    highest_cursor = val

        return new_items, highest_cursor
