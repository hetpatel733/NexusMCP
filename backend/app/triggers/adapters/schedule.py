"""Schedule Trigger Adapter for NexusMCP.

Requirement 7:
- Schedule triggers using deliberate time calculations or Temporal schedule definitions.
- Accurate timezone handling and Daylight Saving Time (DST) support using standard `zoneinfo`.
- Interval or cron-based next run calculation.
- Canonical EventEnvelope generation for schedule ticks.
"""

from __future__ import annotations

import hashlib
import json
import uuid
import zoneinfo
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ...logger import logger
from ...models.trigger import EventEnvelope, TriggerSubscription
from ...models.workflow import TriggerConfig
from .base import BaseTriggerAdapter


class ScheduleTriggerAdapter(BaseTriggerAdapter):
    """Adapter for schedule and cron-based triggers."""

    def __init__(self, provider_name: str = "schedule"):
        super().__init__(provider_name=provider_name)

    @property
    def supported_types(self) -> List[str]:
        return ["schedule"]

    async def register_subscription(
        self,
        workspace_id: str,
        workflow_id: str,
        workflow_version: int,
        trigger_config: TriggerConfig,
        connection_id: Optional[str] = None,
        webhook_base_url: Optional[str] = None,
    ) -> TriggerSubscription:
        """Register schedule trigger subscription with timezone support."""
        tz_name = trigger_config.parameters.get("timezone", "UTC")
        # Validate timezone name with zoneinfo
        try:
            zoneinfo.ZoneInfo(tz_name)
        except Exception:
            tz_name = "UTC"

        sub = TriggerSubscription(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            trigger_type="schedule",
            provider=self.provider_name,
            connection_id=connection_id,
            config={
                "cron": trigger_config.cron,
                "interval_seconds": trigger_config.interval_seconds,
                "timezone": tz_name,
                "parameters": trigger_config.parameters,
            },
        )
        return sub

    async def unregister_subscription(
        self,
        subscription: TriggerSubscription,
    ) -> bool:
        return True

    def calculate_next_run(
        self,
        config: Dict[str, Any],
        after_dt: Optional[datetime] = None,
    ) -> datetime:
        """Calculate next scheduled execution datetime taking timezone and DST transitions into account."""
        tz_str = config.get("timezone", "UTC")
        try:
            tz = zoneinfo.ZoneInfo(tz_str)
        except Exception:
            tz = timezone.utc

        now = after_dt or datetime.now(timezone.utc)
        local_now = now.astimezone(tz)

        interval_seconds = config.get("interval_seconds")
        if interval_seconds and interval_seconds > 0:
            next_local = local_now + timedelta(seconds=interval_seconds)
            return next_local.astimezone(timezone.utc)

        cron_str = config.get("cron")
        if cron_str:
            # Parse standard 5-part cron or hourly/daily shortcut
            next_local = self._compute_next_cron(cron_str, local_now)
            return next_local.astimezone(timezone.utc)

        # Default fallback: 1 hour later
        return (local_now + timedelta(hours=1)).astimezone(timezone.utc)

    def _compute_next_cron(self, cron_str: str, from_dt: datetime) -> datetime:
        """Lightweight robust cron evaluation supporting minutes, hours, dom, month, dow."""
        parts = cron_str.strip().split()
        if len(parts) != 5:
            # Fallback to hourly if malformed
            return from_dt.replace(second=0, microsecond=0) + timedelta(hours=1)

        minute_part, hour_part, dom_part, month_part, dow_part = parts

        def match_field(val: int, spec: str, min_val: int, max_val: int) -> bool:
            if spec == "*":
                return True
            for segment in spec.split(","):
                if "/" in segment:
                    base, step = segment.split("/", 1)
                    start = min_val if base == "*" else int(base)
                    step_val = int(step)
                    if val >= start and (val - start) % step_val == 0:
                        return True
                elif "-" in segment:
                    start_str, end_str = segment.split("-", 1)
                    if int(start_str) <= val <= int(end_str):
                        return True
                else:
                    if int(segment) == val:
                        return True
            return False

        # Advance by 1 minute increments up to 366 days
        candidate = from_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
        max_steps = 60 * 24 * 366  # max 1 year search
        for _ in range(max_steps):
            if not match_field(candidate.month, month_part, 1, 12):
                # Jump to next month
                candidate = (candidate.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
                continue
            if not match_field(candidate.day, dom_part, 1, 31):
                candidate = (candidate.replace(hour=0, minute=0) + timedelta(days=1))
                continue
            # cron DOW: 0 = Sunday or 7 = Sunday; Python isoweekday: 1=Mon..7=Sun
            cron_dow = 0 if candidate.isoweekday() == 7 else candidate.isoweekday()
            if not (match_field(cron_dow, dow_part, 0, 7) or (dow_part == "7" and cron_dow == 0)):
                candidate = (candidate.replace(hour=0, minute=0) + timedelta(days=1))
                continue
            if not match_field(candidate.hour, hour_part, 0, 23):
                candidate = (candidate.replace(minute=0) + timedelta(hours=1))
                continue
            if match_field(candidate.minute, minute_part, 0, 59):
                return candidate
            candidate += timedelta(minutes=1)

        return from_dt + timedelta(days=1)

    async def normalize_event(
        self,
        raw_payload: bytes,
        headers: Dict[str, str],
        workspace_id: str,
        workflow_id: Optional[str] = None,
        workflow_version: Optional[int] = None,
        connection_id: Optional[str] = None,
    ) -> EventEnvelope:
        """Generate canonical EventEnvelope for a schedule tick."""
        now = datetime.now(timezone.utc)
        tick_id = f"sched_{workflow_id}_{now.strftime('%Y%m%d%H%M')}"
        event_type = "schedule.tick"
        data = {
            "scheduled_time": now.isoformat(),
            "workflow_id": workflow_id,
            "version": workflow_version,
        }
        payload_bytes = json.dumps(data).encode("utf-8")

        idempotency_key = self.compute_idempotency_key(
            provider=self.provider_name,
            provider_event_id=tick_id,
            event_type=event_type,
            raw_payload=payload_bytes,
        )

        return EventEnvelope(
            event_id=tick_id,
            provider=self.provider_name,
            connection_id=connection_id,
            event_type=event_type,
            received_at=now,
            payload_reference=self.generate_payload_reference(payload_bytes),
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            idempotency_key=idempotency_key,
            data=data,
            raw_headers=headers,
        )
