"""Deterministic Temporal Workflow for Workflow Execution Orchestration.

Architecture:
- Execution Model: Individual execution orchestration model (`WorkflowExecution`).
  Each triggered event launches an isolated Temporal workflow instance.
  Pinned to the exact immutable workflow version that was active upon trigger.
- Determinism:
  All network calls, database mutations, and tool invocations are executed exclusively
  through Temporal activities. Workflow logic contains zero non-deterministic calls.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Dict, List, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from .models import (
    ExecutionStatus,
    StepExecutionStatus,
    WorkflowExecutionInput,
    WorkflowExecutionSummary,
)

TEMPORAL_TASK_QUEUE = "nexusmcp-task-queue"


@workflow.defn(name="WorkflowExecution")
class WorkflowExecutionWorkflow:
    """Orchestrates the durable background execution of a version-pinned NexusMCP workflow."""

    def __init__(self) -> None:
        self._status: ExecutionStatus = ExecutionStatus.PENDING
        self._execution_id: str = ""
        self._workspace_id: str = ""
        self._workflow_id: str = ""
        self._workflow_version: int = 1
        self._completed_steps: List[str] = []
        self._skipped_steps: List[str] = []
        self._failed_step: Optional[str] = None
        self._error: Optional[str] = None
        self._step_results: Dict[str, Any] = {}
        self._step_summary: Dict[str, Any] = {}
        self._cancelled: bool = False

    @workflow.query(name="get_status")
    def get_status(self) -> str:
        """Query current execution status."""
        return self._status.value

    @workflow.query(name="get_execution_summary")
    def get_execution_summary(self) -> Dict[str, Any]:
        """Query execution progress and step outcomes."""
        return {
            "execution_id": self._execution_id,
            "status": self._status.value,
            "completed_steps": self._completed_steps,
            "skipped_steps": self._skipped_steps,
            "failed_step": self._failed_step,
            "error": self._error,
            "step_summary": self._step_summary,
        }

    @workflow.signal(name="cancel_execution")
    async def cancel_execution(self) -> None:
        """Signal to gracefully cancel this workflow execution."""
        self._cancelled = True

    @workflow.run
    async def run(self, input_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the workflow graph step-by-step with dependency checking, retry policies, and error handling."""
        start_time = workflow.now()
        parsed_input = WorkflowExecutionInput(**input_dict)
        self._workspace_id = parsed_input.workspace_id
        self._workflow_id = parsed_input.workflow_id
        self._execution_id = parsed_input.execution_id
        self._workflow_version = parsed_input.workflow_version
        self._status = ExecutionStatus.RUNNING

        # Initial context contains the originating trigger event
        context_data: Dict[str, Any] = {
            "trigger": parsed_input.trigger_event,
            "steps": {},
        }

        try:
            # 1. Load pinned workflow definition from MongoDB via Activity
            definition = await workflow.execute_activity(
                "load_execution_definition",
                {
                    "workspace_id": self._workspace_id,
                    "workflow_id": self._workflow_id,
                    "execution_id": self._execution_id,
                    "workflow_version": self._workflow_version,
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_attempts=3,
                    non_retryable_error_types=["ApplicationError"],
                ),
            )

            steps: List[Dict[str, Any]] = definition.get("steps", [])

            # 2. Execute Steps respecting dependencies and conditions
            executed_steps_set = set()

            for step in steps:
                if self._cancelled:
                    raise asyncio.CancelledError()

                step_id = step.get("id")
                dependencies = step.get("dependencies", [])

                # Check step dependencies
                unmet_deps = [d for d in dependencies if d not in executed_steps_set]
                if unmet_deps:
                    workflow.logger.warning(
                        f"Step '{step_id}' unmet dependencies: {unmet_deps}. Step skipped."
                    )
                    self._skipped_steps.append(step_id)
                    self._step_summary[step_id] = {
                        "status": StepExecutionStatus.SKIPPED.value,
                        "skip_reason": f"Prerequisite steps not completed: {unmet_deps}",
                    }
                    continue

                # Configure step retry policy based on error_policy
                error_policy = step.get("error_policy", {})
                max_retries = max(1, error_policy.get("max_retries", 3) + 1)
                retry_delay = max(1, error_policy.get("retry_delay_seconds", 2))
                backoff = max(1.0, float(error_policy.get("backoff_multiplier", 2.0)))
                strategy = error_policy.get("strategy", "stop")

                step_retry_policy = RetryPolicy(
                    initial_interval=timedelta(seconds=retry_delay),
                    backoff_coefficient=backoff,
                    maximum_attempts=max_retries,
                    non_retryable_error_types=["ApplicationError"],
                )

                # Execute step via Activity
                try:
                    step_output: Dict[str, Any] = await workflow.execute_activity(
                        "execute_step",
                        {
                            "workspace_id": self._workspace_id,
                            "workflow_id": self._workflow_id,
                            "execution_id": self._execution_id,
                            "workflow_version": self._workflow_version,
                            "step": step,
                            "context_data": context_data,
                            "attempt": 1,
                            "correlation_id": parsed_input.correlation_id,
                            "user_id": parsed_input.user_id,
                            "user_role": parsed_input.user_role,
                        },
                        start_to_close_timeout=timedelta(seconds=120),
                        heartbeat_timeout=timedelta(seconds=30),
                        retry_policy=step_retry_policy,
                    )

                    status = step_output.get("status")
                    if status == StepExecutionStatus.SKIPPED.value:
                        self._skipped_steps.append(step_id)
                        self._step_summary[step_id] = {
                            "status": StepExecutionStatus.SKIPPED.value,
                            "skip_reason": step_output.get("skip_reason"),
                        }
                    else:
                        self._completed_steps.append(step_id)
                        executed_steps_set.add(step_id)
                        out_val = step_output.get("output", {})
                        self._step_results[step_id] = out_val
                        context_data["steps"][step_id] = {
                            "status": status,
                            "output": out_val,
                        }
                        self._step_summary[step_id] = {
                            "status": StepExecutionStatus.COMPLETED.value,
                            "output": out_val,
                            "execution_time_ms": step_output.get("execution_time_ms", 0),
                        }

                except (ActivityError, ApplicationError, Exception) as step_exc:
                    workflow.logger.error(f"Step '{step_id}' failed: {step_exc}")
                    self._failed_step = step_id
                    err_msg = str(step_exc)
                    self._step_summary[step_id] = {
                        "status": StepExecutionStatus.FAILED.value,
                        "error": err_msg,
                    }

                    if strategy == "continue":
                        # Record error in context and allow downstream steps to evaluate
                        context_data["steps"][step_id] = {
                            "status": StepExecutionStatus.FAILED.value,
                            "error": err_msg,
                            "output": {},
                        }
                        continue
                    else:
                        # Strategy is STOP or retry exhausted: fail the entire workflow
                        self._status = ExecutionStatus.FAILED
                        self._error = f"Step '{step_id}' failed: {err_msg}"
                        break

            # If no step failed with STOP, workflow is COMPLETED
            if self._status != ExecutionStatus.FAILED:
                self._status = ExecutionStatus.COMPLETED

        except asyncio.CancelledError:
            self._status = ExecutionStatus.CANCELLED
            self._error = "Workflow execution was cancelled"
            workflow.logger.info("Workflow execution cancelled")

        except Exception as e:
            self._status = ExecutionStatus.FAILED
            self._error = str(e)
            workflow.logger.error(f"Workflow execution uncaught failure: {e}")

        # 3. Finalize Execution state in MongoDB via Activity
        try:
            await workflow.execute_activity(
                "complete_execution",
                {
                    "workspace_id": self._workspace_id,
                    "execution_id": self._execution_id,
                    "status": self._status.value,
                    "error": self._error,
                    "step_summary": self._step_summary,
                },
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_attempts=5,
                ),
            )
        except Exception as final_exc:
            workflow.logger.error(f"Failed to record execution completion: {final_exc}")

        completed_time = workflow.now()
        duration_ms = int((completed_time - start_time).total_seconds() * 1000)

        return WorkflowExecutionSummary(
            execution_id=self._execution_id,
            workspace_id=self._workspace_id,
            workflow_id=self._workflow_id,
            workflow_version=self._workflow_version,
            status=self._status,
            step_count=len(self._completed_steps) + len(self._skipped_steps),
            completed_steps=self._completed_steps,
            skipped_steps=self._skipped_steps,
            failed_step=self._failed_step,
            error=self._error,
            started_at=start_time,
            completed_at=completed_time,
            duration_ms=duration_ms,
        ).model_dump(mode="json")
