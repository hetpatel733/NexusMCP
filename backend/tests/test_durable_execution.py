"""Phase 8 Comprehensive Tests: Durable Workflow Runtime and Execution Workers.

Tests:
1. Temporal Workflow Orchestration (time-skipping environment, dependencies, conditions).
2. Temporal Activities (tool invocation, data mappings, externalization of large payloads).
3. Reliability & Retry Policies (transient vs permanent failure classification, heartbeat, cancellation).
4. Idempotency & Step Tracking (step-level memoization, duplicate prevention).
5. Immutable Version Pinning & Trigger Identity (version preservation across workflow edits).
6. Execution Service & REST API (tenant isolation, run, list, get, cancel, payload inspection).
"""

from typing import Any, Dict, List, Optional

import pytest
from httpx import AsyncClient
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.execution.activities import ExecutionActivities
from app.execution.evaluator import (
    evaluate_step_conditions,
    resolve_json_path,
    resolve_step_arguments,
)
from app.execution.models import (
    ExecutionStatus,
    StepExecutionStatus,
    WorkflowExecutionInput,
)
from app.execution.workflows import TEMPORAL_TASK_QUEUE, WorkflowExecutionWorkflow
from app.integrations.tool import (
    NormalizedToolError,
    ToolInvokedResult,
    ToolType,
)
from app.models.workflow import WorkflowStatus
from app.repositories.execution_repo import ExecutionRepository
from app.repositories.workflow_repo import WorkflowRepository


class MockToolProvider:
    """Mock integration provider for testing tool executions."""
    def __init__(self):
        self.invocations: List[Dict[str, Any]] = []
        self.should_fail: bool = False
        self.fail_transient: bool = True
        self.output_override: Optional[Dict[str, Any]] = None

    async def invoke_tool(
        self,
        tool_id: str,
        arguments: Dict[str, Any],
        workspace_id: Optional[str] = None,
        user_role: str = "member",
    ) -> ToolInvokedResult:
        self.invocations.append({
            "tool_id": tool_id,
            "arguments": arguments,
            "workspace_id": workspace_id,
        })

        if self.should_fail:
            return ToolInvokedResult(
                success=False,
                tool_id=tool_id,
                provider=ToolType.COMPOSIO,
                error=NormalizedToolError(
                    message="Simulated provider error",
                    tool_id=tool_id,
                    provider=ToolType.COMPOSIO,
                    error_code="rate_limit" if self.fail_transient else "validation_error",
                    retryable=self.fail_transient,
                ),
            )

        data = self.output_override if self.output_override is not None else {
            "result": f"Executed {tool_id} successfully",
            "received_args": arguments,
        }
        return ToolInvokedResult(
            success=True,
            tool_id=tool_id,
            provider=ToolType.COMPOSIO,
            output=data,
        )


# ==============================================================================
# 1. Evaluator and Data Mapping Unit Tests
# ==============================================================================

def test_resolve_json_path():
    data = {
        "trigger": {"event_id": "evt-123", "data": {"issue": {"id": 42, "title": "Bug"}}},
        "steps": {
            "step_1": {"output": {"status": "ok", "tags": ["prod", "urgent"]}}
        }
    }
    assert resolve_json_path(data, "trigger.event_id") == "evt-123"
    assert resolve_json_path(data, "trigger.data.issue.id") == 42
    assert resolve_json_path(data, "steps.step_1.output.tags.0") == "prod"
    assert resolve_json_path(data, "steps.step_1.output.tags.1") == "urgent"
    assert resolve_json_path(data, "non.existent.path") is None


def test_resolve_step_arguments():
    context = {
        "trigger": {"data": {"recipient": "alice@example.com"}},
        "steps": {"step_1": {"output": {"msg_id": "msg_999"}}},
    }
    static_args = {"channel": "alerts", "priority": "high"}
    data_mappings = [
        {"target_field": "to", "source": "trigger.data.recipient"},
        {"target_field": "reply_to", "source": "steps.step_1.output.msg_id"},
        {"target_field": "custom_note", "source": "literal", "value": "Fixed literal note"},
    ]
    resolved = resolve_step_arguments(static_args, data_mappings, context)
    assert resolved["channel"] == "alerts"
    assert resolved["priority"] == "high"
    assert resolved["to"] == "alice@example.com"
    assert resolved["reply_to"] == "msg_999"
    assert resolved["custom_note"] == "Fixed literal note"


def test_evaluate_step_conditions():
    context = {
        "trigger": {"data": {"severity": "critical", "count": 10}},
        "steps": {"step_1": {"output": {"state": "open"}}},
    }

    # All conditions pass
    conds = [
        {"field": "trigger.data.severity", "operator": "eq", "value": "critical"},
        {"field": "trigger.data.count", "operator": "gt", "value": 5},
        {"field": "steps.step_1.output.state", "operator": "neq", "value": "closed"},
    ]
    met, reason = evaluate_step_conditions(conds, context)
    assert met is True
    assert reason is None

    # Condition fails
    fail_conds = [
        {"field": "trigger.data.severity", "operator": "eq", "value": "low"}
    ]
    met, reason = evaluate_step_conditions(fail_conds, context)
    assert met is False
    assert "critical" in reason


# ==============================================================================
# 2. Activity Execution, Idempotency, and Large Payload Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_activity_step_execution_and_idempotency(mock_db):
    mock_provider = MockToolProvider()
    activities = ExecutionActivities(mock_db, tool_registry=mock_provider)

    step_dict = {
        "id": "step_1",
        "name": "Send Slack Notification",
        "tool_id": "composio:slack:send_message",
        "arguments": {"text": "Hello world"},
        "data_mappings": [],
        "conditions": [],
        "error_policy": {"strategy": "stop", "max_retries": 2},
    }

    input_payload = {
        "workspace_id": "ws-1",
        "workflow_id": "wf-1",
        "execution_id": "exec-100",
        "workflow_version": 1,
        "step": step_dict,
        "context_data": {"trigger": {"data": {}}},
        "attempt": 1,
    }

    # First execution attempt
    res1 = await activities.execute_step(input_payload)
    assert res1["status"] == StepExecutionStatus.COMPLETED.value
    assert len(mock_provider.invocations) == 1

    # Check step execution was persisted
    step_rec = await activities.repo.get_step_execution("ws-1", "exec-100", "step_1")
    assert step_rec is not None
    assert step_rec["status"] == StepExecutionStatus.COMPLETED.value
    assert "idem-ws-1-wf-1-v1-exec-100-step_1" in step_rec["idempotency_key"]

    # Second execution attempt (simulate retry or restart with same idempotency key)
    res2 = await activities.execute_step(input_payload)
    assert res2["status"] == StepExecutionStatus.COMPLETED.value
    # Provider must NOT have been called a second time!
    assert len(mock_provider.invocations) == 1


@pytest.mark.asyncio
async def test_activity_large_payload_externalization(mock_db):
    """Large output payloads (> 32KB) are stored in MongoDB and a reference is returned."""
    mock_provider = MockToolProvider()
    # Create large payload (> 32KB)
    large_data = {"big_array": ["item_" + str(i) * 100 for i in range(500)]}
    mock_provider.output_override = large_data

    activities = ExecutionActivities(mock_db, tool_registry=mock_provider)

    step_dict = {
        "id": "step_export",
        "name": "Export Data",
        "tool_id": "composio:exporter:dump",
        "arguments": {},
    }

    input_payload = {
        "workspace_id": "ws-1",
        "workflow_id": "wf-1",
        "execution_id": "exec-200",
        "workflow_version": 1,
        "step": step_dict,
        "context_data": {},
        "attempt": 1,
    }

    res = await activities.execute_step(input_payload)
    assert res["status"] == StepExecutionStatus.COMPLETED.value
    # Returned Temporal output contains $ref instead of large raw payload
    assert "$ref" in res["output"]
    payload_id = res["output"]["$ref"]

    # Verify payload was stored in DB and can be retrieved by reference
    stored = await activities.repo.get_payload("ws-1", payload_id)
    assert stored is not None
    assert len(stored["big_array"]) == 500


@pytest.mark.asyncio
async def test_activity_error_distinction_transient_vs_permanent(mock_db):
    """Permanent errors raise non_retryable ApplicationError; transient raise retryable."""
    mock_provider = MockToolProvider()
    activities = ExecutionActivities(mock_db, tool_registry=mock_provider)

    step_dict = {
        "id": "step_fail",
        "name": "Failing Step",
        "tool_id": "composio:fail:action",
        "arguments": {},
    }

    input_payload = {
        "workspace_id": "ws-1",
        "workflow_id": "wf-1",
        "execution_id": "exec-300",
        "workflow_version": 1,
        "step": step_dict,
        "context_data": {},
        "attempt": 1,
    }

    # Transient error (e.g. rate limit)
    mock_provider.should_fail = True
    mock_provider.fail_transient = True
    with pytest.raises(ApplicationError) as exc_info:
        await activities.execute_step(input_payload)
    assert exc_info.value.non_retryable is False

    # Permanent error (e.g. schema validation)
    mock_provider.fail_transient = False
    with pytest.raises(ApplicationError) as exc_info2:
        await activities.execute_step(input_payload)
    assert exc_info2.value.non_retryable is True


# ==============================================================================
# 3. Temporal Workflow Orchestration End-to-End Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_temporal_workflow_execution_end_to_end(mock_db):
    """End-to-end execution of WorkflowExecution in Temporal time-skipping test server."""
    # 1. Setup workflow definition in mock DB
    workflow_repo = WorkflowRepository(mock_db)
    wf_id = await workflow_repo.create({
        "workspace_id": "ws-test",
        "name": "Durable Notification Pipeline",
        "status": WorkflowStatus.ACTIVE.value,
        "current_version": 1,
        "active_version": 1,
        "definition": {
            "schema_version": "1.0.0",
            "name": "Durable Notification Pipeline",
            "trigger": {"type": "webhook", "provider": "github"},
            "steps": [
                {
                    "id": "step_1",
                    "name": "Fetch Issue Details",
                    "tool_id": "composio:github:get_issue",
                    "provider": "composio",
                    "arguments": {"issue_number": 10},
                    "data_mappings": [],
                    "dependencies": [],
                    "conditions": [],
                    "error_policy": {"strategy": "stop", "max_retries": 2},
                },
                {
                    "id": "step_2",
                    "name": "Notify Slack",
                    "tool_id": "composio:slack:send_message",
                    "provider": "composio",
                    "arguments": {"channel": "#engineering"},
                    "data_mappings": [
                        {"target_field": "text", "source": "steps.step_1.output.result"}
                    ],
                    "dependencies": ["step_1"],
                    "conditions": [],
                    "error_policy": {"strategy": "stop", "max_retries": 2},
                },
            ],
        },
    })

    # Create execution record
    exec_repo = ExecutionRepository(mock_db)
    exec_id = await exec_repo.create_execution({
        "workspace_id": "ws-test",
        "workflow_id": wf_id,
        "workflow_version": 1,
        "status": "pending",
        "triggered_by": "webhook",
        "trigger_event": {
            "event_id": "evt-999",
            "event_type": "issues.opened",
            "data": {"action": "opened"},
        },
    })

    mock_tool_provider = MockToolProvider()
    activities = ExecutionActivities(mock_db, tool_registry=mock_tool_provider)

    # Launch Temporal in-memory time-skipping test server
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[WorkflowExecutionWorkflow],
            activities=[
                activities.load_execution_definition,
                activities.execute_step,
                activities.complete_execution,
                activities.store_payload_activity,
                activities.get_payload_activity,
            ],
        ):
            wf_input = WorkflowExecutionInput(
                workspace_id="ws-test",
                workflow_id=wf_id,
                execution_id=exec_id,
                workflow_version=1,
                trigger_event={"event_id": "evt-999", "data": {"action": "opened"}},
            )

            # Start and run workflow
            summary = await env.client.execute_workflow(
                WorkflowExecutionWorkflow.run,
                wf_input.model_dump(mode="json"),
                id=f"wf-{wf_id}-{exec_id}",
                task_queue=TEMPORAL_TASK_QUEUE,
            )

            assert summary["status"] == ExecutionStatus.COMPLETED.value
            assert summary["completed_steps"] == ["step_1", "step_2"]
            assert len(mock_tool_provider.invocations) == 2

            # Check execution document in DB
            db_exec = await exec_repo.get_execution("ws-test", exec_id)
            assert db_exec["status"] == ExecutionStatus.COMPLETED.value
            assert db_exec["completed_at"] is not None

            # Verify step 2 received output mapped from step 1
            step2_call = mock_tool_provider.invocations[1]
            assert "Executed composio:github:get_issue successfully" in step2_call["arguments"]["text"]


@pytest.mark.asyncio
async def test_immutable_version_pinning_during_active_execution(mock_db):
    """Executions remain pinned to their initiated version even when a new version is created."""
    workflow_repo = WorkflowRepository(mock_db)
    wf_id = await workflow_repo.create({
        "workspace_id": "ws-pin",
        "name": "Version Pinning Test",
        "status": WorkflowStatus.ACTIVE.value,
        "current_version": 1,
        "active_version": 1,
        "definition": {
            "schema_version": "1.0.0",
            "name": "Version Pinning Test",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "step_v1",
                    "name": "V1 Original Action",
                    "tool_id": "composio:echo:v1",
                    "arguments": {"version": 1},
                }
            ],
        },
    })

    # Save version 1 to versions history
    await workflow_repo.create_version({
        "workspace_id": "ws-pin",
        "workflow_id": wf_id,
        "version": 1,
        "definition": {
            "schema_version": "1.0.0",
            "name": "Version Pinning Test",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "step_v1",
                    "name": "V1 Original Action",
                    "tool_id": "composio:echo:v1",
                    "arguments": {"version": 1},
                }
            ],
        },
        "created_by": "user1",
    })

    # Start an execution pinned to version 1
    exec_repo = ExecutionRepository(mock_db)
    exec_id = await exec_repo.create_execution({
        "workspace_id": "ws-pin",
        "workflow_id": wf_id,
        "workflow_version": 1,
        "status": "pending",
        "triggered_by": "manual",
        "trigger_event": {},
    })

    # Now edit workflow to version 2 with completely different step
    await workflow_repo.update_workflow("ws-pin", wf_id, {
        "current_version": 2,
        "active_version": 2,
        "definition": {
            "schema_version": "1.0.0",
            "name": "Version Pinning Test",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "step_v2",
                    "name": "V2 New Action",
                    "tool_id": "composio:echo:v2",
                    "arguments": {"version": 2},
                }
            ],
        },
    })

    # Verify activity loads the pinned version 1 definition, NOT version 2!
    mock_provider = MockToolProvider()
    activities = ExecutionActivities(mock_db, tool_registry=mock_provider)
    loaded_def = await activities.load_execution_definition({
        "workspace_id": "ws-pin",
        "workflow_id": wf_id,
        "execution_id": exec_id,
        "workflow_version": 1,
    })

    assert loaded_def["steps"][0]["id"] == "step_v1"
    assert loaded_def["steps"][0]["arguments"]["version"] == 1


# ==============================================================================
# 4. REST API & Execution Service Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_execution_api_endpoints_and_isolation(client: AsyncClient, mock_db):
    """Test REST API for running, listing, querying, and cancelling executions with tenant isolation."""
    # Register two real tenants via auth API for complete tenant-isolation testing
    from tests.test_tenant_isolation import setup_two_tenants
    tenants = await setup_two_tenants(client)
    user_a = tenants["user_a"]
    user_b = tenants["user_b"]

    headers_a = {
        "Authorization": f"Bearer {user_a['token']}",
        "X-Workspace-ID": user_a["workspace_id"],
    }
    headers_b = {
        "Authorization": f"Bearer {user_b['token']}",
        "X-Workspace-ID": user_b["workspace_id"],
    }

    # Setup workflow in workspace A
    workflow_repo = WorkflowRepository(mock_db)
    wf_id = await workflow_repo.create({
        "workspace_id": user_a["workspace_id"],
        "name": "API Triggered Workflow",
        "status": WorkflowStatus.ACTIVE.value,
        "current_version": 1,
        "active_version": 1,
        "definition": {
            "schema_version": "1.0.0",
            "name": "API Triggered Workflow",
            "trigger": {"type": "manual"},
            "steps": [
                {"id": "step_1", "name": "Step One", "tool_id": "composio:test:run", "arguments": {}}
            ],
        },
    })

    # 1. Trigger manual run in workspace A
    run_resp = await client.post(
        f"/api/v1/workflows/{wf_id}/run",
        json={"trigger_data": {"test_var": "val1"}},
        headers=headers_a,
    )
    assert run_resp.status_code == 202
    run_data = run_resp.json()
    exec_id = run_data["execution_id"]
    assert run_data["workflow_version"] == 1
    assert run_data["status"] == "pending"

    # 2. List executions for workflow in workspace A
    list_resp = await client.get(
        f"/api/v1/workflows/{wf_id}/executions",
        headers=headers_a,
    )
    assert list_resp.status_code == 200
    executions = list_resp.json()
    assert len(executions) == 1
    assert executions[0]["id"] == exec_id

    # 3. List all executions in workspace A
    all_resp = await client.get(
        "/api/v1/executions",
        headers=headers_a,
    )
    assert all_resp.status_code == 200
    assert len(all_resp.json()) == 1

    # 4. Get single execution details in workspace A
    get_resp = await client.get(
        f"/api/v1/executions/{exec_id}",
        headers=headers_a,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == exec_id
    assert get_resp.json()["trigger_event"]["data"]["test_var"] == "val1"

    # 5. Cancel execution in workspace A
    cancel_resp = await client.post(
        f"/api/v1/executions/{exec_id}/cancel",
        headers=headers_a,
    )
    assert cancel_resp.status_code == 200

    # Verify cancelled status
    check_resp = await client.get(
        f"/api/v1/executions/{exec_id}",
        headers=headers_a,
    )
    assert check_resp.json()["status"] == "cancelled"

    # 6. Tenant isolation: Workspace B cannot see or access execution of Workspace A!
    iso_resp = await client.get(
        f"/api/v1/executions/{exec_id}",
        headers=headers_b,
    )
    assert iso_resp.status_code == 404

    # Workspace B executions list must be empty
    list_b = await client.get("/api/v1/executions", headers=headers_b)
    assert list_b.status_code == 200
    assert len(list_b.json()) == 0


@pytest.mark.asyncio
async def test_step_conditions_and_continue_strategy_in_temporal(mock_db):
    """Test conditions skipping steps and strategy 'continue' on step failure."""
    workflow_repo = WorkflowRepository(mock_db)
    wf_id = await workflow_repo.create({
        "workspace_id": "ws-branch",
        "name": "Branching Workflow",
        "status": WorkflowStatus.ACTIVE.value,
        "current_version": 1,
        "active_version": 1,
        "definition": {
            "schema_version": "1.0.0",
            "name": "Branching Workflow",
            "trigger": {"type": "manual"},
            "steps": [
                {
                    "id": "step_conditional",
                    "name": "Conditional Step",
                    "tool_id": "composio:check:eval",
                    "conditions": [
                        {"field": "trigger.data.enabled", "operator": "eq", "value": True}
                    ],
                    "arguments": {},
                },
                {
                    "id": "step_non_blocking_fail",
                    "name": "Non Blocking Failure",
                    "tool_id": "composio:fail:ignore",
                    "error_policy": {"strategy": "continue", "max_retries": 1},
                    "arguments": {},
                },
            ],
        },
    })

    exec_repo = ExecutionRepository(mock_db)
    exec_id = await exec_repo.create_execution({
        "workspace_id": "ws-branch",
        "workflow_id": wf_id,
        "workflow_version": 1,
        "status": "pending",
        "trigger_event": {"data": {"enabled": False}},
    })

    mock_provider = MockToolProvider()
    mock_provider.should_fail = True  # will fail step_non_blocking_fail
    mock_provider.fail_transient = False

    activities = ExecutionActivities(mock_db, tool_registry=mock_provider)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[WorkflowExecutionWorkflow],
            activities=[
                activities.load_execution_definition,
                activities.execute_step,
                activities.complete_execution,
                activities.store_payload_activity,
                activities.get_payload_activity,
            ],
        ):
            wf_input = WorkflowExecutionInput(
                workspace_id="ws-branch",
                workflow_id=wf_id,
                execution_id=exec_id,
                workflow_version=1,
                trigger_event={"data": {"enabled": False}},
            )

            summary = await env.client.execute_workflow(
                WorkflowExecutionWorkflow.run,
                wf_input.model_dump(mode="json"),
                id=f"wf-branch-{exec_id}",
                task_queue=TEMPORAL_TASK_QUEUE,
            )

            # Conditional step was skipped
            assert "step_conditional" in summary["skipped_steps"]
            # Workflow completed because the failure strategy was 'continue'!
            assert summary["status"] == ExecutionStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_step_execution_tracking_and_payload_api(client: AsyncClient, mock_db):
    """Test step execution detail listing and large payload API endpoints."""
    from tests.test_tenant_isolation import setup_two_tenants
    tenants = await setup_two_tenants(client)
    user = tenants["user_a"]
    ws_id = user["workspace_id"]
    headers = {
        "Authorization": f"Bearer {user['token']}",
        "X-Workspace-ID": ws_id,
    }

    exec_repo = ExecutionRepository(mock_db)
    exec_id = await exec_repo.create_execution({
        "workspace_id": ws_id,
        "workflow_id": "wf-123",
        "workflow_version": 1,
        "status": "completed",
    })

    # Record step start and completion
    await exec_repo.record_step_start(
        workspace_id=ws_id,
        execution_id=exec_id,
        workflow_id="wf-123",
        workflow_version=1,
        step_id="step_a",
        step_name="Step Alpha",
        attempt=1,
        idempotency_key="key-alpha-1",
        input_data={"foo": "bar"},
    )
    await exec_repo.record_step_completion(
        workspace_id=ws_id,
        execution_id=exec_id,
        step_id="step_a",
        attempt=1,
        output_data={"result": "ok"},
        execution_time_ms=45,
    )

    # 1. Fetch step details via API
    steps_resp = await client.get(
        f"/api/v1/executions/{exec_id}/steps",
        headers=headers,
    )
    assert steps_resp.status_code == 200
    steps = steps_resp.json()
    assert len(steps) == 1
    assert steps[0]["step_id"] == "step_a"
    assert steps[0]["output_data"]["result"] == "ok"
    assert steps[0]["execution_time_ms"] == 45

    # 2. Store large payload and fetch via API
    pref = await exec_repo.store_payload(
        workspace_id=ws_id,
        execution_id=exec_id,
        payload_id="payload-test-1",
        data={"large_field": "data" * 100},
    )
    payload_resp = await client.get(
        f"/api/v1/executions/payloads/{pref.payload_id}",
        headers=headers,
    )
    assert payload_resp.status_code == 200
    p_data = payload_resp.json()
    assert p_data["payload_id"] == "payload-test-1"
    assert "large_field" in p_data["data"]

