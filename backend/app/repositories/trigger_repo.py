"""Trigger and Ingestion Repository.

Manages MongoDB collections:
- `trigger_events`: Durable event ledger for accepted canonical events
- `deduplication_records`: Idempotency keys and replay protection with TTL
- `trigger_subscriptions`: Active webhook/polling/schedule subscriptions
- `trigger_checkpoints`: Cursors for polling workflows
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase

from .base import BaseRepository
from ..models.trigger import (
    DeduplicationRecord,
    EventEnvelope,
    IngestionCheckpoint,
    IngestionStatus,
    SubscriptionStatus,
    TriggerSubscription,
)


class TriggerRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.trigger_events)
        self.events_collection: AsyncIOMotorCollection = db.trigger_events
        self.dedup_collection: AsyncIOMotorCollection = db.deduplication_records
        self.subscriptions_collection: AsyncIOMotorCollection = db.trigger_subscriptions
        self.checkpoints_collection: AsyncIOMotorCollection = db.trigger_checkpoints

    async def init_indexes(self) -> None:
        """Create essential indexes for performance, isolation, deduplication, and TTL retention."""
        # Deduplication unique index & TTL expiration index
        try:
            await self.dedup_collection.create_index(
                [("workspace_id", 1), ("idempotency_key", 1)],
                unique=True,
            )
            await self.dedup_collection.create_index(
                [("expires_at", 1)],
                expireAfterSeconds=0,
            )
        except Exception:
            pass

        # Events collection indexes
        try:
            await self.events_collection.create_index(
                [("workspace_id", 1), ("received_at", -1)]
            )
            await self.events_collection.create_index(
                [("status", 1)]
            )
            await self.events_collection.create_index(
                [("event_id", 1)]
            )
        except Exception:
            pass

        # Subscriptions indexes
        try:
            await self.subscriptions_collection.create_index(
                [("workspace_id", 1), ("workflow_id", 1)],
                unique=True,
            )
            await self.subscriptions_collection.create_index(
                [("status", 1), ("trigger_type", 1)]
            )
        except Exception:
            pass

        # Checkpoints indexes
        try:
            await self.checkpoints_collection.create_index(
                [("workspace_id", 1), ("workflow_id", 1)],
                unique=True,
            )
        except Exception:
            pass

    # ------------------ Deduplication & Replay Protection ------------------

    async def try_acquire_idempotency_key(
        self,
        workspace_id: str,
        idempotency_key: str,
        provider: str,
        event_id: str,
        workflow_id: Optional[str],
        ttl_seconds: int = 86400 * 7,  # 7 days retention policy
    ) -> bool:
        """Atomically attempt to acquire an idempotency key.
        
        Returns True if acquired (first time seen), False if duplicate/replay.
        """
        now = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(now.timestamp() + ttl_seconds, tz=timezone.utc)
        record = {
            "workspace_id": workspace_id,
            "idempotency_key": idempotency_key,
            "provider": provider,
            "event_id": event_id,
            "workflow_id": workflow_id,
            "created_at": now,
            "expires_at": expires_at,
        }
        try:
            # Check existing first
            existing = await self.dedup_collection.find_one({
                "workspace_id": workspace_id,
                "idempotency_key": idempotency_key,
            })
            if existing:
                return False

            await self.dedup_collection.insert_one(record)
            return True
        except Exception:
            # In case of duplicate key error
            return False

    async def get_dedup_record(
        self,
        workspace_id: str,
        idempotency_key: str,
    ) -> Optional[Dict[str, Any]]:
        return await self.dedup_collection.find_one({
            "workspace_id": workspace_id,
            "idempotency_key": idempotency_key,
        })

    # ------------------ Durable Event Acceptance ------------------

    async def save_event(
        self,
        envelope: EventEnvelope,
        status: IngestionStatus = IngestionStatus.ACCEPTED,
    ) -> str:
        """Durably persist accepted canonical event envelope into MongoDB before acknowledging provider."""
        doc = envelope.model_dump()
        doc["status"] = status.value
        doc["created_at"] = datetime.now(timezone.utc)
        doc["updated_at"] = doc["created_at"]
        doc["attempts"] = 0
        doc["error"] = None
        res = await self.events_collection.insert_one(doc)
        return str(res.inserted_id)

    async def update_event_status(
        self,
        event_db_id: str,
        status: IngestionStatus,
        error: Optional[str] = None,
        execution_id: Optional[str] = None,
    ) -> bool:
        """Update event ingestion status and link to execution if dispatched."""
        update: Dict[str, Any] = {
            "status": status.value,
            "updated_at": datetime.now(timezone.utc),
        }
        if error:
            update["error"] = error
        if execution_id:
            update["execution_id"] = execution_id

        try:
            res = await self.events_collection.update_one(
                {"_id": ObjectId(event_db_id)},
                {"$set": update, "$inc": {"attempts": 1}},
            )
            return res.modified_count > 0
        except Exception:
            return False

    async def get_event(self, workspace_id: str, event_id: str) -> Optional[Dict[str, Any]]:
        return await self.events_collection.find_one({
            "workspace_id": workspace_id,
            "event_id": event_id,
        })

    async def list_pending_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve accepted/unprocessed events across workspaces for crash recovery."""
        cursor = self.events_collection.find({
            "status": {"$in": [IngestionStatus.ACCEPTED.value, IngestionStatus.PROCESSING.value]}
        }).sort("created_at", 1).limit(limit)
        return await cursor.to_list(length=limit)

    async def list_events_for_workflow(
        self,
        workspace_id: str,
        workflow_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        cursor = self.events_collection.find({
            "workspace_id": workspace_id,
            "workflow_id": workflow_id,
        }).sort("received_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    # ------------------ Subscription Lifecycle ------------------

    async def save_subscription(self, sub: TriggerSubscription) -> str:
        """Upsert trigger subscription for a workflow."""
        doc = sub.model_dump(exclude={"id"})
        doc["updated_at"] = datetime.now(timezone.utc)
        res = await self.subscriptions_collection.update_one(
            {"workspace_id": sub.workspace_id, "workflow_id": sub.workflow_id},
            {"$set": doc},
            upsert=True,
        )
        upserted = getattr(res, "upserted_id", None)
        if upserted:
            return str(upserted)
        existing = await self.subscriptions_collection.find_one({
            "workspace_id": sub.workspace_id,
            "workflow_id": sub.workflow_id,
        })
        return str(existing["_id"]) if existing else ""

    async def get_subscription(
        self,
        workspace_id: str,
        workflow_id: str,
    ) -> Optional[Dict[str, Any]]:
        return await self.subscriptions_collection.find_one({
            "workspace_id": workspace_id,
            "workflow_id": workflow_id,
        })

    async def update_subscription_status(
        self,
        workspace_id: str,
        workflow_id: str,
        status: SubscriptionStatus,
    ) -> bool:
        res = await self.subscriptions_collection.update_one(
            {"workspace_id": workspace_id, "workflow_id": workflow_id},
            {"$set": {"status": status.value, "updated_at": datetime.now(timezone.utc)}},
        )
        return res.modified_count > 0

    async def delete_subscription(
        self,
        workspace_id: str,
        workflow_id: str,
    ) -> bool:
        res = await self.subscriptions_collection.delete_one({
            "workspace_id": workspace_id,
            "workflow_id": workflow_id,
        })
        return res.deleted_count > 0

    async def list_active_subscriptions(
        self,
        trigger_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {"status": SubscriptionStatus.ACTIVE.value}
        if trigger_type:
            query["trigger_type"] = trigger_type
        cursor = self.subscriptions_collection.find(query)
        return await cursor.to_list(length=500)

    # ------------------ Checkpoints (Polling Cursors) ------------------

    async def get_checkpoint(
        self,
        workspace_id: str,
        workflow_id: str,
    ) -> Optional[Dict[str, Any]]:
        return await self.checkpoints_collection.find_one({
            "workspace_id": workspace_id,
            "workflow_id": workflow_id,
        })

    async def save_checkpoint(self, checkpoint: IngestionCheckpoint) -> bool:
        doc = checkpoint.model_dump()
        doc["updated_at"] = datetime.now(timezone.utc)
        await self.checkpoints_collection.update_one(
            {"workspace_id": checkpoint.workspace_id, "workflow_id": checkpoint.workflow_id},
            {"$set": doc},
            upsert=True,
        )
        return True

    async def delete_checkpoint(
        self,
        workspace_id: str,
        workflow_id: str,
    ) -> bool:
        res = await self.checkpoints_collection.delete_one({
            "workspace_id": workspace_id,
            "workflow_id": workflow_id,
        })
        return res.deleted_count > 0
