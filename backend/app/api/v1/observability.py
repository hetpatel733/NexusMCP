"""Observability and Operations API Router for NexusMCP.

Endpoints:
- GET /observability/metrics/executions: Workflow execution statistics, duration, queue latency, trigger delays
- GET /observability/metrics/steps: Step activity durations, retry distributions, provider error breakdowns
- GET /observability/alerts: Active operational alerts (repeated failures, expired connections, worker heartbeat loss)
- POST /observability/heartbeat: Worker node heartbeat check-in
- GET /observability/dlq: List Dead Letter Queue failed events
- GET /observability/dlq/{dlq_id}: Get detailed failed event info with redacted payload
- POST /observability/dlq/{dlq_id}/replay: Trigger controlled replay of failed event
- POST /observability/dlq/{dlq_id}/discard: Discard failed event
- GET /observability/live: Server-Sent Events (SSE) live updates stream
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...db import get_db
from ...models.workspace import WorkspaceRole
from ...observability.alerts import AlertEvaluator, AlertItem
from ...observability.broadcaster import get_broadcaster
from ...observability.dlq_service import DeadLetterQueueService
from ...observability.metrics import MetricsService
from ...security.dependencies import get_current_workspace, require_role

router = APIRouter(prefix="/observability", tags=["Observability & Operations"])


class ReplayRequest(BaseModel):
    idempotency_key: Optional[str] = Field(default=None, description="Client-provided idempotency key for replay")
    use_latest_version: bool = Field(default=False, description="Replay against latest active version instead of original pinned version")


class WorkerHeartbeatRequest(BaseModel):
    worker_id: str
    task_queue: str = "nexusmcp-task-queue"
    metadata: Dict[str, Any] = Field(default_factory=dict)


@router.get("/metrics/executions")
async def get_execution_metrics(
    workflow_id: Optional[str] = Query(None),
    timeframe_hours: int = Query(24, ge=1, le=168),
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """Retrieve execution metrics: success/failure rates, duration, queue latency, and trigger delays."""
    workspace, _ = workspace_and_role
    service = MetricsService(database)
    return await service.get_execution_metrics(
        workspace_id=workspace["id"],
        workflow_id=workflow_id,
        timeframe_hours=timeframe_hours,
    )


@router.get("/metrics/steps")
async def get_step_metrics(
    workflow_id: Optional[str] = Query(None),
    timeframe_hours: int = Query(24, ge=1, le=168),
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """Retrieve step-level duration, retry distributions, and provider error breakdowns."""
    workspace, _ = workspace_and_role
    service = MetricsService(database)
    return await service.get_step_and_provider_metrics(
        workspace_id=workspace["id"],
        workflow_id=workflow_id,
        timeframe_hours=timeframe_hours,
    )


@router.get("/alerts", response_model=List[AlertItem])
async def get_active_alerts(
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """Evaluate and retrieve active operational alerts."""
    workspace, _ = workspace_and_role
    evaluator = AlertEvaluator(database)
    return await evaluator.evaluate_all_alerts(workspace_id=workspace["id"])


@router.post("/heartbeat", status_code=status.HTTP_200_OK)
async def worker_heartbeat(
    payload: WorkerHeartbeatRequest,
    database=Depends(get_db),
):
    """Internal/worker endpoint to record worker node liveness and heartbeats."""
    evaluator = AlertEvaluator(database)
    await evaluator.record_worker_heartbeat(
        worker_id=payload.worker_id,
        task_queue=payload.task_queue,
        metadata=payload.metadata,
    )
    return {"status": "ok", "worker_id": payload.worker_id}


# Dead Letter Queue Endpoints
@router.get("/dlq")
async def list_dlq_events(
    workflow_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN])),
    database=Depends(get_db),
):
    """List failed events in Dead Letter Queue."""
    workspace, _ = workspace_and_role
    dlq_service = DeadLetterQueueService(database)
    return await dlq_service.list_failed_events(
        workspace_id=workspace["id"],
        workflow_id=workflow_id,
        status=status_filter,
        limit=limit,
        skip=skip,
    )


@router.get("/dlq/{dlq_id}")
async def get_dlq_event(
    dlq_id: str,
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN])),
    database=Depends(get_db),
):
    """Inspect single failed event with redacted sensitive details."""
    workspace, _ = workspace_and_role
    dlq_service = DeadLetterQueueService(database)
    event = await dlq_service.get_failed_event(workspace_id=workspace["id"], dlq_id=dlq_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DLQ event not found")
    return event


@router.post("/dlq/{dlq_id}/replay", status_code=status.HTTP_202_ACCEPTED)
async def replay_dlq_event(
    dlq_id: str,
    payload: Optional[ReplayRequest] = None,
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN])),
    database=Depends(get_db),
):
    """Controlled, idempotent replay of a Dead Letter Queue failed event."""
    workspace, _ = workspace_and_role
    req = payload or ReplayRequest()
    dlq_service = DeadLetterQueueService(database)
    try:
        return await dlq_service.replay_failed_event(
            workspace_id=workspace["id"],
            dlq_id=dlq_id,
            idempotency_key=req.idempotency_key,
            use_latest_version=req.use_latest_version,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Replay failed: {str(e)}",
        )


@router.post("/dlq/{dlq_id}/discard")
async def discard_dlq_event(
    dlq_id: str,
    workspace_and_role=Depends(require_role([WorkspaceRole.OWNER, WorkspaceRole.ADMIN])),
    database=Depends(get_db),
):
    """Discard a failed event from DLQ."""
    workspace, _ = workspace_and_role
    dlq_service = DeadLetterQueueService(database)
    success = await dlq_service.discard_failed_event(workspace["id"], dlq_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="DLQ event not found or cannot be discarded")
    return {"status": "discarded", "dlq_id": dlq_id}


# Live Stream SSE Endpoint
@router.get("/live")
async def live_execution_stream(
    execution_id: Optional[str] = Query(None),
    workspace_and_role=Depends(get_current_workspace),
):
    """Server-Sent Events (SSE) live updates stream for workflow execution and step logs."""
    workspace, _ = workspace_and_role
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
