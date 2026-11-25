from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ...db import get_db
from ...models.workspace import (
    AddMemberRequest,
    UpdateMemberRoleRequest,
    WorkspaceCreate,
    WorkspaceMemberResponse,
    WorkspaceResponse,
    WorkspaceRole,
    WorkspaceUpdate,
)
from ...repositories.workspace_repo import WorkspaceRepository
from ...security.dependencies import get_current_user
from ...services.workspace_service import WorkspaceService

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


def get_client_info(request: Request) -> tuple[Optional[str], Optional[str]]:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


async def get_membership_or_403(
    workspace_id: str,
    user_id: str,
    database
) -> Dict[str, Any]:
    repo = WorkspaceRepository(database)
    membership = await repo.get_membership(workspace_id, user_id)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: You are not a member of this workspace",
        )
    return membership


@router.get("", response_model=List[WorkspaceResponse])
async def list_workspaces(
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """List all workspaces that the current user belongs to."""
    service = WorkspaceService(database)
    return await service.list_user_workspaces(current_user["id"])


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    data: WorkspaceCreate,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Create a new workspace with the authenticated user as Owner."""
    ip, ua = get_client_info(request)
    service = WorkspaceService(database)
    return await service.create_workspace(
        user_id=current_user["id"],
        user_email=current_user["email"],
        data=data,
        ip_address=ip,
        user_agent=ua
    )


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Get details of a specific workspace, verifying membership."""
    membership = await get_membership_or_403(workspace_id, current_user["id"], database)
    repo = WorkspaceRepository(database)
    ws = await repo.get_by_id(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return WorkspaceResponse(
        id=str(ws["_id"]),
        name=ws["name"],
        slug=ws["slug"],
        description=ws.get("description"),
        created_by=ws["created_by"],
        created_at=ws["created_at"],
        role=WorkspaceRole(membership["role"])
    )


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: str,
    data: WorkspaceUpdate,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Update workspace details. Requires OWNER or ADMIN role."""
    membership = await get_membership_or_403(workspace_id, current_user["id"], database)
    if membership["role"] not in [WorkspaceRole.OWNER.value, WorkspaceRole.ADMIN.value]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Updating workspace requires Owner or Admin role",
        )
    ip, ua = get_client_info(request)
    service = WorkspaceService(database)
    return await service.update_workspace(
        workspace_id=workspace_id,
        actor_id=current_user["id"],
        actor_email=current_user["email"],
        data=data,
        ip_address=ip,
        user_agent=ua
    )


@router.delete("/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Delete a workspace and all its data. Requires OWNER role."""
    membership = await get_membership_or_403(workspace_id, current_user["id"], database)
    if membership["role"] != WorkspaceRole.OWNER.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only workspace Owners can delete a workspace",
        )
    ip, ua = get_client_info(request)
    service = WorkspaceService(database)
    await service.delete_workspace(
        workspace_id=workspace_id,
        actor_id=current_user["id"],
        actor_email=current_user["email"],
        ip_address=ip,
        user_agent=ua
    )
    return {"message": "Workspace successfully deleted"}


@router.get("/{workspace_id}/members", response_model=List[WorkspaceMemberResponse])
async def list_members(
    workspace_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """List all members of a workspace."""
    await get_membership_or_403(workspace_id, current_user["id"], database)
    service = WorkspaceService(database)
    return await service.list_members(workspace_id)


@router.post("/{workspace_id}/members", response_model=WorkspaceMemberResponse, status_code=status.HTTP_201_CREATED)
async def add_member(
    workspace_id: str,
    data: AddMemberRequest,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Add or invite a member to the workspace. Requires OWNER or ADMIN role."""
    membership = await get_membership_or_403(workspace_id, current_user["id"], database)
    if membership["role"] not in [WorkspaceRole.OWNER.value, WorkspaceRole.ADMIN.value]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only Owner or Admin can add members",
        )
    # Admins cannot add an Owner
    if membership["role"] == WorkspaceRole.ADMIN.value and data.role == WorkspaceRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Admins cannot assign Owner role",
        )

    ip, ua = get_client_info(request)
    service = WorkspaceService(database)
    return await service.add_member(
        workspace_id=workspace_id,
        actor_id=current_user["id"],
        actor_email=current_user["email"],
        data=data,
        ip_address=ip,
        user_agent=ua
    )


@router.patch("/{workspace_id}/members/{target_user_id}")
async def update_member_role(
    workspace_id: str,
    target_user_id: str,
    data: UpdateMemberRoleRequest,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Update role of an existing member. Requires OWNER role."""
    membership = await get_membership_or_403(workspace_id, current_user["id"], database)
    if membership["role"] != WorkspaceRole.OWNER.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only workspace Owner can modify member roles",
        )

    ip, ua = get_client_info(request)
    service = WorkspaceService(database)
    await service.update_member_role(
        workspace_id=workspace_id,
        actor_id=current_user["id"],
        actor_email=current_user["email"],
        target_user_id=target_user_id,
        new_role=data.role,
        ip_address=ip,
        user_agent=ua
    )
    return {"message": "Member role updated successfully"}


@router.delete("/{workspace_id}/members/{target_user_id}")
async def remove_member(
    workspace_id: str,
    target_user_id: str,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Remove a member from the workspace. Requires OWNER or ADMIN (or self-removal)."""
    actor_membership = await get_membership_or_403(workspace_id, current_user["id"], database)
    actor_role = actor_membership["role"]
    is_self = current_user["id"] == target_user_id

    if not is_self and actor_role not in [WorkspaceRole.OWNER.value, WorkspaceRole.ADMIN.value]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Insufficient permissions to remove member",
        )

    # Admin cannot remove an Owner
    if not is_self and actor_role == WorkspaceRole.ADMIN.value:
        repo = WorkspaceRepository(database)
        target_membership = await repo.get_membership(workspace_id, target_user_id)
        if target_membership and target_membership["role"] == WorkspaceRole.OWNER.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Admins cannot remove workspace Owners",
            )

    ip, ua = get_client_info(request)
    service = WorkspaceService(database)
    await service.remove_member(
        workspace_id=workspace_id,
        actor_id=current_user["id"],
        actor_email=current_user["email"],
        target_user_id=target_user_id,
        ip_address=ip,
        user_agent=ua
    )
    return {"message": "Member removed successfully"}
