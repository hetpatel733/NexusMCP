"""Dead Letter Queue (DLQ) Repository for NexusMCP.

Manages failed event inspection, tracking, status updates, and replay history.
Strictly tenant-isolated.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase

from ..logger import logger
from ..repositories.base import BaseRepository
from ..security.redaction import redact_sensitive_data


class DLQEventStatus(str, Enum):
    FAILED = "failed"
    REPLAYING = "replaying"
    RESOLVED = "resolved"
    DISCARDED = "discarded"


class DeadLetterQueueRepository(BaseRepository):
    """Database operations for Dead Letter Queue events."""

    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.dead_letter_queue)
        self.replays: AsyncIOMotorCollection = db.dlq_replays

    async def record_failed_event(
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
        failure_stage: str = "dispatch",  # "ingress", "dispatch", "execution"
    ) -> str:
        """Persist a failed event into DLQ with redacted payload and metadata."""
        now = datetime.now(timezone.utc)
        safe_payload = redact_sensitive_data(payload)
        safe_headers = redact_sensitive_data(headers or {})
        safe_error = redact_sensitive_data(error)

        doc = {
            "workspace_id": workspace_id,
            "event_id": event_id,
            "workflow_id": workflow_id,
            "workflow_version": workflow_version,
            "provider": provider,
            "event_type": event_type,
            "error": safe_error,
            "payload": safe_payload,
            "headers": safe_headers,
            "execution_id": execution_id,
            "failure_stage": failure_stage,
            "status": DLQEventStatus.FAILED.value,
            "replay_count": 0,
            "last_replayed_at": None,
            "created_at": now,
            "updated_at": now,
        }
        res = await self.collection.insert_one(doc)
        logger.warning(
            "Event recorded in Dead Letter Queue",
            dlq_id=str(res.inserted_id),
            event_id=event_id,
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            failure_stage=failure_stage,
            error=safe_error,
        )
        return str(res.inserted_id)

    async def get_dlq_event(self, workspace_id: str, dlq_id: str) -> Optional[Dict[str, Any]]:
        """Fetch single DLQ event strictly scoped to workspace."""
        try:
            doc = await self.collection.find_one({
                "_id": ObjectId(dlq_id),
                "workspace_id": workspace_id,
            })
            if doc:
                doc["id"] = str(doc.pop("_id"))
            return doc
        except Exception:
            return None

    async def list_dlq_events(
        self,
        workspace_id: str,
        workflow_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        """List DLQ events for workspace with optional filters."""
        query: Dict[str, Any] = {"workspace_id": workspace_id}
        if workflow_id:
            query["workflow_id"] = workflow_id
        if status:
            query["status"] = status
        cursor = self.collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
        docs = await cursor.to_list(length=limit)
        for d in docs:
            d["id"] = str(d.pop("_id"))
        return docs

    async def update_dlq_status(
        self,
        workspace_id: str,
        dlq_id: str,
        status: DLQEventStatus,
        resolution_notes: Optional[str] = None,
    ) -> bool:
        """Update DLQ event status."""
        try:
            update = {
                "status": status.value,
                "updated_at": datetime.now(timezone.utc),
            }
            if resolution_notes:
                update["resolution_notes"] = resolution_notes
            res = await self.collection.update_one(
                {"_id": ObjectId(dlq_id), "workspace_id": workspace_id},
                {"$set": update},
            )
            return res.modified_count > 0
        except Exception as e:
            logger.error("Failed to update DLQ status", error=str(e), dlq_id=dlq_id)
            return False

    async def record_replay_attempt(
        self,
        workspace_id: str,
        dlq_id: str,
        replay_idempotency_key: str,
        dispatched_execution_id: Optional[str],
        status: str,
        error: Optional[str] = None,
    ) -> str:
        """Record an immutable replay history record for idempotency and audit."""
        now = datetime.now(timezone.utc)
        replay_doc = {
            "workspace_id": workspace_id,
            "dlq_id": dlq_id,
            "replay_idempotency_key": replay_idempotency_key,
            "dispatched_execution_id": dispatched_execution_id,
            "status": status,
            "error": error,
            "created_at": now,
        }
        res = await self.replays.insert_one(replay_doc)

        # Increment replay_count on main dlq record
        try:
            await self.collection.update_one(
                {"_id": ObjectId(dlq_id), "workspace_id": workspace_id},
                {
                    "$inc": {"replay_count": 1},
                    "$set": {"last_replayed_at": now, "updated_at": now},
                },
            )
        except Exception:
            pass

        return str(res.inserted_id)

    async def get_replay_by_idempotency_key(
        self,
        workspace_id: str,
        replay_idempotency_key: str,
    ) -> Optional[Dict[str, Any]]:
        """Check if a replay was already initiated with this idempotency key."""
        return await self.replays.find_one({
            "workspace_id": workspace_id,
            "replay_idempotency_key": replay_idempotency_key,
        })
