"""Base Trigger Adapter Interface for NexusMCP.

Requirement 1:
- Define a common interface for webhook, polling, and schedule triggers.
- Keep provider-specific trigger logic separate from workflow execution.
- Maintain provider capability flags (e.g. webhook vs polling only).
"""

from __future__ import annotations

import abc
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...models.trigger import EventEnvelope, TriggerSubscription
from ...models.workflow import TriggerConfig


class BaseTriggerAdapter(abc.ABC):
    """Abstract base class for all trigger adapters (Webhook, Polling, Schedule)."""

    def __init__(self, provider_name: str):
        self.provider_name = provider_name

    @property
    @abc.abstractmethod
    def supported_types(self) -> List[str]:
        """List of supported trigger types: 'webhook', 'polling', 'schedule'."""
        pass

    @abc.abstractmethod
    async def register_subscription(
        self,
        workspace_id: str,
        workflow_id: str,
        workflow_version: int,
        trigger_config: TriggerConfig,
        connection_id: Optional[str] = None,
        webhook_base_url: Optional[str] = None,
    ) -> TriggerSubscription:
        """Register a trigger subscription (webhook setup, schedule registration, polling cursor init)."""
        pass

    @abc.abstractmethod
    async def unregister_subscription(
        self,
        subscription: TriggerSubscription,
    ) -> bool:
        """Unregister or clean up external provider webhook or schedule."""
        pass

    @abc.abstractmethod
    async def normalize_event(
        self,
        raw_payload: bytes,
        headers: Dict[str, str],
        workspace_id: str,
        workflow_id: Optional[str] = None,
        workflow_version: Optional[int] = None,
        connection_id: Optional[str] = None,
    ) -> EventEnvelope:
        """Convert incoming provider payload into canonical EventEnvelope."""
        pass

    def compute_idempotency_key(
        self,
        provider: str,
        provider_event_id: Optional[str],
        event_type: str,
        raw_payload: bytes,
    ) -> str:
        """Compute stable idempotency fingerprint.
        
        Uses provider event ID if provided, otherwise SHA256 of normalized payload & event type.
        """
        if provider_event_id and provider_event_id.strip():
            combined = f"{provider}:{event_type}:{provider_event_id.strip()}"
            return hashlib.sha256(combined.encode("utf-8")).hexdigest()
        
        # Fallback to payload hash fingerprint
        payload_hash = hashlib.sha256(raw_payload).hexdigest()
        combined = f"{provider}:{event_type}:{payload_hash}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    def generate_payload_reference(self, raw_payload: bytes) -> str:
        """Generate payload content reference (SHA256 reference or storage pointer)."""
        return f"sha256:{hashlib.sha256(raw_payload).hexdigest()}"
