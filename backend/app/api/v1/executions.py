"""Executions API Router for NexusMCP.

Provides tenant-isolated endpoints for:
- Manual workflow triggering and dispatch
- Listing and inspecting workflow executions
- Retrieving step-by-step trace and attempt logs
- Cancelling in-flight executions
- Inspecting externalized large payloads
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ...db import get_db
from ...execution.service import ExecutionService
from ...models.workspace import WorkspaceRole
from ...security.dependencies import get_current_user, get_current_workspace, require_role

router = APIRouter(tags=["Executions"])


class ManualExecutionRequest(BaseModel):
    trigger_data: Dict[str, Any] = Field(default_factory=dict, description="Custom payload or input variables for trigger")
    version: Optional[int] = Field(default=None, description="Optional explicit version to execute (defaults to active_version)")


class ExecutionSummaryResponse(BaseModel):
    id: str
    workspace_id: str
    workflow_id: str
    workflow_version: int
    status: str
    triggered_by: str
    trigger_event: Dict[str, Any] = Field(default_factory=dict)
    started_at: Any
    completed_at: Optional[Any] = None
    error: Optional[str] = None
    steps: Optional[Dict[str, Any]] = None


@router.post("/workflows/{workflow_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_workflow(
    workflow_id: str,
    payload: Optional[ManualExecutionRequest] = None,
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.MEMBER])),
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db),
):
    """Trigger a manual background execution of an active workflow."""
    workspace, _ = workspace_and_role
    service = ExecutionService(database)
    req = payload or ManualExecutionRequest()

    try:
        res = await service.create_and_dispatch_execution(
            workspace_id=workspace["id"],
            workflow_id=workflow_id,
            triggered_by="manual",
            trigger_event={
                "event_id": f"manual-{current_user.get('id', 'user')}",
                "event_type": "manual_trigger",
                "provider": "manual",
                "data": req.trigger_data,
            },
            workflow_version=req.version,
            user_id=current_user.get("id"),
            user_role=str(workspace_and_role[1]),
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to dispatch workflow execution: {str(e)}",
        )


@router.get("/workflows/{workflow_id}/executions")
async def list_workflow_executions(
    workflow_id: str,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """List executions for a specific workflow."""
    workspace, _ = workspace_and_role
    service = ExecutionService(database)
    return await service.list_executions(
        workspace_id=workspace["id"],
        workflow_id=workflow_id,
        status=status_filter,
        limit=limit,
        skip=skip,
    )


@router.get("/executions")
async def list_all_executions(
    workflow_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """List all workflow executions within the current workspace."""
    workspace, _ = workspace_and_role
    service = ExecutionService(database)
    return await service.list_executions(
        workspace_id=workspace["id"],
        workflow_id=workflow_id,
        status=status_filter,
        limit=limit,
        skip=skip,
    )


@router.get("/executions/{execution_id}")
async def get_execution_details(
    execution_id: str,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """Get single execution details with pinned version and step status."""
    workspace, _ = workspace_and_role
    service = ExecutionService(database)
    execution = await service.get_execution(workspace["id"], execution_id)
    if not execution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
    return execution


@router.get("/executions/{execution_id}/steps")
async def get_execution_steps(
    execution_id: str,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """Get step attempt history, logs, outputs, and idempotency status for an execution."""
    workspace, _ = workspace_and_role
    service = ExecutionService(database)
    # Ensure execution belongs to workspace
    execution = await service.get_execution(workspace["id"], execution_id)
    if not execution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")

    return await service.list_step_executions(workspace["id"], execution_id)


@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(
    execution_id: str,
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN])),
    database=Depends(get_db),
):
    """Cancel an in-flight workflow execution."""
    workspace, _ = workspace_and_role
    service = ExecutionService(database)
    execution = await service.get_execution(workspace["id"], execution_id)
    if not execution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")

    success = await service.cancel_execution(
        workspace_id=workspace["id"],
        workflow_id=execution["workflow_id"],
        execution_id=execution_id,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not cancel execution",
        )
    return {"message": "Execution cancelled successfully", "execution_id": execution_id}


@router.get("/executions/{execution_id}/live")
async def stream_execution_live(
    execution_id: str,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """Server-Sent Events (SSE) live updates stream for this specific execution."""
    from fastapi.responses import StreamingResponse
    from ...observability.broadcaster import get_broadcaster

    workspace, _ = workspace_and_role
    service = ExecutionService(database)
    execution = await service.get_execution(workspace["id"], execution_id)
    if not execution:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")

    broadcaster = get_broadcaster()
    return StreamingResponse(
        broadcaster.event_generator(workspace_id=workspace["id"], execution_id=execution_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )



@router.get("/executions/payloads/{payload_id}")
async def get_execution_payload(
    payload_id: str,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """Inspect externalized large payload by payload_id."""
    workspace, _ = workspace_and_role
    service = ExecutionService(database)
    data = await service.get_payload(workspace["id"], payload_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payload not found")
    return {"payload_id": payload_id, "data": data}
