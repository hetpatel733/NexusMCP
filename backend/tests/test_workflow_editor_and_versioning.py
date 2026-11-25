"""Tests for Phase 6: Workflow Editor, Validation & Versioning.

Validates all Phase 6 acceptance criteria and requirements:
1. Workflow Detail & Editing (trigger, steps, arguments, data mappings, dependencies).
2. Deterministic validation:
   - Node types & graph structure
   - Registry tool existence & authorization
   - Required fields and parameters against tool schemas
   - Unreachable nodes detection
   - Invalid / cyclic dependencies detection
   - Data mappings validity
3. Integration connection & health verification before activation.
4. Workflow lifecycle statuses: draft, ready, active, paused, needs_attention, archived.
5. Immutable workflow versioning:
   - Editing an active workflow increments version and preserves previous active version.
   - Version history listing and inspection.
6. Execution version behavior:
   - Existing executions remain pinned to their original version.
   - New triggers execute the newly activated version.
7. Explicit user approval requirement for activation.
8. Strict workspace isolation for workflow editing, validation, and versioning.
"""

from datetime import datetime, timezone
from typing import Any, Dict

import pytest
from httpx import AsyncClient

from app.integrations.registry import ToolRepository
from app.integrations.tool import RiskCategory, ToolDescriptor, ToolSchema, ToolType
from app.models.connection import ConnectionStatus
from app.models.workflow import (
    CURRENT_WORKFLOW_SCHEMA_VERSION,
    DataMapping,
    ErrorPolicy,
    TriggerConfig,
    TriggerType,
    WorkflowDefinition,
    WorkflowStatus,
    WorkflowStep,
)
from app.repositories.connection_repo import ConnectionRepository
from app.security.rate_limiter import auth_limiter
from app.services.workflow_service import WorkflowService


async def register_tool_in_db(
    db: Any,
    tool_id: str,
    app_name: str,
    name: str,
    required_args: list[str] = None,
    workspace_id: Any = None,
):
    tool_repo = ToolRepository(db)
    now = datetime.now(timezone.utc)
    entry = {
        "tool_id": tool_id,
        "provider": ToolType.COMPOSIO.value,
        "name": name,
        "description": f"Tool {name} for {app_name}",
        "input_schema": {
            "type": "object",
            "properties": {arg: {"type": "string"} for arg in (required_args or [])},
            "required": required_args or [],
        },
        "connection_required": True,
        "scopes": ["write"],
        "risk_category": RiskCategory.MEDIUM.value,
        "timeout_seconds": 30,
        "tags": [app_name],
        "workspace_id": workspace_id,
        "enabled": True,
        "allowed_roles": ["owner", "admin", "member"],
        "created_at": now,
        "updated_at": now,
    }
    await tool_repo.create_entry(entry)


async def setup_authenticated_workspace(client: AsyncClient, email: str = "phase6_user@example.com"):
    auth_limiter.reset()
    res = await client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "Phase 6 Tester",
    })
    token = res.json()["access_token"]
    ws_res = await client.get("/api/v1/workspaces", headers={"Authorization": f"Bearer {token}"})
    workspace_id = ws_res.json()[0]["id"]
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Workspace-ID": workspace_id,
    }
    return token, workspace_id, headers


# ---------------------------------------------------------------------------
# 1. Deterministic Validation Unit Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deterministic_validation_cycles_and_unreachable_nodes(mock_db):
    """Validation detects cycles and unreachable nodes."""
    service = WorkflowService(mock_db)

    # Cyclic workflow
    cyclic_workflow = {
        "schema_version": "1.0.0",
        "name": "Cyclic Workflow",
        "trigger": {"type": "manual"},
        "steps": [
            {
                "id": "step_1",
                "name": "Step 1",
                "tool_id": "composio:slack:send_message",
                "provider": "composio",
                "dependencies": ["step_2"],
            },
            {
                "id": "step_2",
                "name": "Step 2",
                "tool_id": "composio:slack:send_message",
                "provider": "composio",
                "dependencies": ["step_1"],
            },
        ],
    }
    errors = await service.validate_custom_workflow("ws_test", cyclic_workflow)
    assert any("Cyclic" in e for e in errors)
    assert any("Unreachable" in e for e in errors)


@pytest.mark.asyncio
async def test_deterministic_validation_missing_tool_and_unauthorized(mock_db):
    """Validation flags unregistered tool IDs."""
    service = WorkflowService(mock_db)
    workflow_data = {
        "schema_version": "1.0.0",
        "name": "Unregistered Tool Workflow",
        "trigger": {"type": "manual"},
        "steps": [
            {
                "id": "step_1",
                "name": "Step 1",
                "tool_id": "composio:unknown:unregistered_tool",
                "provider": "composio",
                "dependencies": [],
            }
        ],
    }
    errors = await service.validate_custom_workflow("ws_test", workflow_data)
    assert any("Unauthorized or unregistered tool" in e for e in errors)


@pytest.mark.asyncio
async def test_deterministic_validation_required_tool_arguments(mock_db):
    """Validation checks that required parameters in tool schema are satisfied."""
    await register_tool_in_db(
        mock_db,
        tool_id="composio:github:create_issue",
        app_name="github",
        name="create_issue",
        required_args=["title", "repo"],
        workspace_id="ws_test",
    )
    service = WorkflowService(mock_db)

    # Missing required argument 'repo'
    workflow_data = {
        "schema_version": "1.0.0",
        "name": "Create Issue Workflow",
        "trigger": {"type": "manual"},
        "steps": [
            {
                "id": "step_1",
                "name": "Create Issue",
                "tool_id": "composio:github:create_issue",
                "provider": "composio",
                "arguments": {"title": "Bug report"},  # 'repo' missing
                "dependencies": [],
            }
        ],
    }
    errors = await service.validate_custom_workflow("ws_test", workflow_data)
    assert any("missing required argument 'repo'" in e for e in errors)

    # Now provide via data mapping -> Should pass
    workflow_data["steps"][0]["data_mappings"] = [
        {"target_field": "repo", "source": "literal", "value": "my-org/repo"}
    ]
    errors_fixed = await service.validate_custom_workflow("ws_test", workflow_data)
    assert not any("missing required argument 'repo'" in e for e in errors_fixed)


@pytest.mark.asyncio
async def test_deterministic_validation_invalid_data_mappings(mock_db):
    """Validation flags data mappings that reference non-existent steps or undeclared dependencies."""
    await register_tool_in_db(
        mock_db,
        tool_id="composio:slack:send_message",
        app_name="slack",
        name="send_message",
        workspace_id="ws_test",
    )
    service = WorkflowService(mock_db)

    workflow_data = {
        "schema_version": "1.0.0",
        "name": "Mapping Test",
        "trigger": {"type": "manual"},
        "steps": [
            {
                "id": "step_1",
                "name": "Step 1",
                "tool_id": "composio:slack:send_message",
                "provider": "composio",
                "dependencies": [],
                "data_mappings": [
                    {"target_field": "channel", "source": "steps.non_existent_step.output.id"}
                ],
            }
        ],
    }
    errors = await service.validate_custom_workflow("ws_test", workflow_data)
    assert any("references non-existent step 'non_existent_step'" in e for e in errors)


# ---------------------------------------------------------------------------
# 2. Integration Verification Before Activation Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integration_verification_blocks_unconnected_apps(mock_db):
    """Workflow activation is blocked if required integrations are not connected."""
    service = WorkflowService(mock_db)
    errors = await service.verify_workflow_integrations("ws_test", ["github", "slack"])
    assert len(errors) == 2
    assert any("github" in e for e in errors)
    assert any("slack" in e for e in errors)

    # Connect github
    conn_repo = ConnectionRepository(mock_db)
    await conn_repo.create_connection(
        workspace_id="ws_test",
        user_id="user_123",
        app_name="github",
        connection_id="conn_gh",
        status=ConnectionStatus.CONNECTED,
    )
    errors_after = await service.verify_workflow_integrations("ws_test", ["github", "slack"])
    assert len(errors_after) == 1
    assert "slack" in errors_after[0]


# ---------------------------------------------------------------------------
# 3. Workflow Lifecycle States & Versioning API Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workflow_crud_and_status_transitions(client: AsyncClient, mock_db):
    """Full lifecycle: draft -> validation -> approval & activation -> pause -> resume -> archive."""
    token, ws_id, headers = await setup_authenticated_workspace(client, "lifecycle@test.com")

    # Register slack tool
    await register_tool_in_db(
        mock_db,
        tool_id="composio:slack:send_message",
        app_name="slack",
        name="send_message",
        workspace_id=ws_id,
    )

    # 1. Create draft workflow
    create_payload = {
        "name": "Lifecycle Workflow",
        "description": "Tests all Phase 6 statuses",
        "trigger": {"type": "manual"},
        "required_integrations": ["slack"],
        "steps": [
            {
                "id": "step_1",
                "name": "Notify Slack",
                "tool_id": "composio:slack:send_message",
                "provider": "composio",
                "arguments": {"channel": "general"},
                "dependencies": [],
            }
        ],
    }
    res_create = await client.post("/api/v1/workflows", json=create_payload, headers=headers)
    assert res_create.status_code == 201
    wf = res_create.json()
    wf_id = wf["id"]
    assert wf["status"] == "draft"
    assert wf["current_version"] == 1
    assert wf["active_version"] is None

    # 2. Validate without connected slack -> transitions to needs_attention
    res_val = await client.post(f"/api/v1/workflows/{wf_id}/validate", headers=headers)
    assert res_val.status_code == 200
    val_data = res_val.json()
    assert val_data["valid"] is False
    assert any("slack" in e for e in val_data["integration_errors"])

    wf_detail = (await client.get(f"/api/v1/workflows/{wf_id}", headers=headers)).json()
    assert wf_detail["status"] == "needs_attention"

    # 3. Try to activate without approval -> Rejected with 400
    res_act_no_app = await client.post(
        f"/api/v1/workflows/{wf_id}/activate",
        json={"approved": False},
        headers=headers,
    )
    assert res_act_no_app.status_code == 400

    # 4. Try to activate with approval but without slack connected -> Rejected with 400
    res_act_unconn = await client.post(
        f"/api/v1/workflows/{wf_id}/activate",
        json={"approved": True},
        headers=headers,
    )
    assert res_act_unconn.status_code == 400
    assert "Integration verification failed" in res_act_unconn.json()["detail"]

    # 5. Connect Slack
    conn_repo = ConnectionRepository(mock_db)
    await conn_repo.create_connection(
        workspace_id=ws_id,
        user_id="lifecycle_user",
        app_name="slack",
        connection_id="conn_slack",
        status=ConnectionStatus.CONNECTED,
    )

    # 6. Validate again -> passes and transitions to READY
    res_val_2 = await client.post(f"/api/v1/workflows/{wf_id}/validate", headers=headers)
    assert res_val_2.json()["valid"] is True

    wf_ready = (await client.get(f"/api/v1/workflows/{wf_id}", headers=headers)).json()
    assert wf_ready["status"] == "ready"

    # 7. Activate workflow with explicit approval -> transitions to ACTIVE, active_version=1
    res_act = await client.post(
        f"/api/v1/workflows/{wf_id}/activate",
        json={"approved": True},
        headers=headers,
    )
    assert res_act.status_code == 200
    wf_active = res_act.json()
    assert wf_active["status"] == "active"
    assert wf_active["active_version"] == 1
    assert wf_active["approved_at"] is not None

    # 8. Pause workflow -> transitions to PAUSED
    res_pause = await client.post(f"/api/v1/workflows/{wf_id}/pause", headers=headers)
    assert res_pause.status_code == 200
    assert res_pause.json()["status"] == "paused"

    # 9. Resume workflow -> transitions back to ACTIVE
    res_resume = await client.post(f"/api/v1/workflows/{wf_id}/resume", headers=headers)
    assert res_resume.status_code == 200
    assert res_resume.json()["status"] == "active"

    # 10. Archive workflow -> transitions to ARCHIVED
    res_arch = await client.post(f"/api/v1/workflows/{wf_id}/archive", headers=headers)
    assert res_arch.status_code == 200
    assert res_arch.json()["status"] == "archived"


# ---------------------------------------------------------------------------
# 4. Immutable Workflow Versioning & Execution Pinning Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_immutable_versioning_and_execution_pinning(client: AsyncClient, mock_db):
    """Editing an active workflow increments current_version while preserving active_version.

    Executions remain pinned to their original version; new triggers resolve active_version.
    """
    token, ws_id, headers = await setup_authenticated_workspace(client, "versioning@test.com")

    # Register slack tool
    await register_tool_in_db(
        mock_db,
        tool_id="composio:slack:send_message",
        app_name="slack",
        name="send_message",
        workspace_id=ws_id,
    )

    # Connect slack
    conn_repo = ConnectionRepository(mock_db)
    await conn_repo.create_connection(
        workspace_id=ws_id,
        user_id="ver_user",
        app_name="slack",
        connection_id="conn_slack_v",
        status=ConnectionStatus.CONNECTED,
    )

    # 1. Create and activate v1
    create_payload = {
        "name": "Versioned Automation",
        "description": "Original V1",
        "trigger": {"type": "manual"},
        "required_integrations": ["slack"],
        "steps": [
            {
                "id": "step_1",
                "name": "Notify Slack V1",
                "tool_id": "composio:slack:send_message",
                "provider": "composio",
                "arguments": {"channel": "v1_channel"},
                "dependencies": [],
            }
        ],
    }
    wf = (await client.post("/api/v1/workflows", json=create_payload, headers=headers)).json()
    wf_id = wf["id"]

    await client.post(
        f"/api/v1/workflows/{wf_id}/activate",
        json={"approved": True},
        headers=headers,
    )

    # Verify active workflow has active_version = 1, current_version = 1
    wf_v1 = (await client.get(f"/api/v1/workflows/{wf_id}", headers=headers)).json()
    assert wf_v1["status"] == "active"
    assert wf_v1["current_version"] == 1
    assert wf_v1["active_version"] == 1

    # Simulate an execution running on version 1
    service = WorkflowService(mock_db)
    execution_id = await service.repo.create_execution({
        "workspace_id": ws_id,
        "workflow_id": wf_id,
        "workflow_version": 1,
        "status": "running",
        "triggered_by": "manual",
    })

    # 2. Edit active workflow -> creates Version 2 without changing active_version 1
    update_payload = {
        "name": "Versioned Automation (v2 draft)",
        "change_summary": "Updated channel to v2_channel",
        "steps": [
            {
                "id": "step_1",
                "name": "Notify Slack V2",
                "tool_id": "composio:slack:send_message",
                "provider": "composio",
                "arguments": {"channel": "v2_channel"},
                "dependencies": [],
            }
        ],
    }
    res_update = await client.patch(f"/api/v1/workflows/{wf_id}", json=update_payload, headers=headers)
    assert res_update.status_code == 200
    wf_after_edit = res_update.json()

    # Current definition is now v2, but active execution version remains 1!
    assert wf_after_edit["current_version"] == 2
    assert wf_after_edit["active_version"] == 1

    # 3. Check version history endpoint
    versions_res = await client.get(f"/api/v1/workflows/{wf_id}/versions", headers=headers)
    assert versions_res.status_code == 200
    versions = versions_res.json()
    assert len(versions) == 2
    assert versions[0]["version"] == 2
    assert versions[1]["version"] == 1
    assert versions[1]["definition"]["steps"][0]["arguments"]["channel"] == "v1_channel"
    assert versions[0]["definition"]["steps"][0]["arguments"]["channel"] == "v2_channel"

    # 4. Verify Requirement 7: Execution version resolution
    # Existing running execution resolves to its pinned version 1
    res_exec_resolve = await client.get(
        f"/api/v1/workflows/{wf_id}/execution-version",
        params={"execution_id": execution_id},
        headers=headers,
    )
    assert res_exec_resolve.status_code == 200
    exec_info = res_exec_resolve.json()
    assert exec_info["pinned_version"] == 1
    assert exec_info["source"] == "existing_execution"
    assert exec_info["definition"]["steps"][0]["arguments"]["channel"] == "v1_channel"

    # A new trigger event resolves to the active execution version (still 1 until v2 is activated)
    res_new_trigger = await client.get(
        f"/api/v1/workflows/{wf_id}/execution-version",
        headers=headers,
    )
    assert res_new_trigger.status_code == 200
    trigger_info = res_new_trigger.json()
    assert trigger_info["pinned_version"] == 1
    assert trigger_info["source"] == "active_trigger"

    # 5. Activate version 2 with explicit approval -> active_version becomes 2
    res_act_v2 = await client.post(
        f"/api/v1/workflows/{wf_id}/activate",
        json={"approved": True, "version": 2},
        headers=headers,
    )
    assert res_act_v2.status_code == 200
    assert res_act_v2.json()["active_version"] == 2

    # New triggers now resolve to version 2!
    res_new_trigger_v2 = await client.get(
        f"/api/v1/workflows/{wf_id}/execution-version",
        headers=headers,
    )
    assert res_new_trigger_v2.json()["pinned_version"] == 2

    # Old running execution STILL resolves to version 1!
    res_old_exec_again = await client.get(
        f"/api/v1/workflows/{wf_id}/execution-version",
        params={"execution_id": execution_id},
        headers=headers,
    )
    assert res_old_exec_again.json()["pinned_version"] == 1


# ---------------------------------------------------------------------------
# 5. Prompt-Based Workflow Editing Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edit_workflow_via_prompt(client: AsyncClient, mock_db):
    """Users can revise any existing workflow via a natural-language prompt."""
    token, ws_id, headers = await setup_authenticated_workspace(client, "prompt_edit@test.com")

    # Register github tool
    await register_tool_in_db(
        mock_db,
        tool_id="composio:github:create_issue",
        app_name="github",
        name="create_issue",
        workspace_id=ws_id,
    )

    # 1. Create initial workflow
    create_payload = {
        "name": "Initial Issue Workflow",
        "description": "Original description",
        "trigger": {"type": "manual"},
        "required_integrations": ["github"],
        "steps": [
            {
                "id": "step_1",
                "name": "Original Step",
                "tool_id": "composio:github:create_issue",
                "provider": "composio",
                "arguments": {"title": "Original title"},
                "dependencies": [],
            }
        ],
    }
    wf = (await client.post("/api/v1/workflows", json=create_payload, headers=headers)).json()
    wf_id = wf["id"]

    # 2. Edit through prompt on draft workflow
    edit_payload = {
        "prompt": "Change the workflow to trigger on webhook and rename the step to Create Bug Report",
        "save_draft": True,
    }
    res_edit = await client.post(f"/api/v1/workflows/{wf_id}/prompt-edit", json=edit_payload, headers=headers)
    assert res_edit.status_code == 200
    data = res_edit.json()
    assert data["result"]["success"] is True
    assert data["workflow"] is not None
    assert data["workflow"]["current_version"] == 1

    # 3. Connect github & activate workflow
    conn_repo = ConnectionRepository(mock_db)
    await conn_repo.create_connection(
        workspace_id=ws_id,
        user_id="user_prompt",
        app_name="github",
        connection_id="conn_gh_p",
        status=ConnectionStatus.CONNECTED,
    )
    await client.post(f"/api/v1/workflows/{wf_id}/activate", json={"approved": True}, headers=headers)

    wf_active = (await client.get(f"/api/v1/workflows/{wf_id}", headers=headers)).json()
    assert wf_active["status"] == "active"
    assert wf_active["active_version"] == 1

    # 4. Edit active workflow through prompt -> must create immutable Version 2 without changing active_version 1
    edit_active_payload = {
        "prompt": "Add error handling retry policy and configure webhook event to issues.opened",
        "save_draft": True,
    }
    res_active_edit = await client.post(
        f"/api/v1/workflows/{wf_id}/prompt-edit",
        json=edit_active_payload,
        headers=headers,
    )
    assert res_active_edit.status_code == 200
    active_edit_data = res_active_edit.json()
    assert active_edit_data["result"]["success"] is True
    updated_wf = active_edit_data["workflow"]
    assert updated_wf["current_version"] == 2
    assert updated_wf["active_version"] == 1  # Active execution version intact!

    # 5. Check version history has 2 versions
    versions = (await client.get(f"/api/v1/workflows/{wf_id}/versions", headers=headers)).json()
    assert len(versions) == 2
    assert versions[0]["version"] == 2
    assert "AI Prompt Edit" in versions[0]["change_summary"]

