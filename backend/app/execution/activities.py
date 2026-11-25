"""Temporal Activities for Durable Workflow Execution.

Implements:
- Step execution with ToolRegistry (Composio and MCP)
- Payload externalization and serialization
- Idempotency key generation and duplicate action prevention
- Timeout, heartbeat, and cancellation handling
- Error categorization (transient vs permanent)
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..integrations.registry import ToolRegistry
from ..logger import logger
from ..repositories.execution_repo import ExecutionRepository
from .evaluator import evaluate_step_conditions, resolve_step_arguments
from .models import (
    ExecutionStatus,
    StepExecutionInput,
    StepExecutionOutput,
    StepExecutionStatus,
)

# Threshold in bytes above which payload data is externalized into MongoDB
PAYLOAD_SIZE_THRESHOLD_BYTES = 32 * 1024  # 32 KB


class ExecutionActivities:
    """Class-based Temporal activities with dependency-injected DB and Tool Registry."""

    def __init__(self, db, tool_registry: Optional[ToolRegistry] = None):
        self.db = db
        self.repo = ExecutionRepository(db)
        self.tool_registry = tool_registry or ToolRegistry(db)

    @activity.defn(name="load_execution_definition")
    async def load_execution_definition(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Load pinned workflow definition and transition execution to RUNNING."""
        workspace_id = input_data["workspace_id"]
        workflow_id = input_data["workflow_id"]
        execution_id = input_data["execution_id"]
        version = input_data["workflow_version"]

        logger.info(
            "Loading pinned workflow definition for execution",
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            execution_id=execution_id,
            version=version,
        )

        definition = await self.repo.get_workflow_definition_for_execution(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            version=version,
        )
        if not definition:
            raise ApplicationError(
                f"Workflow definition not found for workflow {workflow_id} version {version}",
                non_retryable=True,
            )

        # Update execution state to RUNNING
        await self.repo.update_execution_status(
            workspace_id=workspace_id,
            execution_id=execution_id,
            status=ExecutionStatus.RUNNING.value,
        )

        return definition

    @activity.defn(name="execute_step")
    async def execute_step(self, step_input_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single workflow step with full idempotency, condition evaluation, and error handling."""
        step_input = StepExecutionInput(**step_input_dict)
        workspace_id = step_input.workspace_id
        workflow_id = step_input.workflow_id
        execution_id = step_input.execution_id
        workflow_version = step_input.workflow_version
        step = step_input.step
        context_data = step_input.context_data
        step_id = step.get("id", "unknown_step")
        step_name = step.get("name", step_id)

        # Bind correlation ID for activity execution
        if step_input.correlation_id:
            from ..observability.correlation import set_correlation_id
            set_correlation_id(step_input.correlation_id)

        # Determine attempt number
        try:
            attempt = activity.info().attempt
        except Exception:
            attempt = step_input.attempt

        # Handle cancellation
        try:
            if activity.is_cancelled():
                raise asyncio.CancelledError()
        except Exception:
            pass

        # Activity heartbeat
        try:
            activity.heartbeat(f"Executing step {step_id} (attempt {attempt})")
        except Exception:
            pass

        # Deterministic Idempotency Key
        idempotency_key = f"idem-{workspace_id}-{workflow_id}-v{workflow_version}-{execution_id}-{step_id}"

        # 1. Idempotency Check: Prevent duplicate side effects if already executed
        cached_step = await self.repo.get_step_by_idempotency_key(workspace_id, idempotency_key)
        if cached_step and cached_step.get("status") == StepExecutionStatus.COMPLETED.value:
            logger.info(
                "Step already completed; returning cached output via idempotency key",
                step_id=step_id,
                idempotency_key=idempotency_key,
                execution_id=execution_id,
            )
            return StepExecutionOutput(
                step_id=step_id,
                status=StepExecutionStatus.COMPLETED,
                output=cached_step.get("output_data") or {},
                execution_time_ms=cached_step.get("execution_time_ms", 0),
                idempotency_key=idempotency_key,
                skipped=False,
            ).model_dump(mode="json")

        # 2. Condition Evaluation: check step preconditions
        conditions = step.get("conditions", [])
        if conditions:
            conditions_met, failure_reason = evaluate_step_conditions(conditions, context_data)
            if not conditions_met:
                logger.info(
                    "Step skipped because conditions were not met",
                    step_id=step_id,
                    reason=failure_reason,
                    execution_id=execution_id,
                )
                await self.repo.record_step_completion(
                    workspace_id=workspace_id,
                    execution_id=execution_id,
                    step_id=step_id,
                    attempt=attempt,
                    output_data={},
                    execution_time_ms=0,
                    status=StepExecutionStatus.SKIPPED,
                    skip_reason=failure_reason,
                )
                return StepExecutionOutput(
                    step_id=step_id,
                    status=StepExecutionStatus.SKIPPED,
                    output={},
                    idempotency_key=idempotency_key,
                    skipped=True,
                    skip_reason=failure_reason,
                ).model_dump(mode="json")

        # 3. Argument Resolution & Data Mapping
        static_args = step.get("arguments", {})
        data_mappings = step.get("data_mappings", [])
        resolved_arguments = resolve_step_arguments(static_args, data_mappings, context_data)

        # Propagate idempotency key into target tool arguments if accepted
        resolved_arguments["idempotency_key"] = idempotency_key

        # 4. Record step attempt start
        await self.repo.record_step_start(
            workspace_id=workspace_id,
            execution_id=execution_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            step_id=step_id,
            step_name=step_name,
            attempt=attempt,
            idempotency_key=idempotency_key,
            input_data=resolved_arguments,
            correlation_id=step_input.correlation_id,
        )

        # 5. Invoke Tool via ToolRegistry
        start_time = time.time()
        tool_id = step.get("tool_id")
        if not tool_id:
            await self.repo.record_step_completion(
                workspace_id=workspace_id,
                execution_id=execution_id,
                step_id=step_id,
                attempt=attempt,
                output_data={},
                execution_time_ms=0,
                status=StepExecutionStatus.FAILED,
                error="Step is missing tool_id",
            )
            raise ApplicationError(f"Step '{step_id}' is missing tool_id", non_retryable=True)

        try:
            invoked_result = await self.tool_registry.invoke_tool(
                tool_id=tool_id,
                arguments=resolved_arguments,
                workspace_id=workspace_id,
                user_id=step_input.user_id,
                user_role=step_input.user_role,
            )
        except Exception as exc:
            elapsed_ms = int((time.time() - start_time) * 1000)
            err_msg = str(exc)
            await self.repo.record_step_completion(
                workspace_id=workspace_id,
                execution_id=execution_id,
                step_id=step_id,
                attempt=attempt,
                output_data={},
                execution_time_ms=elapsed_ms,
                status=StepExecutionStatus.FAILED,
                error=err_msg,
            )
            raise ApplicationError(
                f"Tool invocation exception on step '{step_id}': {err_msg}",
                non_retryable=False,
            )

        elapsed_ms = int((time.time() - start_time) * 1000)

        # 6. Evaluate Result and Handle Failures
        if not invoked_result.success:
            err = invoked_result.error
            err_message = err.message if err else "Tool execution failed"
            err_code = err.error_code if err else "execution_error"
            is_retryable = err.retryable if err else True

            # If input validation failed or tool not found, it is non-retryable
            if err_code in ("validation_error", "tool_not_found", "permission_denied", "unauthorized"):
                is_retryable = False

            await self.repo.record_step_completion(
                workspace_id=workspace_id,
                execution_id=execution_id,
                step_id=step_id,
                attempt=attempt,
                output_data={},
                execution_time_ms=elapsed_ms,
                status=StepExecutionStatus.FAILED,
                error=f"{err_message} (code: {err_code})",
            )

            # Signal failure to Temporal with retryable distinction
            raise ApplicationError(
                f"Step '{step_id}' failed: {err_message} (code: {err_code})",
                non_retryable=(not is_retryable),
            )

        # 7. Payload externalization for large outputs (Serializable Activity Data)
        output_data = getattr(invoked_result, "output", None) or getattr(invoked_result, "data", None) or {}
        if not isinstance(output_data, dict):
            output_data = {"result": output_data}
        serialized_output = json.dumps(output_data, default=str)
        payload_ref_dict = None
        temporal_output = output_data

        if len(serialized_output.encode("utf-8")) > PAYLOAD_SIZE_THRESHOLD_BYTES:
            payload_id = f"payload-{execution_id}-{step_id}-{attempt}"
            pref = await self.repo.store_payload(
                workspace_id=workspace_id,
                execution_id=execution_id,
                payload_id=payload_id,
                data=output_data,
            )
            payload_ref_dict = pref.model_dump(mode="json")
            # Compact reference returned through Temporal history
            temporal_output = {
                "$ref": payload_id,
                "size_bytes": pref.size_bytes,
                "preview": pref.preview,
            }

        # 8. Record successful step execution
        await self.repo.record_step_completion(
            workspace_id=workspace_id,
            execution_id=execution_id,
            step_id=step_id,
            attempt=attempt,
            output_data=output_data,
            execution_time_ms=elapsed_ms,
            payload_ref=payload_ref_dict,
            status=StepExecutionStatus.COMPLETED,
        )

        return StepExecutionOutput(
            step_id=step_id,
            status=StepExecutionStatus.COMPLETED,
            output=temporal_output,
            execution_time_ms=elapsed_ms,
            idempotency_key=idempotency_key,
            skipped=False,
        ).model_dump(mode="json")

    @activity.defn(name="complete_execution")
    async def complete_execution(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update workflow execution record to final status (COMPLETED, FAILED, or CANCELLED)."""
        workspace_id = input_data["workspace_id"]
        execution_id = input_data["execution_id"]
        status = input_data["status"]
        error = input_data.get("error")
        step_summary = input_data.get("step_summary")
        completed_at = datetime.now(timezone.utc)

        logger.info(
            "Finalizing workflow execution in MongoDB",
            workspace_id=workspace_id,
            execution_id=execution_id,
            status=status,
            error=error,
        )

        await self.repo.update_execution_status(
            workspace_id=workspace_id,
            execution_id=execution_id,
            status=status,
            error=error,
            completed_at=completed_at,
            step_summary=step_summary,
        )

        return {
            "execution_id": execution_id,
            "status": status,
            "completed_at": completed_at.isoformat(),
        }

    @activity.defn(name="store_payload_activity")
    async def store_payload_activity(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Store large payload explicitly and return a reference."""
        pref = await self.repo.store_payload(
            workspace_id=input_data["workspace_id"],
            execution_id=input_data["execution_id"],
            payload_id=input_data["payload_id"],
            data=input_data["data"],
        )
        return pref.model_dump(mode="json")

    @activity.defn(name="get_payload_activity")
    async def get_payload_activity(self, input_data: Dict[str, Any]) -> Any:
        """Retrieve externalized large payload by reference."""
        return await self.repo.get_payload(
            workspace_id=input_data["workspace_id"],
            payload_id=input_data["payload_id"],
        )
