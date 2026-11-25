from typing import Any, Dict, List, Optional, TypeVar

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection

T = TypeVar("T")

class BaseRepository:
    def __init__(self, collection: AsyncIOMotorCollection):
        self.collection = collection

    async def get_by_id(self, id_str: str) -> Optional[Dict[str, Any]]:
        try:
            return await self.collection.find_one({"_id": ObjectId(id_str)})
        except Exception:
            return None

    async def get_by_workspace_and_id(self, workspace_id: str, id_str: str) -> Optional[Dict[str, Any]]:
        """Strict tenant-isolated lookup by workspace_id and document _id."""
        try:
            return await self.collection.find_one({
                "_id": ObjectId(id_str),
                "workspace_id": workspace_id
            })
        except Exception:
            return None

    async def list_by_workspace(self, workspace_id: str, limit: int = 50, skip: int = 0) -> List[Dict[str, Any]]:
        cursor = self.collection.find({"workspace_id": workspace_id}).skip(skip).limit(limit)
        return await cursor.to_list(length=limit)

    async def create(self, document: Dict[str, Any]) -> str:
        result = await self.collection.insert_one(document)
        return str(result.inserted_id)

    async def update(self, id_str: str, update_data: Dict[str, Any]) -> bool:
        try:
            result = await self.collection.update_one(
                {"_id": ObjectId(id_str)},
                {"$set": update_data}
            )
            return result.modified_count > 0
        except Exception:
            return False

    async def update_by_workspace(self, workspace_id: str, id_str: str, update_data: Dict[str, Any]) -> bool:
        """Strict tenant-isolated update scoped by workspace_id and document _id."""
        try:
            result = await self.collection.update_one(
                {"_id": ObjectId(id_str), "workspace_id": workspace_id},
                {"$set": update_data}
            )
            return result.modified_count > 0
        except Exception:
            return False

    async def delete(self, id_str: str) -> bool:
        try:
            result = await self.collection.delete_one({"_id": ObjectId(id_str)})
            return result.deleted_count > 0
        except Exception:
            return False

    async def delete_by_workspace(self, workspace_id: str, id_str: str) -> bool:
        """Strict tenant-isolated deletion scoped by workspace_id and document _id."""
        try:
            result = await self.collection.delete_one({
                "_id": ObjectId(id_str),
                "workspace_id": workspace_id
            })
            return result.deleted_count > 0
        except Exception:
            return False
