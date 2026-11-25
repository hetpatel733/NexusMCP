"""Unit tests for Phase 3 Composio integration and connection management.

Uses mocked Composio provider responses to test connection creation, status
retrieval, disconnect/revocation, workspace/user isolation, and invalid
connection detection without requiring a real Composio account.
"""

from typing import Any, cast

import pytest
from httpx import AsyncClient

from app.security.rate_limiter import auth_limiter


async def register_user(client: AsyncClient, email: str, name: str) -> dict[str, Any]:
    auth_limiter.reset()
    res = await client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": name,
    })
    return cast(dict[str, Any], res.json())


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_connection_create_and_list(client: AsyncClient, monkeypatch):
    """Test initiating a connection and listing it."""
    user_data = await register_user(client, "conn-alice@example.com", "Conn Alice")
    token = user_data["access_token"]
    headers = auth_headers(token)

    # Mock the provider's connection initiation so the real service persists to DB
    from app.integrations.composio_adapter import ComposioAdapter

    async def fake_initiate(
        self,
        app_name: str,
        external_user_id: str,
        redirect_url: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict:
        return {
            "connection_id": "provider-conn-abc",
            "redirect_url": "https://composio.dev/auth/connect?x=1",
            "app_name": app_name,
            "external_user_id": external_user_id,
        }

    monkeypatch.setattr(ComposioAdapter, "create_connection_link", fake_initiate)

    res = await client.post(
        "/api/v1/connections",
        json={"app_name": "github"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["app_name"] == "github"
    assert body["status"] == "action_required"
    assert "redirect_url" in body

    # List connections - persisted via real service
    list_res = await client.get("/api/v1/connections", headers=headers)
    assert list_res.status_code == 200, list_res.text
    connections = list_res.json()
    assert isinstance(connections, list)
    assert len(connections) >= 1
    assert connections[0]["app_name"] == "github"


@pytest.mark.asyncio
async def test_connection_duplicate_rejected(client: AsyncClient, monkeypatch):
    """Test that duplicate connections for the same app are rejected."""
    user_data = await register_user(client, "conn-dupe@example.com", "Conn Dupe")
    token = user_data["access_token"]
    headers = auth_headers(token)

    from app.integrations.composio_adapter import ComposioAdapter

    async def fake_initiate(
        self,
        app_name: str,
        external_user_id: str,
        redirect_url: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict:
        return {
            "connection_id": "provider-conn",
            "redirect_url": "https://composio.dev/auth",
            "app_name": app_name,
            "external_user_id": external_user_id,
        }

    monkeypatch.setattr(ComposioAdapter, "create_connection_link", fake_initiate)

    # First connection
    res1 = await client.post(
        "/api/v1/connections",
        json={"app_name": "slack"},
        headers=headers,
    )
    assert res1.status_code == 201, res1.text

    # Second connection to same app - service's count_by_workspace_and_app
    # should detect the existing record in the mock DB.
    res2 = await client.post(
        "/api/v1/connections",
        json={"app_name": "slack"},
        headers=headers,
    )
    assert res2.status_code == 409, res2.text
    assert "already exists" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_connection_workspace_isolation(client: AsyncClient, monkeypatch):
    """Test that connections are isolated by workspace."""
    alice = await register_user(client, "iso-alice@example.com", "Iso Alice")
    bob = await register_user(client, "iso-bob@example.com", "Iso Bob")

    alice_headers = auth_headers(alice["access_token"])
    bob_headers = auth_headers(bob["access_token"])

    from app.integrations.composio_adapter import ComposioAdapter

    async def fake_initiate(
        self,
        app_name: str,
        external_user_id: str,
        redirect_url: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict:
        return {
            "connection_id": f"provider-{external_user_id}",
            "redirect_url": "https://composio.dev/auth",
            "app_name": app_name,
            "external_user_id": external_user_id,
        }

    monkeypatch.setattr(ComposioAdapter, "create_connection_link", fake_initiate)

    # Alice connects to github in her workspace
    res = await client.post(
        "/api/v1/connections",
        json={"app_name": "github"},
        headers=alice_headers,
    )
    assert res.status_code == 201

    # Bob lists connections - should not see Alice's
    list_res = await client.get("/api/v1/connections", headers=bob_headers)
    assert list_res.status_code == 200
    bob_connections = list_res.json()
    bob_app_names = [c["app_name"] for c in bob_connections]
    assert "github" not in bob_app_names


@pytest.mark.asyncio
async def test_connection_status_retrieval(client: AsyncClient, monkeypatch):
    """Test getting connection status."""
    user_data = await register_user(client, "status-alice@example.com", "Status Alice")
    token = user_data["access_token"]
    headers = auth_headers(token)

    from app.integrations.composio_adapter import ComposioAdapter

    async def fake_initiate(
        self,
        app_name: str,
        external_user_id: str,
        redirect_url: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict:
        return {
            "connection_id": "provider-status-1",
            "redirect_url": "https://composio.dev/auth",
            "app_name": app_name,
            "external_user_id": external_user_id,
        }

    async def fake_get_status(
        self,
        external_user_id: str,
        connection_id: str,
    ) -> dict:
        return {
            "connection_id": connection_id,
            "status": "connected",
            "app_name": "github",
        }

    monkeypatch.setattr(ComposioAdapter, "create_connection_link", fake_initiate)
    monkeypatch.setattr(ComposioAdapter, "get_connection_status", fake_get_status)

    res = await client.post(
        "/api/v1/connections",
        json={"app_name": "github"},
        headers=headers,
    )
    assert res.status_code == 201
    conn_id = res.json()["id"]

    status_res = await client.get(
        f"/api/v1/connections/{conn_id}/status",
        headers=headers,
    )
    assert status_res.status_code == 200
    assert status_res.json()["status"] == "connected"


@pytest.mark.asyncio
async def test_connection_revocation(client: AsyncClient, monkeypatch):
    """Test disconnecting/revoking a connection."""
    user_data = await register_user(client, "revoke-alice@example.com", "Revoke Alice")
    token = user_data["access_token"]
    headers = auth_headers(token)

    from app.integrations.composio_adapter import ComposioAdapter

    async def fake_initiate(
        self,
        app_name: str,
        external_user_id: str,
        redirect_url: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict:
        return {
            "connection_id": "provider-revoke-1",
            "redirect_url": "https://composio.dev/auth",
            "app_name": app_name,
            "external_user_id": external_user_id,
        }

    async def fake_revoke(
        self,
        external_user_id: str,
        connection_id: str,
    ) -> dict:
        return {"connection_id": connection_id, "status": "revoked"}

    monkeypatch.setattr(ComposioAdapter, "create_connection_link", fake_initiate)
    monkeypatch.setattr(ComposioAdapter, "revoke_connection", fake_revoke)

    res = await client.post(
        "/api/v1/connections",
        json={"app_name": "github"},
        headers=headers,
    )
    assert res.status_code == 201
    conn_id = res.json()["id"]

    del_res = await client.delete(
        f"/api/v1/connections/{conn_id}",
        headers=headers,
    )
    assert del_res.status_code == 200, del_res.text
    assert del_res.json()["status"] == "disconnected"


@pytest.mark.asyncio
async def test_connection_not_found(client: AsyncClient, monkeypatch):
    """Test that accessing a non-existent connection returns 404."""
    user_data = await register_user(client, "notfound-alice@example.com", "NotFound Alice")
    token = user_data["access_token"]
    headers = auth_headers(token)

    res = await client.get(
        "/api/v1/connections/nonexistent-id/status",
        headers=headers,
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_unauthenticated_connection_access(client: AsyncClient):
    """Test that unauthenticated requests are rejected."""
    res = await client.get("/api/v1/connections")
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_connection_invalid_status_detected(client: AsyncClient, monkeypatch):
    """Test that an invalid/unknown provider connection status is surfaced."""
    user_data = await register_user(client, "invalid-alice@example.com", "Invalid Alice")
    token = user_data["access_token"]
    headers = auth_headers(token)

    from app.integrations.composio_adapter import ComposioAdapter

    async def fake_initiate(
        self,
        app_name: str,
        external_user_id: str,
        redirect_url: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict:
        return {
            "connection_id": "provider-invalid-1",
            "redirect_url": "https://composio.dev/auth",
            "app_name": "github",
            "external_user_id": external_user_id,
        }

    async def fake_get_status(
        self,
        external_user_id: str,
        connection_id: str,
    ) -> dict:
        return {
            "connection_id": connection_id,
            "status": "expired",
            "app_name": "github",
        }

    monkeypatch.setattr(ComposioAdapter, "create_connection_link", fake_initiate)
    monkeypatch.setattr(ComposioAdapter, "get_connection_status", fake_get_status)

    res = await client.post(
        "/api/v1/connections",
        json={"app_name": "github"},
        headers=headers,
    )
    assert res.status_code == 201
    conn_id = res.json()["id"]

    status_res = await client.get(
        f"/api/v1/connections/{conn_id}/status",
        headers=headers,
    )
    assert status_res.status_code == 200
    assert status_res.json()["status"] == "expired"
