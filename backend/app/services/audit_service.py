from typing import List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..models.audit import AuditLogResponse
from ..repositories.audit_repo import AuditRepository


class AuditService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.audit_repo = AuditRepository(db)

    async def list_workspace_logs(
        self,
        workspace_id: str,
        limit: int = 50,
        skip: int = 0,
        action: Optional[str] = None
    ) -> List[AuditLogResponse]:
        events = await self.audit_repo.list_workspace_events(
            workspace_id=workspace_id,
            limit=limit,
            skip=skip,
            action=action
        )
        return [
            AuditLogResponse(
                id=str(e["_id"]),
                workspace_id=e.get("workspace_id"),
                user_id=e.get("user_id"),
                user_email=e.get("user_email"),
                action=e["action"],
                resource_type=e.get("resource_type"),
                resource_id=e.get("resource_id"),
                details=e.get("details", {}),
                ip_address=e.get("ip_address"),
                created_at=e["created_at"]
            )
            for e in events
        ]
