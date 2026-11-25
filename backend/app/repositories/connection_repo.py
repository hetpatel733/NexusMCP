"""Repository for third-party connection records.

All connection records are strictly tenant-scoped by ``workspace_id``
and ``user_id``. NexusMCP never stores plaintext provider OAuth tokens.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..models.connection import ConnectionStatus


class ConnectionRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection = db.connections

    async def create_connection(
        self,
        workspace_id: str,
        user_id: str,
        app_name: str,
        connection_id: str,
        status: ConnectionStatus = ConnectionStatus.CONNECTED,
        external_user_id: Optional[str] = None,
        expires_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Insert a new connection record."""
        now = datetime.now(timezone.utc)
        doc: Dict[str, Any] = {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "app_name": app_name,
            "connection_id": connection_id,
            "external_user_id": external_user_id or user_id,
            "status": status.value,
            "connected_at": now if status == ConnectionStatus.CONNECTED else None,
            "expires_at": expires_at,
            "created_at": now,
            "updated_at": now,
        }
        res = await self.collection.insert_one(doc)
        doc["_id"] = res.inserted_id
        return doc

    async def get_by_id_and_workspace(
        self,
        workspace_id: str,
        connection_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a connection strictly scoped to its workspace."""
        try:
            return await self.collection.find_one({
                "_id": ObjectId(connection_id),
                "workspace_id": workspace_id,
            })
        except Exception:
            return None

    async def get_by_connection_id_and_workspace(
        self,
        workspace_id: str,
        provider_connection_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a connection by its provider connection id within a workspace."""
        return await self.collection.find_one({
            "workspace_id": workspace_id,
            "connection_id": provider_connection_id,
        })

    async def list_by_workspace(
        self,
        workspace_id: str,
        user_id: Optional[str] = None,
        app_name: Optional[str] = None,
        status: Optional[ConnectionStatus] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        """List connections for a workspace, optionally filtered by user/app/status."""
        query: Dict[str, Any] = {"workspace_id": workspace_id}
        if user_id:
            query["user_id"] = user_id
        if app_name:
            query["app_name"] = app_name
        if status:
            query["status"] = status.value

        cursor = self.collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
        return await cursor.to_list(length=limit)

    async def update_status(
        self,
        workspace_id: str,
        connection_id: str,
        status: ConnectionStatus,
        expires_at: Optional[datetime] = None,
    ) -> bool:
        """Update a connection's status within its workspace."""
        update_data: Dict[str, Any] = {
            "status": status.value,
            "updated_at": datetime.now(timezone.utc),
        }
        if status == ConnectionStatus.CONNECTED:
            update_data["connected_at"] = datetime.now(timezone.utc)
        if expires_at is not None:
            update_data["expires_at"] = expires_at

        try:
            result = await self.collection.update_one(
                {"_id": ObjectId(connection_id), "workspace_id": workspace_id},
                {"$set": update_data},
            )
            return result.modified_count > 0
        except Exception:
            return False

    async def delete_by_workspace(
        self,
        workspace_id: str,
        connection_id: str,
    ) -> bool:
        """Delete a connection strictly scoped to its workspace."""
        try:
            result = await self.collection.delete_one({
                "_id": ObjectId(connection_id),
                "workspace_id": workspace_id,
            })
            return result.deleted_count > 0
        except Exception:
            return False

    async def count_by_workspace_and_app(
        self,
        workspace_id: str,
        user_id: str,
        app_name: str,
    ) -> int:
        """Count existing connections for a workspace/user/app combination."""
        return await self.collection.count_documents({
            "workspace_id": workspace_id,
            "user_id": user_id,
            "app_name": app_name,
        })
