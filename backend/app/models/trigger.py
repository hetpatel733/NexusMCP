"""Pydantic models and schemas for Phase 7 Trigger Management and Event Ingestion.

Covers:
- Canonical Event Envelopes
- Trigger Subscriptions and Status
- Deduplication Records
- Ingestion Checkpoints (Polling Cursors)
- Webhook Ingress Models
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class IngestionStatus(str, Enum):
    ACCEPTED = "accepted"
    PROCESSING = "processing"
    DISPATCHED = "dispatched"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    SKIPPED = "skipped"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    FAILED = "failed"
    UNREGISTERED = "unregistered"


class EventEnvelope(BaseModel):
    """Canonical event envelope for all incoming trigger events.
    
    Requirement:
    Canonical event envelope containing:
    event_id, provider, connection_id, event_type, received_at, and payload_reference.
    """
    event_id: str = Field(description="Unique event ID (canonical or provider-assigned)")
    provider: str = Field(description="Provider name, e.g. 'github', 'slack', 'webhook', 'schedule', 'custom'")
    connection_id: Optional[str] = Field(default=None, description="Workspace Connection ID if tied to an integration")
    event_type: str = Field(description="Type of event, e.g. 'issues.opened', 'message.posted', 'poll_tick', 'schedule_tick'")
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp when event was accepted")
    payload_reference: str = Field(description="Reference or storage pointer to payload or payload hash")
    
    # Contextual envelope fields
    workspace_id: str = Field(description="Target workspace ID")
    workflow_id: Optional[str] = Field(default=None, description="Target workflow ID if directed")
    workflow_version: Optional[int] = Field(default=None, description="Pinned active workflow version for execution")
    idempotency_key: str = Field(description="Stable deduplication fingerprint")
    data: Dict[str, Any] = Field(default_factory=dict, description="Normalized event payload data")
    raw_headers: Dict[str, str] = Field(default_factory=dict, description="Selected ingress headers")

    model_config = ConfigDict(extra="forbid")


class DeduplicationRecord(BaseModel):
    """Deduplication and replay protection record in MongoDB."""
    idempotency_key: str = Field(description="Unique deduplication hash/fingerprint")
    workspace_id: str = Field(description="Workspace boundary")
    provider: str = Field(description="Provider source")
    event_id: str = Field(description="Canonical event ID")
    workflow_id: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(description="TTL expiration date for automatic cleanup")

    model_config = ConfigDict(extra="forbid")


class IngestionCheckpoint(BaseModel):
    """Polling cursor and checkpoint tracking per workflow / connection."""
    workspace_id: str = Field(description="Workspace ID")
    workflow_id: str = Field(description="Workflow ID")
    connection_id: Optional[str] = Field(default=None, description="Associated connection ID")
    provider: str = Field(description="Provider name")
    cursor: Optional[str] = Field(default=None, description="Provider cursor, timestamp, or highest ID seen")
    last_polled_at: Optional[datetime] = Field(default=None)
    last_event_at: Optional[datetime] = Field(default=None)
    poll_count: int = Field(default=0)
    consecutive_failures: int = Field(default=0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(extra="ignore")


class TriggerSubscription(BaseModel):
    """Lifecycle tracking for registered triggers (webhook subscriptions, scheduled jobs, polling tasks)."""
    id: Optional[str] = None
    workspace_id: str
    workflow_id: str
    workflow_version: int
    trigger_type: str = Field(description="'webhook', 'polling', 'schedule'")
    provider: Optional[str] = None
    connection_id: Optional[str] = None
    status: SubscriptionStatus = Field(default=SubscriptionStatus.ACTIVE)
    
    # External subscription details (e.g. Composio/GitHub webhook subscription ID)
    external_subscription_id: Optional[str] = None
    webhook_url: Optional[str] = None
    secret: Optional[str] = None
    expires_at: Optional[datetime] = None
    renewal_required: bool = False
    
    # Configuration snapshot
    config: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(extra="ignore")


class WebhookIngressResponse(BaseModel):
    """Response returned upon durable event acceptance."""
    status: str = Field(description="'accepted', 'duplicate', or 'rejected'")
    event_id: str
    idempotency_key: str
    received_at: datetime
    message: str
