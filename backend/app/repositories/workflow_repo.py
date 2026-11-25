from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase

from .base import BaseRepository


class WorkflowRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.workflows)
        self.generations_collection: AsyncIOMotorCollection = db.workflow_generations
        self.versions_collection: AsyncIOMotorCollection = db.workflow_versions
        self.executions_collection: AsyncIOMotorCollection = db.workflow_executions

    async def get_by_name(self, workspace_id: str, name: str) -> Optional[Dict[str, Any]]:
        return await self.collection.find_one({"workspace_id": workspace_id, "name": name})

    async def list_active(self, workspace_id: str) -> List[Dict[str, Any]]:
        cursor = self.collection.find({"workspace_id": workspace_id, "status": "active"})
        return await cursor.to_list(length=100)

    async def get_workflow(self, workspace_id: str, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a workflow ensuring it belongs to the given workspace."""
        return await self.get_by_workspace_and_id(workspace_id, workflow_id)

    async def update_workflow(self, workspace_id: str, workflow_id: str, update_data: Dict[str, Any]) -> bool:
        """Update a workflow ensuring it belongs to the given workspace."""
        return await self.update_by_workspace(workspace_id, workflow_id, update_data)

    async def delete_workflow(self, workspace_id: str, workflow_id: str) -> bool:
        """Delete a workflow ensuring it belongs to the given workspace."""
        return await self.delete_by_workspace(workspace_id, workflow_id)

    # Workflow Versioning Persistence (Requirements 6 & 7)
    async def create_version(self, record: Dict[str, Any]) -> str:
        """Persist an immutable workflow version record."""
        now = datetime.now(timezone.utc)
        record["created_at"] = record.get("created_at", now)
        res = await self.versions_collection.insert_one(record)
        return str(res.inserted_id)

    async def get_version(self, workspace_id: str, workflow_id: str, version: int) -> Optional[Dict[str, Any]]:
        """Fetch an exact immutable version of a workflow."""
        return await self.versions_collection.find_one({
            "workspace_id": workspace_id,
            "workflow_id": workflow_id,
            "version": version,
        })

    async def list_versions(self, workspace_id: str, workflow_id: str) -> List[Dict[str, Any]]:
        """List all version history records for a workflow, descending by version."""
        cursor = self.versions_collection.find({
            "workspace_id": workspace_id,
            "workflow_id": workflow_id,
        }).sort("version", -1)
        return await cursor.to_list(length=100)

    # Workflow Execution Records (Requirement 7)
    async def create_execution(self, record: Dict[str, Any]) -> str:
        """Create an execution record pinned to a specific workflow version."""
        now = datetime.now(timezone.utc)
        record["started_at"] = record.get("started_at", now)
        res = await self.executions_collection.insert_one(record)
        return str(res.inserted_id)

    async def get_execution(self, workspace_id: str, execution_id: str) -> Optional[Dict[str, Any]]:
        """Fetch execution record strictly scoped to workspace."""
        try:
            return await self.executions_collection.find_one({
                "_id": ObjectId(execution_id),
                "workspace_id": workspace_id,
            })
        except Exception:
            return None

    async def list_executions(self, workspace_id: str, workflow_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """List executions scoped to workspace and optionally workflow_id."""
        query: Dict[str, Any] = {"workspace_id": workspace_id}
        if workflow_id:
            query["workflow_id"] = workflow_id
        cursor = self.executions_collection.find(query).sort("started_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def update_execution(self, workspace_id: str, execution_id: str, update_data: Dict[str, Any]) -> bool:
        """Update execution state within workspace boundaries."""
        try:
            res = await self.executions_collection.update_one(
                {"_id": ObjectId(execution_id), "workspace_id": workspace_id},
                {"$set": update_data},
            )
            return res.modified_count > 0
        except Exception:
            return False

    # Workflow Generation Metadata Persistence (Requirement 8)
    async def record_generation(self, record: Dict[str, Any]) -> str:
        """Persist generation session metadata, prompts, schema version, model info."""
        now = datetime.now(timezone.utc)
        record["created_at"] = now
        res = await self.generations_collection.insert_one(record)
        return str(res.inserted_id)

    async def get_generation(self, workspace_id: str, generation_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve workflow generation record strictly scoped to workspace."""
        try:
            return await self.generations_collection.find_one({
                "_id": ObjectId(generation_id),
                "workspace_id": workspace_id,
            })
        except Exception:
            return None

    async def list_generations(self, workspace_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """List generation records for a workspace."""
        cursor = self.generations_collection.find({"workspace_id": workspace_id}).sort("created_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

