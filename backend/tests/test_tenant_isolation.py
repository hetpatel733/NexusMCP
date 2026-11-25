import pytest
from httpx import AsyncClient

from app.repositories.workflow_repo import WorkflowRepository
from app.security.rate_limiter import auth_limiter


async def setup_two_tenants(client: AsyncClient):
    auth_limiter.reset()
    # Register Tenant A
    res_a = await client.post("/api/v1/auth/register", json={
        "email": "user_a@tenant-a.com",
        "password": "Password123!",
        "full_name": "Tenant A User"
    })
    data_a = res_a.json()
    token_a = data_a["access_token"]

    # Register Tenant B
    res_b = await client.post("/api/v1/auth/register", json={
        "email": "user_b@tenant-b.com",
        "password": "Password123!",
        "full_name": "Tenant B User"
    })
    data_b = res_b.json()
    token_b = data_b["access_token"]

    # Get Workspace A
    ws_a_list = await client.get("/api/v1/workspaces", headers={"Authorization": f"Bearer {token_a}"})
    ws_a_id = ws_a_list.json()[0]["id"]

    # Get Workspace B
    ws_b_list = await client.get("/api/v1/workspaces", headers={"Authorization": f"Bearer {token_b}"})
    ws_b_id = ws_b_list.json()[0]["id"]

    return {
        "user_a": {"token": token_a, "workspace_id": ws_a_id},
        "user_b": {"token": token_b, "workspace_id": ws_b_id},
    }


@pytest.mark.asyncio
async def test_cross_workspace_workflow_isolation(client: AsyncClient):
    tenants = await setup_two_tenants(client)
    user_a = tenants["user_a"]
    user_b = tenants["user_b"]

    headers_a = {
        "Authorization": f"Bearer {user_a['token']}",
        "X-Workspace-ID": user_a["workspace_id"]
    }
    headers_b = {
        "Authorization": f"Bearer {user_b['token']}",
        "X-Workspace-ID": user_b["workspace_id"]
    }

    # Tenant A creates workflow in Workspace A
    wf_create = await client.post(
        "/api/v1/workflows",
        json={"name": "Tenant A Secret Workflow", "description": "Confidential data"},
        headers=headers_a
    )
    assert wf_create.status_code == 201
    wf_a = wf_create.json()
    wf_a_id = wf_a["id"]
    assert wf_a["workspace_id"] == user_a["workspace_id"]

    # Tenant B tries to GET Workflow A -> Must return 404 (isolation)
    cross_get = await client.get(f"/api/v1/workflows/{wf_a_id}", headers=headers_b)
    assert cross_get.status_code == 404

    # Tenant B lists workflows -> Must NOT include Workflow A
    b_list = await client.get("/api/v1/workflows", headers=headers_b)
    assert b_list.status_code == 200
    b_workflows = b_list.json()
    assert all(w["id"] != wf_a_id for w in b_workflows)

    # Tenant B tries to update Workflow A -> Must return 404
    cross_patch = await client.patch(
        f"/api/v1/workflows/{wf_a_id}",
        json={"name": "Hacked Workflow"},
        headers=headers_b
    )
    assert cross_patch.status_code == 404

    # Tenant B tries to delete Workflow A -> Must return 404
    cross_delete = await client.delete(f"/api/v1/workflows/{wf_a_id}", headers=headers_b)
    assert cross_delete.status_code == 404

    # Tenant A can successfully get, update, and delete their own workflow
    a_get = await client.get(f"/api/v1/workflows/{wf_a_id}", headers=headers_a)
    assert a_get.status_code == 200
    assert a_get.json()["name"] == "Tenant A Secret Workflow"


@pytest.mark.asyncio
async def test_cross_workspace_header_forgery_rejected(client: AsyncClient):
    tenants = await setup_two_tenants(client)
    user_a = tenants["user_a"]
    user_b = tenants["user_b"]

    # Tenant B attempts to use Tenant A's workspace ID in X-Workspace-ID header
    forged_headers = {
        "Authorization": f"Bearer {user_b['token']}",
        "X-Workspace-ID": user_a["workspace_id"]
    }

    res = await client.get("/api/v1/workflows", headers=forged_headers)
    assert res.status_code == 403
    assert "not a member of this workspace" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_repository_level_tenant_isolation(mock_db):
    repo = WorkflowRepository(mock_db)
    ws_1 = "ws-tenant-1"
    ws_2 = "ws-tenant-2"

    # Create workflow in ws_1
    wf_id = await repo.create({
        "workspace_id": ws_1,
        "name": "Repo Isolated Workflow",
        "status": "draft"
    })

    # Query with correct workspace
    found = await repo.get_workflow(ws_1, wf_id)
    assert found is not None
    assert found["name"] == "Repo Isolated Workflow"

    # Query with wrong workspace -> None
    wrong_found = await repo.get_workflow(ws_2, wf_id)
    assert wrong_found is None

    # Update with wrong workspace -> False
    update_res = await repo.update_workflow(ws_2, wf_id, {"name": "Tampered"})
    assert update_res is False

    # Delete with wrong workspace -> False
    del_res = await repo.delete_workflow(ws_2, wf_id)
    assert del_res is False

    # Verify document was untouched
    verify = await repo.get_workflow(ws_1, wf_id)
    assert verify is not None
    assert verify["name"] == "Repo Isolated Workflow"
