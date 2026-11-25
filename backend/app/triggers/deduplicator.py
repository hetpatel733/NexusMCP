"""Deduplication and Replay Protection Service for NexusMCP.

Requirement 4:
- Deduplicate using provider event IDs or stable idempotency fingerprints.
- Define a retention policy for deduplication records (default: 7 days TTL).
- Prevent duplicate events from starting duplicate logical work.
- Atomic deduplication check across concurrent workers.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..logger import logger
from ..models.trigger import DeduplicationRecord, EventEnvelope
from ..repositories.trigger_repo import TriggerRepository


class DeduplicationService:
    """Manages idempotency keys, duplicate detection, and retention policy."""

    def __init__(self, repo: TriggerRepository, ttl_seconds: int = 7 * 86400):
        self.repo = repo
        self.ttl_seconds = ttl_seconds

    async def is_duplicate_or_record(
        self,
        envelope: EventEnvelope,
    ) -> bool:
        """Check if event is a duplicate. If not duplicate, record it atomically.
        
        Returns:
            True if duplicate (already seen), False if newly acquired.
        """
        acquired = await self.repo.try_acquire_idempotency_key(
            workspace_id=envelope.workspace_id,
            idempotency_key=envelope.idempotency_key,
            provider=envelope.provider,
            event_id=envelope.event_id,
            workflow_id=envelope.workflow_id,
            ttl_seconds=self.ttl_seconds,
        )
        if not acquired:
            logger.info(
                "Duplicate or replayed event detected; ignoring",
                workspace_id=envelope.workspace_id,
                event_id=envelope.event_id,
                idempotency_key=envelope.idempotency_key,
                provider=envelope.provider,
            )
            return True

        return False
