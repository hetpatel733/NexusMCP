from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from ...db import get_db
from ...models.audit import AuditLogResponse
from ...models.workspace import WorkspaceRole
from ...security.dependencies import require_role
from ...services.audit_service import AuditService

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])


@router.get("", response_model=List[AuditLogResponse])
async def list_audit_logs(
    limit: int = Query(default=50, ge=1, le=100),
    skip: int = Query(default=0, ge=0),
    action: Optional[str] = Query(default=None),
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN])),
    database=Depends(get_db)
):
    """Retrieve audit logs scoped to the active workspace. Requires Owner or Admin role."""
    workspace, _ = workspace_and_role
    service = AuditService(database)
    return await service.list_workspace_logs(
        workspace_id=workspace["id"],
        limit=limit,
        skip=skip,
        action=action
    )
