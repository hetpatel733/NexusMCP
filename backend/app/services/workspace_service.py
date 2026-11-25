import re
import uuid
from typing import List, Optional

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..models.audit import AuditAction
from ..models.workspace import (
    AddMemberRequest,
    WorkspaceCreate,
    WorkspaceMemberResponse,
    WorkspaceResponse,
    WorkspaceRole,
    WorkspaceUpdate,
)
from ..repositories.audit_repo import AuditRepository
from ..repositories.user_repo import UserRepository
from ..repositories.workspace_repo import WorkspaceRepository


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-") or "workspace"


class WorkspaceService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.workspace_repo = WorkspaceRepository(db)
        self.user_repo = UserRepository(db)
        self.audit_repo = AuditRepository(db)

    async def create_workspace(
        self,
        user_id: str,
        user_email: str,
        data: WorkspaceCreate,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> WorkspaceResponse:
        slug = data.slug or slugify(data.name)
        existing = await self.workspace_repo.get_by_slug(slug)
        if existing:
            # Append unique suffix
            slug = f"{slug}-{uuid.uuid4().hex[:6]}"

        workspace = await self.workspace_repo.create_workspace(
            name=data.name,
            slug=slug,
            description=data.description,
            created_by=user_id
        )
        ws_id = str(workspace["_id"])

        await self.audit_repo.record_event(
            action=AuditAction.WORKSPACE_CREATE,
            workspace_id=ws_id,
            user_id=user_id,
            user_email=user_email,
            resource_type="workspace",
            resource_id=ws_id,
            details={"name": data.name, "slug": slug},
            ip_address=ip_address,
            user_agent=user_agent
        )

        return WorkspaceResponse(
            id=ws_id,
            name=workspace["name"],
            slug=workspace["slug"],
            description=workspace.get("description"),
            created_by=user_id,
            created_at=workspace["created_at"],
            role=WorkspaceRole.OWNER
        )

    async def update_workspace(
        self,
        workspace_id: str,
        actor_id: str,
        actor_email: str,
        data: WorkspaceUpdate,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> WorkspaceResponse:
        ws = await self.workspace_repo.get_by_id(workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found")

        updated = await self.workspace_repo.update_workspace_details(
            workspace_id=workspace_id,
            name=data.name,
            description=data.description
        )
        if not updated and (data.name or data.description):
            pass

        await self.audit_repo.record_event(
            action=AuditAction.WORKSPACE_UPDATE,
            workspace_id=workspace_id,
            user_id=actor_id,
            user_email=actor_email,
            resource_type="workspace",
            resource_id=workspace_id,
            details={"updated": {k: v for k, v in data.model_dump().items() if v is not None}},
            ip_address=ip_address,
            user_agent=user_agent
        )

        refreshed = await self.workspace_repo.get_by_id(workspace_id)
        assert refreshed is not None
        membership = await self.workspace_repo.get_membership(workspace_id, actor_id)

        return WorkspaceResponse(
            id=str(refreshed["_id"]),
            name=refreshed["name"],
            slug=refreshed["slug"],
            description=refreshed.get("description"),
            created_by=refreshed["created_by"],
            created_at=refreshed["created_at"],
            role=WorkspaceRole(membership["role"]) if membership else None
        )

    async def delete_workspace(
        self,
        workspace_id: str,
        actor_id: str,
        actor_email: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> bool:
        ws = await self.workspace_repo.get_by_id(workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found")

        # Record audit log before deletion
        await self.audit_repo.record_event(
            action=AuditAction.WORKSPACE_DELETE,
            workspace_id=workspace_id,
            user_id=actor_id,
            user_email=actor_email,
            resource_type="workspace",
            resource_id=workspace_id,
            details={"name": ws["name"], "slug": ws["slug"]},
            ip_address=ip_address,
            user_agent=user_agent
        )

        return await self.workspace_repo.delete_workspace(workspace_id)

    async def list_user_workspaces(self, user_id: str) -> List[WorkspaceResponse]:
        raw_workspaces = await self.workspace_repo.list_user_workspaces(user_id)
        return [
            WorkspaceResponse(
                id=str(ws["_id"]),
                name=ws["name"],
                slug=ws["slug"],
                description=ws.get("description"),
                created_by=ws["created_by"],
                created_at=ws["created_at"],
                role=WorkspaceRole(ws["role"])
            )
            for ws in raw_workspaces
        ]

    async def list_members(self, workspace_id: str) -> List[WorkspaceMemberResponse]:
        members = await self.workspace_repo.list_workspace_members(workspace_id)
        return [
            WorkspaceMemberResponse(
                id=m["id"],
                workspace_id=m["workspace_id"],
                user_id=m["user_id"],
                email=m["email"],
                full_name=m["full_name"],
                role=WorkspaceRole(m["role"]),
                joined_at=m["joined_at"]
            )
            for m in members
        ]

    async def add_member(
        self,
        workspace_id: str,
        actor_id: str,
        actor_email: str,
        data: AddMemberRequest,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> WorkspaceMemberResponse:
        invited_user = await self.user_repo.get_by_email(data.email)
        if not invited_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with email '{data.email}' was not found. They must register first.",
            )

        invited_user_id = str(invited_user["_id"])
        existing_membership = await self.workspace_repo.get_membership(workspace_id, invited_user_id)
        if existing_membership:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User is already a member of this workspace.",
            )

        member_doc = await self.workspace_repo.add_member(
            workspace_id=workspace_id,
            user_id=invited_user_id,
            role=data.role.value
        )

        await self.audit_repo.record_event(
            action=AuditAction.WORKSPACE_MEMBER_ADD,
            workspace_id=workspace_id,
            user_id=actor_id,
            user_email=actor_email,
            resource_type="workspace_member",
            resource_id=invited_user_id,
            details={"invited_email": data.email, "role": data.role.value},
            ip_address=ip_address,
            user_agent=user_agent
        )

        return WorkspaceMemberResponse(
            id=str(member_doc["_id"]),
            workspace_id=workspace_id,
            user_id=invited_user_id,
            email=invited_user["email"],
            full_name=invited_user["full_name"],
            role=data.role,
            joined_at=member_doc["joined_at"]
        )

    async def update_member_role(
        self,
        workspace_id: str,
        actor_id: str,
        actor_email: str,
        target_user_id: str,
        new_role: WorkspaceRole,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> bool:
        membership = await self.workspace_repo.get_membership(workspace_id, target_user_id)
        if not membership:
            raise HTTPException(status_code=404, detail="Member not found in this workspace")

        # Check if demoting the sole owner
        if membership["role"] == WorkspaceRole.OWNER.value and new_role != WorkspaceRole.OWNER:
            owner_count = await self.workspace_repo.count_owners(workspace_id)
            if owner_count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot demote the sole owner of the workspace. Promote another member to owner first.",
                )

        updated = await self.workspace_repo.update_member_role(
            workspace_id=workspace_id,
            user_id=target_user_id,
            role=new_role.value
        )

        await self.audit_repo.record_event(
            action=AuditAction.WORKSPACE_MEMBER_ROLE_UPDATE,
            workspace_id=workspace_id,
            user_id=actor_id,
            user_email=actor_email,
            resource_type="workspace_member",
            resource_id=target_user_id,
            details={"old_role": membership["role"], "new_role": new_role.value},
            ip_address=ip_address,
            user_agent=user_agent
        )

        return updated

    async def remove_member(
        self,
        workspace_id: str,
        actor_id: str,
        actor_email: str,
        target_user_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> bool:
        membership = await self.workspace_repo.get_membership(workspace_id, target_user_id)
        if not membership:
            raise HTTPException(status_code=404, detail="Member not found in this workspace")

        # Check if removing the sole owner
        if membership["role"] == WorkspaceRole.OWNER.value:
            owner_count = await self.workspace_repo.count_owners(workspace_id)
            if owner_count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot remove the sole owner of the workspace. Transfer ownership first.",
                )

        removed = await self.workspace_repo.remove_member(workspace_id, target_user_id)

        await self.audit_repo.record_event(
            action=AuditAction.WORKSPACE_MEMBER_REMOVE,
            workspace_id=workspace_id,
            user_id=actor_id,
            user_email=actor_email,
            resource_type="workspace_member",
            resource_id=target_user_id,
            details={"removed_user_id": target_user_id},
            ip_address=ip_address,
            user_agent=user_agent
        )

        return removed
