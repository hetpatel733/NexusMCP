from typing import Any, cast

import pytest
from httpx import AsyncClient

from app.security.rate_limiter import auth_limiter


async def register_user(client: AsyncClient, email: str, name: str) -> dict[str, Any]:
    auth_limiter.reset()
    res = await client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": name
    })
    return cast(dict[str, Any], res.json())


@pytest.mark.asyncio
async def test_rbac_role_permissions(client: AsyncClient):
    # Setup users
    owner_data = await register_user(client, "owner@corp.com", "The Owner")
    admin_data = await register_user(client, "admin@corp.com", "The Admin")
    member_data = await register_user(client, "member@corp.com", "The Member")
    await register_user(client, "stranger@corp.com", "The Stranger")

    owner_headers = {"Authorization": f"Bearer {owner_data['access_token']}"}
    admin_headers = {"Authorization": f"Bearer {admin_data['access_token']}"}
    member_headers = {"Authorization": f"Bearer {member_data['access_token']}"}

    # 1. Owner creates workspace
    ws_res = await client.post(
        "/api/v1/workspaces",
        json={"name": "RBAC Enterprise"},
        headers=owner_headers
    )
    assert ws_res.status_code == 201
    ws_id = ws_res.json()["id"]

    # Add workspace ID to all headers
    owner_headers["X-Workspace-ID"] = ws_id
    admin_headers["X-Workspace-ID"] = ws_id
    member_headers["X-Workspace-ID"] = ws_id

    # 2. Owner adds Admin and Member
    add_admin = await client.post(
        f"/api/v1/workspaces/{ws_id}/members",
        json={"email": "admin@corp.com", "role": "admin"},
        headers=owner_headers
    )
    assert add_admin.status_code == 201

    add_member = await client.post(
        f"/api/v1/workspaces/{ws_id}/members",
        json={"email": "member@corp.com", "role": "member"},
        headers=owner_headers
    )
    assert add_member.status_code == 201

    # 3. Test Member restrictions
    # Member cannot view audit logs
    member_audit = await client.get("/api/v1/audit-logs", headers=member_headers)
    assert member_audit.status_code == 403

    # Member cannot invite new members
    member_invite = await client.post(
        f"/api/v1/workspaces/{ws_id}/members",
        json={"email": "stranger@corp.com", "role": "member"},
        headers=member_headers
    )
    assert member_invite.status_code == 403

    # Member cannot delete workspace
    member_delete_ws = await client.delete(f"/api/v1/workspaces/{ws_id}", headers=member_headers)
    assert member_delete_ws.status_code == 403

    # Member CAN create a workflow
    member_create_wf = await client.post(
        "/api/v1/workflows",
        json={"name": "Member Workflow"},
        headers=member_headers
    )
    assert member_create_wf.status_code == 201
    wf_id = member_create_wf.json()["id"]

    # 4. Test Admin permissions & restrictions
    # Admin CAN view audit logs
    admin_audit = await client.get("/api/v1/audit-logs", headers=admin_headers)
    assert admin_audit.status_code == 200

    # Admin CAN invite members
    admin_invite = await client.post(
        f"/api/v1/workspaces/{ws_id}/members",
        json={"email": "stranger@corp.com", "role": "member"},
        headers=admin_headers
    )
    assert admin_invite.status_code == 201

    # Admin CANNOT add an Owner
    admin_add_owner = await client.post(
        f"/api/v1/workspaces/{ws_id}/members",
        json={"email": "another@corp.com", "role": "owner"},
        headers=admin_headers
    )
    assert admin_add_owner.status_code in [403, 404]

    # Admin CANNOT change member roles
    admin_change_role = await client.patch(
        f"/api/v1/workspaces/{ws_id}/members/{member_data['user']['id']}",
        json={"role": "admin"},
        headers=admin_headers
    )
    assert admin_change_role.status_code == 403

    # Admin CANNOT remove Owner
    admin_remove_owner = await client.delete(
        f"/api/v1/workspaces/{ws_id}/members/{owner_data['user']['id']}",
        headers=admin_headers
    )
    assert admin_remove_owner.status_code == 403

    # Admin CANNOT delete workspace
    admin_del_ws = await client.delete(f"/api/v1/workspaces/{ws_id}", headers=admin_headers)
    assert admin_del_ws.status_code == 403

    # Admin CAN delete workflow
    admin_del_wf = await client.delete(f"/api/v1/workflows/{wf_id}", headers=admin_headers)
    assert admin_del_wf.status_code == 200

    # 5. Owner has full control
    # Owner CAN change member role
    owner_promote = await client.patch(
        f"/api/v1/workspaces/{ws_id}/members/{member_data['user']['id']}",
        json={"role": "admin"},
        headers=owner_headers
    )
    assert owner_promote.status_code == 200
