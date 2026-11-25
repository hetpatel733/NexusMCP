"""API endpoints for Phase 7 Trigger Management and Event Ingestion.

Exposes:
- Public/Provider Webhook Ingress: `POST /api/v1/triggers/webhook/{workspace_id}/{workflow_id}`
- Trigger Event History: `GET /api/v1/triggers/events`
- Trigger Subscription details: `GET /api/v1/triggers/subscriptions/{workflow_id}`
- On-demand Trigger Evaluation (Polling/Schedule check): `POST /api/v1/triggers/evaluate/{workflow_id}`
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Header, Request, status

from ...db import get_db
from ...logger import logger
from ...models.trigger import WebhookIngressResponse
from ...repositories.trigger_repo import TriggerRepository
from ...security.dependencies import get_current_user, get_current_workspace
from ...triggers.adapters.webhook import WebhookVerificationError
from ...triggers.polling_engine import PollingEngine
from ...triggers.scheduler import ScheduleEngine
from ...triggers.service import TriggerIngestionService

router = APIRouter(prefix="/triggers", tags=["Triggers"])


@router.post(
    "/webhook/{workspace_id}/{workflow_id}",
    response_model=WebhookIngressResponse,
    status_code=status.HTTP_200_OK,
)
async def handle_webhook_ingress(
    workspace_id: str,
    workflow_id: str,
    request: Request,
    database=Depends(get_db),
):
    """Secure webhook ingress endpoint.
    
    Validates request payload, signature, and timestamp, performs deduplication,
    durably accepts event before acknowledging the provider, and dispatches workflow execution.
    """
    raw_payload = await request.body()
    headers_dict = dict(request.headers)

    service = TriggerIngestionService(database)

    try:
        ingress_response = await service.ingest_webhook_event(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            raw_payload=raw_payload,
            headers=headers_dict,
        )
        return ingress_response
    except WebhookVerificationError as e:
        logger.warning(
            "Webhook verification failed",
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Webhook verification failed: {str(e)}",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(
            "Unexpected error during webhook ingestion",
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to ingest webhook event",
        )


@router.get("/events")
async def list_trigger_events(
    workflow_id: str,
    limit: int = 50,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """List durably accepted trigger events for a workflow in the active workspace."""
    workspace, _ = workspace_and_role
    repo = TriggerRepository(database)
    events = await repo.list_events_for_workflow(
        workspace_id=workspace["id"],
        workflow_id=workflow_id,
        limit=min(limit, 100),
    )
    for ev in events:
        ev["id"] = str(ev["_id"])
        ev.pop("_id", None)
    return events


@router.get("/subscriptions/{workflow_id}")
async def get_trigger_subscription(
    workflow_id: str,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """Get active trigger subscription metadata and webhook URL for a workflow."""
    workspace, _ = workspace_and_role
    repo = TriggerRepository(database)
    sub = await repo.get_subscription(
        workspace_id=workspace["id"],
        workflow_id=workflow_id,
    )
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trigger subscription not found")
    sub["id"] = str(sub["_id"])
    sub.pop("_id", None)
    return sub


@router.post("/evaluate/{workflow_id}")
async def evaluate_trigger_now(
    workflow_id: str,
    workspace_and_role=Depends(get_current_workspace),
    database=Depends(get_db),
):
    """Trigger an on-demand check for polling or schedule workflows (for verification and testing)."""
    workspace, _ = workspace_and_role
    ingestion_service = TriggerIngestionService(database)
    repo = TriggerRepository(database)

    sub = await repo.get_subscription(workspace["id"], workflow_id)
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trigger subscription not found")

    trigger_type = sub.get("trigger_type")
    if trigger_type == "polling":
        engine = PollingEngine(database, ingestion_service)
        ingested = await engine.poll_workflow(workspace["id"], workflow_id)
        return {"trigger_type": "polling", "events_ingested": ingested}
    elif trigger_type == "schedule":
        scheduler = ScheduleEngine(database, ingestion_service)
        fired = await scheduler.evaluate_workflow_schedule(workspace["id"], workflow_id)
        return {"trigger_type": "schedule", "fired": fired}
    else:
        return {"trigger_type": trigger_type, "message": "Manual evaluation not applicable for webhook trigger"}
