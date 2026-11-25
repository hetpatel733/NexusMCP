"""Temporal Client Service for Dispatching and Managing Workflow Executions.

Handles:
- Connecting to Temporal server
- Dispatching execution workflows with deterministic IDs
- Querying and signalling workflow execution instances
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from temporalio.client import Client

from ..config import Settings, get_settings
from ..logger import logger
from .models import WorkflowExecutionInput
from .workflows import TEMPORAL_TASK_QUEUE, WorkflowExecutionWorkflow

_client_cache: Optional[Client] = None


async def get_temporal_client(settings: Optional[Settings] = None) -> Optional[Client]:
    """Retrieve or create a singleton Temporal client instance."""
    global _client_cache
    if _client_cache is not None:
        return _client_cache

    st = settings or get_settings()
    try:
        _client_cache = await Client.connect(
            target_host=st.TEMPORAL_HOST,
            namespace=st.TEMPORAL_NAMESPACE,
        )
        logger.info(
            "Connected to Temporal server",
            host=st.TEMPORAL_HOST,
            namespace=st.TEMPORAL_NAMESPACE,
        )
        return _client_cache
    except Exception as e:
        logger.warning("Could not connect to Temporal server", host=st.TEMPORAL_HOST, error=str(e))
        return None


class TemporalWorkflowDispatcher:
    """Dispatches and controls durable workflow executions."""

    def __init__(self, client: Optional[Client] = None):
        self.client = client

    async def ensure_client(self) -> Optional[Client]:
        if not self.client:
            self.client = await get_temporal_client()
        return self.client

    async def dispatch_execution(
        self,
        workspace_id: str,
        workflow_id: str,
        execution_id: str,
        workflow_version: int,
        trigger_event: Dict[str, Any],
        correlation_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_role: str = "member",
    ) -> Optional[str]:
        """Dispatch a new Temporal execution instance pinned to workflow_version."""
        client = await self.ensure_client()
        if not client:
            logger.warning(
                "Temporal client unavailable; execution queued in database only",
                execution_id=execution_id,
            )
            return None

        temporal_wf_id = f"wf-{workflow_id}-{execution_id}"
        wf_input = WorkflowExecutionInput(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            execution_id=execution_id,
            workflow_version=workflow_version,
            trigger_event=trigger_event,
            correlation_id=correlation_id,
            user_id=user_id,
            user_role=user_role,
        )

        try:
            handle = await client.start_workflow(
                WorkflowExecutionWorkflow.run,
                wf_input.model_dump(mode="json"),
                id=temporal_wf_id,
                task_queue=TEMPORAL_TASK_QUEUE,
            )
            logger.info(
                "Started Temporal WorkflowExecution",
                temporal_workflow_id=temporal_wf_id,
                execution_id=execution_id,
            )
            return handle.id
        except Exception as e:
            logger.error(
                "Failed to dispatch Temporal workflow execution",
                execution_id=execution_id,
                error=str(e),
            )
            raise

    async def cancel_execution(self, workflow_id: str, execution_id: str) -> bool:
        """Signal and cancel an active Temporal workflow execution."""
        client = await self.ensure_client()
        if not client:
            return False

        temporal_wf_id = f"wf-{workflow_id}-{execution_id}"
        try:
            handle = client.get_workflow_handle(temporal_wf_id)
            await handle.signal(WorkflowExecutionWorkflow.cancel_execution)
            await handle.cancel()
            return True
        except Exception as e:
            logger.warning("Failed to cancel Temporal workflow", id=temporal_wf_id, error=str(e))
            return False

    async def query_execution_status(
        self,
        workflow_id: str,
        execution_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Query in-flight Temporal workflow state."""
        client = await self.ensure_client()
        if not client:
            return None

        temporal_wf_id = f"wf-{workflow_id}-{execution_id}"
        try:
            handle = client.get_workflow_handle(temporal_wf_id)
            return await handle.query(WorkflowExecutionWorkflow.get_execution_summary)
        except Exception as e:
            logger.debug("Temporal workflow query note", id=temporal_wf_id, error=str(e))
            return None
