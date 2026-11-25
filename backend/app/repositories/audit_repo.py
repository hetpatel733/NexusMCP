from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..security.redaction import redact_sensitive_data
from .base import BaseRepository


class AuditRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.audit_logs)

    async def record_event(
        self,
        action: str,
        workspace_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_email: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> str:
        now = datetime.now(timezone.utc)
        clean_details = redact_sensitive_data(details or {})
        doc = {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "user_email": user_email,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": clean_details,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "created_at": now,
        }
        res = await self.collection.insert_one(doc)
        return str(res.inserted_id)

    async def list_workspace_events(
        self,
        workspace_id: str,
        limit: int = 50,
        skip: int = 0,
        action: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {"workspace_id": workspace_id}
        if action:
            query["action"] = action
        cursor = self.collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
        return await cursor.to_list(length=limit)

    async def list_user_events(
        self,
        user_id: str,
        limit: int = 50,
        skip: int = 0
    ) -> List[Dict[str, Any]]:
        cursor = self.collection.find({"user_id": user_id}).sort("created_at", -1).skip(skip).limit(limit)
        return await cursor.to_list(length=limit)
