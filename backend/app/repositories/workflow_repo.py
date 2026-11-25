from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase

from .base import BaseRepository


class WorkflowRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.workflows)
        self.generations_collection: AsyncIOMotorCollection = db.workflow_generations

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

    # Workflow Generation Metadata Persistence (Requirement 8)
    async def record_generation(self, record: Dict[str, Any]) -> str:
        """Persist generation session metadata, prompts, schema version, model info."""
        now = datetime.now(timezone.utc)
        record["created_at"] = now
        res = await self.generations_collection.insert_one(record)
        return str(res.inserted_id)

    async def get_generation(self, workspace_id: str, generation_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve workflow generation record strictly scoped to workspace."""
        from bson import ObjectId
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
