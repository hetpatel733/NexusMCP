from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from .base import BaseRepository


class WorkspaceRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.workspaces)
        self.db = db
        self.memberships = db.memberships
        self.users = db.users

    async def get_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        return await self.collection.find_one({"slug": slug})

    async def create_workspace(
        self,
        name: str,
        slug: str,
        description: Optional[str],
        created_by: str
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        doc = {
            "name": name.strip(),
            "slug": slug.strip().lower(),
            "description": description.strip() if description else None,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
        res = await self.collection.insert_one(doc)
        doc["_id"] = res.inserted_id

        # Automatically add the creator as OWNER
        await self.add_member(
            workspace_id=str(res.inserted_id),
            user_id=created_by,
            role="owner"
        )
        return doc

    async def update_workspace_details(
        self,
        workspace_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None
    ) -> bool:
        update_data: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
        if name is not None:
            update_data["name"] = name.strip()
        if description is not None:
            update_data["description"] = description.strip()

        try:
            res = await self.collection.update_one(
                {"_id": ObjectId(workspace_id)},
                {"$set": update_data}
            )
            return res.modified_count > 0
        except Exception:
            return False

    async def delete_workspace(self, workspace_id: str) -> bool:
        try:
            # Delete workspace document
            res = await self.collection.delete_one({"_id": ObjectId(workspace_id)})
            if res.deleted_count > 0:
                # Clean up memberships and workflows
                await self.memberships.delete_many({"workspace_id": workspace_id})
                await self.db.workflows.delete_many({"workspace_id": workspace_id})
                await self.db.audit_logs.delete_many({"workspace_id": workspace_id})
                return True
            return False
        except Exception:
            return False

    async def add_member(self, workspace_id: str, user_id: str, role: str) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        doc = {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "role": role,
            "joined_at": now,
        }
        res = await self.memberships.insert_one(doc)
        doc["_id"] = res.inserted_id
        return doc

    async def get_membership(self, workspace_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        return await self.memberships.find_one({
            "workspace_id": workspace_id,
            "user_id": user_id
        })

    async def update_member_role(self, workspace_id: str, user_id: str, role: str) -> bool:
        res = await self.memberships.update_one(
            {"workspace_id": workspace_id, "user_id": user_id},
            {"$set": {"role": role, "updated_at": datetime.now(timezone.utc)}}
        )
        return res.modified_count > 0

    async def remove_member(self, workspace_id: str, user_id: str) -> bool:
        res = await self.memberships.delete_one({
            "workspace_id": workspace_id,
            "user_id": user_id
        })
        return res.deleted_count > 0

    async def count_owners(self, workspace_id: str) -> int:
        return await self.memberships.count_documents({
            "workspace_id": workspace_id,
            "role": "owner"
        })

    async def list_user_workspaces(self, user_id: str) -> List[Dict[str, Any]]:
        cursor = self.memberships.find({"user_id": user_id})
        memberships = await cursor.to_list(length=100)
        workspaces = []
        for mem in memberships:
            ws_id = mem["workspace_id"]
            try:
                ws = await self.collection.find_one({"_id": ObjectId(ws_id)})
                if ws:
                    ws["role"] = mem["role"]
                    workspaces.append(ws)
            except Exception:
                continue
        return workspaces

    async def list_workspace_members(self, workspace_id: str) -> List[Dict[str, Any]]:
        cursor = self.memberships.find({"workspace_id": workspace_id})
        memberships = await cursor.to_list(length=200)
        results = []
        for mem in memberships:
            user_id = mem["user_id"]
            user = None
            try:
                user = await self.users.find_one({"_id": ObjectId(user_id)})
            except Exception:
                pass
            results.append({
                "id": str(mem["_id"]),
                "workspace_id": workspace_id,
                "user_id": user_id,
                "email": user["email"] if user else "unknown",
                "full_name": user["full_name"] if user else "Unknown User",
                "role": mem["role"],
                "joined_at": mem.get("joined_at", datetime.now(timezone.utc)),
            })
        return results
