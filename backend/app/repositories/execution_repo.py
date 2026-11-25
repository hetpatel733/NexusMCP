"""Repository for Execution Records, Step Tracking, and Payload Storage.

Ensures strict tenant-isolation across executions, steps, and large payload storage.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase

from ..execution.models import (
    ExecutionStatus,
    PayloadReference,
    StepExecutionRecord,
    StepExecutionStatus,
)
from ..logger import logger
from ..observability.broadcaster import get_broadcaster
from ..security.redaction import redact_sensitive_data
from .base import BaseRepository


class ExecutionRepository(BaseRepository):
    """Database operations for workflow executions, step tracking, and payloads."""

    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.workflow_executions)
        self.step_executions: AsyncIOMotorCollection = db.step_executions
        self.payloads: AsyncIOMotorCollection = db.execution_payloads
        self.workflows: AsyncIOMotorCollection = db.workflows
        self.workflow_versions: AsyncIOMotorCollection = db.workflow_versions

    # Execution Lifecycle
    async def create_execution(self, record: Dict[str, Any]) -> str:
        """Create a new execution record pinned to workflow_version."""
        now = datetime.now(timezone.utc)
        record["created_at"] = record.get("created_at", now)
        record["started_at"] = record.get("started_at", now)
        record["status"] = record.get("status", ExecutionStatus.PENDING.value)
        record["steps"] = record.get("steps", {})
        if "trigger_event" in record:
            record["trigger_event"] = redact_sensitive_data(record["trigger_event"])
        res = await self.collection.insert_one(record)
        execution_id = str(res.inserted_id)

        # Notify real-time subscribers
        try:
            broadcaster = get_broadcaster()
            await broadcaster.broadcast(
                workspace_id=record["workspace_id"],
                event_type="execution:created",
                data={
                    "execution_id": execution_id,
                    "workflow_id": record["workflow_id"],
                    "workflow_version": record["workflow_version"],
                    "status": record["status"],
                    "triggered_by": record.get("triggered_by", "manual"),
                    "started_at": record["started_at"].isoformat() if hasattr(record["started_at"], "isoformat") else str(record["started_at"]),
                },
                execution_id=execution_id,
            )
        except Exception:
            pass

        return execution_id

    async def get_execution(self, workspace_id: str, execution_id: str) -> Optional[Dict[str, Any]]:
        """Fetch execution record strictly scoped to workspace."""
        try:
            return await self.collection.find_one({
                "_id": ObjectId(execution_id),
                "workspace_id": workspace_id,
            })
        except Exception:
            return None

    async def list_executions(
        self,
        workspace_id: str,
        workflow_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        """List execution records with optional workflow_id and status filters."""
        query: Dict[str, Any] = {"workspace_id": workspace_id}
        if workflow_id:
            query["workflow_id"] = workflow_id
        if status:
            query["status"] = status
        cursor = self.collection.find(query).sort("started_at", -1).skip(skip).limit(limit)
        return await cursor.to_list(length=limit)

    async def update_execution_status(
        self,
        workspace_id: str,
        execution_id: str,
        status: str,
        error: Optional[str] = None,
        completed_at: Optional[datetime] = None,
        step_summary: Optional[Dict[str, Any]] = None,
        temporal_workflow_id: Optional[str] = None,
    ) -> bool:
        """Update status and summary of an execution."""
        update_data: Dict[str, Any] = {
            "status": status,
            "updated_at": datetime.now(timezone.utc),
        }
        if error is not None:
            update_data["error"] = redact_sensitive_data(error)
        if completed_at is not None:
            update_data["completed_at"] = completed_at
            # Attempt to calculate duration
            try:
                current_doc = await self.get_execution(workspace_id, execution_id)
                if current_doc and "started_at" in current_doc:
                    st = current_doc["started_at"]
                    if isinstance(st, datetime):
                        delta = completed_at - st
                        update_data["duration_ms"] = max(0, int(delta.total_seconds() * 1000))
            except Exception:
                pass
        if temporal_workflow_id is not None:
            update_data["temporal_workflow_id"] = temporal_workflow_id
        if step_summary:
            clean_summary = redact_sensitive_data(step_summary)
            for k, v in clean_summary.items():
                update_data[f"steps.{k}"] = v

        try:
            res = await self.collection.update_one(
                {"_id": ObjectId(execution_id), "workspace_id": workspace_id},
                {"$set": update_data},
            )
            success = res.modified_count > 0

            # Notify real-time subscribers
            try:
                broadcaster = get_broadcaster()
                await broadcaster.broadcast(
                    workspace_id=workspace_id,
                    event_type="execution:updated",
                    data={
                        "execution_id": execution_id,
                        "status": status,
                        "error": update_data.get("error"),
                        "completed_at": completed_at.isoformat() if completed_at else None,
                        "duration_ms": update_data.get("duration_ms"),
                    },
                    execution_id=execution_id,
                )
            except Exception:
                pass

            return success
        except Exception as e:
            logger.error("Failed to update execution status", error=str(e), execution_id=execution_id)
            return False
        except Exception as e:
            logger.error("Failed to update execution status", error=str(e), execution_id=execution_id)
            return False

    # Step Execution Tracking & Idempotency
    async def get_step_by_idempotency_key(
        self,
        workspace_id: str,
        idempotency_key: str,
    ) -> Optional[Dict[str, Any]]:
        """Lookup previously executed step by idempotency key to prevent duplicate external actions."""
        return await self.step_executions.find_one({
            "workspace_id": workspace_id,
            "idempotency_key": idempotency_key,
        })

    async def get_step_execution(
        self,
        workspace_id: str,
        execution_id: str,
        step_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get latest step execution record for a step."""
        cursor = self.step_executions.find({
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "step_id": step_id,
        }).sort("attempt", -1).limit(1)
        res = await cursor.to_list(length=1)
        return res[0] if res else None

    async def list_step_executions(
        self,
        workspace_id: str,
        execution_id: str,
    ) -> List[Dict[str, Any]]:
        """List all step execution records for an execution."""
        cursor = self.step_executions.find({
            "workspace_id": workspace_id,
            "execution_id": execution_id,
        }).sort("started_at", 1)
        return await cursor.to_list(length=100)

    async def record_step_start(
        self,
        workspace_id: str,
        execution_id: str,
        workflow_id: str,
        workflow_version: int,
        step_id: str,
        step_name: str,
        attempt: int,
        idempotency_key: str,
        input_data: Dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> str:
        """Record the initiation of a step attempt with redaction and live broadcast."""
        now = datetime.now(timezone.utc)
        clean_input = redact_sensitive_data(input_data)
        doc = {
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "workflow_id": workflow_id,
            "workflow_version": workflow_version,
            "step_id": step_id,
            "step_name": step_name,
            "attempt": attempt,
            "status": StepExecutionStatus.RUNNING.value,
            "idempotency_key": idempotency_key,
            "correlation_id": correlation_id,
            "input_data": clean_input,
            "started_at": now,
        }
        res = await self.step_executions.insert_one(doc)
        step_rec_id = str(res.inserted_id)

        # Notify real-time subscribers
        try:
            broadcaster = get_broadcaster()
            await broadcaster.broadcast(
                workspace_id=workspace_id,
                event_type="step:started",
                data={
                    "step_id": step_id,
                    "step_name": step_name,
                    "attempt": attempt,
                    "status": StepExecutionStatus.RUNNING.value,
                    "execution_id": execution_id,
                    "started_at": now.isoformat(),
                },
                execution_id=execution_id,
            )
        except Exception:
            pass

        return step_rec_id

    async def record_step_completion(
        self,
        workspace_id: str,
        execution_id: str,
        step_id: str,
        attempt: int,
        output_data: Dict[str, Any],
        execution_time_ms: int,
        payload_ref: Optional[Dict[str, Any]] = None,
        status: StepExecutionStatus = StepExecutionStatus.COMPLETED,
        error: Optional[str] = None,
        skip_reason: Optional[str] = None,
    ) -> bool:
        """Mark a step attempt as completed, skipped, or failed with redaction and live broadcast."""
        now = datetime.now(timezone.utc)
        clean_output = redact_sensitive_data(output_data)
        status_val = status.value if hasattr(status, "value") else str(status)
        update_data: Dict[str, Any] = {
            "status": status_val,
            "output_data": clean_output,
            "execution_time_ms": execution_time_ms,
            "completed_at": now,
        }
        if payload_ref:
            update_data["payload_ref"] = redact_sensitive_data(payload_ref)
        if error:
            update_data["error"] = redact_sensitive_data(error)
        if skip_reason:
            update_data["skip_reason"] = redact_sensitive_data(skip_reason)

        res = await self.step_executions.update_one(
            {
                "workspace_id": workspace_id,
                "execution_id": execution_id,
                "step_id": step_id,
                "attempt": attempt,
            },
            {"$set": update_data},
            upsert=True,
        )
        success = res.modified_count > 0 or res.upserted_id is not None

        # Notify real-time subscribers
        try:
            broadcaster = get_broadcaster()
            await broadcaster.broadcast(
                workspace_id=workspace_id,
                event_type=f"step:{status_val}",
                data={
                    "step_id": step_id,
                    "attempt": attempt,
                    "status": status_val,
                    "execution_id": execution_id,
                    "execution_time_ms": execution_time_ms,
                    "error": update_data.get("error"),
                    "skip_reason": update_data.get("skip_reason"),
                    "completed_at": now.isoformat(),
                },
                execution_id=execution_id,
            )
        except Exception:
            pass

        return success

    # Large Payload Storage (Serializable Activity Data)
    async def store_payload(
        self,
        workspace_id: str,
        execution_id: str,
        payload_id: str,
        data: Any,
    ) -> PayloadReference:
        """Store large payload (> safe threshold) in MongoDB and return a lightweight reference."""
        now = datetime.now(timezone.utc)
        serialized = json.dumps(data, default=str)
        size_bytes = len(serialized.encode("utf-8"))
        preview = (serialized[:200] + "...") if len(serialized) > 200 else serialized

        doc = {
            "workspace_id": workspace_id,
            "execution_id": execution_id,
            "payload_id": payload_id,
            "data": data,
            "size_bytes": size_bytes,
            "created_at": now,
        }
        await self.payloads.update_one(
            {"workspace_id": workspace_id, "payload_id": payload_id},
            {"$set": doc},
            upsert=True,
        )

        return PayloadReference(
            payload_id=payload_id,
            storage="mongodb",
            size_bytes=size_bytes,
            content_type="application/json",
            preview=preview,
        )

    async def get_payload(self, workspace_id: str, payload_id: str) -> Optional[Any]:
        """Fetch externalized large payload by reference."""
        doc = await self.payloads.find_one({
            "workspace_id": workspace_id,
            "payload_id": payload_id,
        })
        return doc.get("data") if doc else None

    # Pinned Workflow Definition Lookup
    async def get_workflow_definition_for_execution(
        self,
        workspace_id: str,
        workflow_id: str,
        version: int,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve the exact immutable workflow definition for this execution version."""
        # 1. Check workflow_versions collection
        ver_doc = await self.workflow_versions.find_one({
            "workspace_id": workspace_id,
            "workflow_id": workflow_id,
            "version": version,
        })
        if ver_doc and "definition" in ver_doc:
            return ver_doc["definition"]

        # 2. Fallback to current workflow definition if versions matched or not yet branched
        wf_doc = await self.workflows.find_one({
            "_id": ObjectId(workflow_id) if ObjectId.is_valid(workflow_id) else workflow_id,
            "workspace_id": workspace_id,
        })
        if wf_doc and (wf_doc.get("active_version") == version or wf_doc.get("current_version") == version):
            return wf_doc.get("definition")

        return None
