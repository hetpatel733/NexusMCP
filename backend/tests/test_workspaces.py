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
async def test_workspace_creation_and_listing(client: AsyncClient):
    user_data = await register_user(client, "alice@workspaces.com", "Alice W")
    token = user_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # List default workspace created upon registration
    res = await client.get("/api/v1/workspaces", headers=headers)
    assert res.status_code == 200
    workspaces = res.json()
    assert len(workspaces) == 1
    assert workspaces[0]["role"] == "owner"

    # Create new custom workspace
    create_res = await client.post(
        "/api/v1/workspaces",
        json={"name": "Engineering Team", "description": "Dev workflows"},
        headers=headers
    )
    assert create_res.status_code == 201
    created_ws = create_res.json()
    assert created_ws["name"] == "Engineering Team"
    assert created_ws["role"] == "owner"
    assert "slug" in created_ws

    # Verify listing now has 2 workspaces
    list_res = await client.get("/api/v1/workspaces", headers=headers)
    assert len(list_res.json()) == 2


@pytest.mark.asyncio
async def test_workspace_membership_management(client: AsyncClient):
    alice = await register_user(client, "alice@team.com", "Alice Team")
    bob = await register_user(client, "bob@team.com", "Bob Team")

    alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
    bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}

    # Alice creates a workspace
    create_res = await client.post(
        "/api/v1/workspaces",
        json={"name": "Acme Corp"},
        headers=alice_headers
    )
    ws_id = create_res.json()["id"]

    # Bob cannot view this workspace yet (not a member)
    bob_view = await client.get(f"/api/v1/workspaces/{ws_id}", headers=bob_headers)
    assert bob_view.status_code == 403

    # Alice invites Bob as Member
    add_res = await client.post(
        f"/api/v1/workspaces/{ws_id}/members",
        json={"email": "bob@team.com", "role": "member"},
        headers=alice_headers
    )
    assert add_res.status_code == 201
    assert add_res.json()["email"] == "bob@team.com"
    assert add_res.json()["role"] == "member"

    # Bob can now view the workspace
    bob_view_after = await client.get(f"/api/v1/workspaces/{ws_id}", headers=bob_headers)
    assert bob_view_after.status_code == 200
    assert bob_view_after.json()["role"] == "member"

    # Bob (Member) cannot invite Charlie
    await register_user(client, "charlie@team.com", "Charlie Team")
    bob_invite = await client.post(
        f"/api/v1/workspaces/{ws_id}/members",
        json={"email": "charlie@team.com", "role": "member"},
        headers=bob_headers
    )
    assert bob_invite.status_code == 403

    # Alice promotes Bob to Admin
    promote_res = await client.patch(
        f"/api/v1/workspaces/{ws_id}/members/{bob['user']['id']}",
        json={"role": "admin"},
        headers=alice_headers
    )
    assert promote_res.status_code == 200

    # Bob (now Admin) can invite Charlie
    bob_invite_success = await client.post(
        f"/api/v1/workspaces/{ws_id}/members",
        json={"email": "charlie@team.com", "role": "member"},
        headers=bob_headers
    )
    assert bob_invite_success.status_code == 201


@pytest.mark.asyncio
async def test_sole_owner_protection(client: AsyncClient):
    alice = await register_user(client, "owner@corp.com", "Solo Owner")
    alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}

    create_res = await client.post(
        "/api/v1/workspaces",
        json={"name": "Solo Org"},
        headers=alice_headers
    )
    ws_id = create_res.json()["id"]

    # Trying to demote sole owner should fail
    demote_res = await client.patch(
        f"/api/v1/workspaces/{ws_id}/members/{alice['user']['id']}",
        json={"role": "member"},
        headers=alice_headers
    )
    assert demote_res.status_code == 400
    assert "Cannot demote the sole owner" in demote_res.json()["detail"]

    # Trying to remove sole owner should fail
    remove_res = await client.delete(
        f"/api/v1/workspaces/{ws_id}/members/{alice['user']['id']}",
        headers=alice_headers
    )
    assert remove_res.status_code == 400
    assert "Cannot remove the sole owner" in remove_res.json()["detail"]


@pytest.mark.asyncio
async def test_workspace_deletion(client: AsyncClient):
    alice = await register_user(client, "alice@del.com", "Alice Del")
    bob = await register_user(client, "bob@del.com", "Bob Del")

    alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
    bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}

    create_res = await client.post(
        "/api/v1/workspaces",
        json={"name": "To Delete"},
        headers=alice_headers
    )
    ws_id = create_res.json()["id"]

    # Add Bob as admin
    await client.post(
        f"/api/v1/workspaces/{ws_id}/members",
        json={"email": "bob@del.com", "role": "admin"},
        headers=alice_headers
    )

    # Bob (Admin) tries to delete workspace -> Rejected
    bob_del = await client.delete(f"/api/v1/workspaces/{ws_id}", headers=bob_headers)
    assert bob_del.status_code == 403

    # Alice (Owner) deletes workspace -> Success
    alice_del = await client.delete(f"/api/v1/workspaces/{ws_id}", headers=alice_headers)
    assert alice_del.status_code == 200

    # Workspace should no longer be found
    get_res = await client.get(f"/api/v1/workspaces/{ws_id}", headers=alice_headers)
    assert get_res.status_code == 403 or get_res.status_code == 404
