"""Workflow Execution Service for Managing Execution Lifecycle.

Orchestrates execution creation, version pinning, step tracking queries,
cancellation, and Temporal dispatching.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..logger import logger
from ..repositories.execution_repo import ExecutionRepository
from ..repositories.workflow_repo import WorkflowRepository
from .client import TemporalWorkflowDispatcher
from .models import ExecutionStatus


class ExecutionService:
    """Service layer for workflow executions."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        dispatcher: Optional[TemporalWorkflowDispatcher] = None,
    ):
        self.db = db
        self.repo = ExecutionRepository(db)
        self.workflow_repo = WorkflowRepository(db)
        self.dispatcher = dispatcher or TemporalWorkflowDispatcher()

    async def create_and_dispatch_execution(
        self,
        workspace_id: str,
        workflow_id: str,
        triggered_by: str = "manual",
        trigger_event: Optional[Dict[str, Any]] = None,
        workflow_version: Optional[int] = None,
        user_id: Optional[str] = None,
        user_role: str = "member",
    ) -> Dict[str, Any]:
        """Create a new execution pinned to workflow_version and dispatch to Temporal worker."""
        # 1. Resolve workflow & version pinning
        workflow = await self.workflow_repo.get_workflow(workspace_id, workflow_id)
        if not workflow:
            raise ValueError(f"Workflow '{workflow_id}' not found in workspace '{workspace_id}'")

        if workflow_version is None:
            workflow_version = workflow.get("active_version") or workflow.get("current_version", 1)

        event_data = trigger_event or {
            "event_id": f"manual-{int(datetime.now(timezone.utc).timestamp())}",
            "event_type": "manual_trigger",
            "provider": triggered_by,
            "data": {},
        }

        # 2. Persist execution record pinned to immutable version
        now = datetime.now(timezone.utc)
        from ..observability.correlation import get_correlation_id
        corr_id = get_correlation_id()
        record = {
            "workspace_id": workspace_id,
            "workflow_id": workflow_id,
            "workflow_version": workflow_version,
            "status": ExecutionStatus.PENDING.value,
            "triggered_by": triggered_by,
            "trigger_event": event_data,
            "correlation_id": corr_id,
            "user_id": user_id,
            "user_role": user_role,
            "created_at": now,
            "started_at": now,
            "steps": {},
        }
        execution_id = await self.repo.create_execution(record)

        # 3. Dispatch to Temporal worker
        temporal_id = None
        try:
            temporal_id = await self.dispatcher.dispatch_execution(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                execution_id=execution_id,
                workflow_version=workflow_version,
                trigger_event=event_data,
                correlation_id=corr_id,
                user_id=user_id,
                user_role=user_role,
            )
            if temporal_id:
                await self.repo.update_execution_status(
                    workspace_id=workspace_id,
                    execution_id=execution_id,
                    status=ExecutionStatus.PENDING.value,
                    temporal_workflow_id=temporal_id,
                )
        except Exception as e:
            logger.warning("Temporal dispatch note", error=str(e), execution_id=execution_id)

        return {
            "execution_id": execution_id,
            "workspace_id": workspace_id,
            "workflow_id": workflow_id,
            "workflow_version": workflow_version,
            "status": ExecutionStatus.PENDING.value,
            "temporal_workflow_id": temporal_id,
            "started_at": now.isoformat(),
        }

    async def list_executions(
        self,
        workspace_id: str,
        workflow_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        """List executions with tenant isolation."""
        docs = await self.repo.list_executions(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            status=status,
            limit=limit,
            skip=skip,
        )
        for d in docs:
            d["id"] = str(d.pop("_id", ""))
        return docs

    async def get_execution(
        self,
        workspace_id: str,
        execution_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get execution details with tenant isolation."""
        doc = await self.repo.get_execution(workspace_id, execution_id)
        if not doc:
            return None
        doc["id"] = str(doc.pop("_id", ""))
        return doc

    async def list_step_executions(
        self,
        workspace_id: str,
        execution_id: str,
    ) -> List[Dict[str, Any]]:
        """Get all step attempt records for an execution."""
        docs = await self.repo.list_step_executions(workspace_id, execution_id)
        for d in docs:
            d["id"] = str(d.pop("_id", ""))
        return docs

    async def cancel_execution(
        self,
        workspace_id: str,
        workflow_id: str,
        execution_id: str,
    ) -> bool:
        """Cancel an in-flight execution."""
        execution = await self.repo.get_execution(workspace_id, execution_id)
        if not execution:
            return False

        # Attempt signal to Temporal
        await self.dispatcher.cancel_execution(workflow_id, execution_id)

        # Mark in MongoDB
        return await self.repo.update_execution_status(
            workspace_id=workspace_id,
            execution_id=execution_id,
            status=ExecutionStatus.CANCELLED.value,
            completed_at=datetime.now(timezone.utc),
            error="Cancelled by user request",
        )

    async def get_payload(
        self,
        workspace_id: str,
        payload_id: str,
    ) -> Optional[Any]:
        """Retrieve large payload data by payload_id."""
        return await self.repo.get_payload(workspace_id, payload_id)
