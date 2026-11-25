import pytest
from httpx import AsyncClient

from app.repositories.audit_repo import redact_sensitive_data
from app.security.rate_limiter import auth_limiter


def test_redact_sensitive_data():
    sample = {
        "email": "user@example.com",
        "password": "SuperSecretPassword123!",
        "refresh_token": "secret.jwt.token",
        "nested": {
            "token": "bearer-token",
            "name": "safe name"
        }
    }
    redacted = redact_sensitive_data(sample)
    assert redacted["email"] == "user@example.com"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["refresh_token"] == "[REDACTED]"
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["nested"]["name"] == "safe name"


@pytest.mark.asyncio
async def test_audit_logs_recorded_and_isolated(client: AsyncClient, mock_db):
    auth_limiter.reset()
    # 1. Register Alice
    reg_res = await client.post("/api/v1/auth/register", json={
        "email": "alice@audit.com",
        "password": "Password123!",
        "full_name": "Alice Auditor"
    })
    alice_data = reg_res.json()
    alice_token = alice_data["access_token"]

    # Get Alice's workspace
    ws_list = await client.get("/api/v1/workspaces", headers={"Authorization": f"Bearer {alice_token}"})
    alice_ws_id = ws_list.json()[0]["id"]
    alice_headers = {
        "Authorization": f"Bearer {alice_token}",
        "X-Workspace-ID": alice_ws_id
    }

    # 2. Trigger failed login for Alice
    await client.post("/api/v1/auth/login", json={
        "email": "alice@audit.com",
        "password": "WrongPassword!"
    })

    # 3. Create a workflow in Alice's workspace
    wf_res = await client.post(
        "/api/v1/workflows",
        json={"name": "Audited Workflow", "status": "active"},
        headers=alice_headers
    )
    assert wf_res.status_code == 201

    # 4. Fetch audit logs as Alice (Owner)
    logs_res = await client.get("/api/v1/audit-logs", headers=alice_headers)
    assert logs_res.status_code == 200
    logs = logs_res.json()
    actions = [item["action"] for item in logs]

    assert "auth.register" in actions or "workspace.create" in actions
    assert "workflow.create" in actions

    # Verify no plaintext passwords or raw secrets appear in log details
    for log_item in logs:
        details_str = str(log_item.get("details", {})).lower()
        assert "supersecretpassword" not in details_str
        assert "password123!" not in details_str

    # 5. Verify tenant isolation of audit logs
    # Register Bob in his own workspace
    bob_res = await client.post("/api/v1/auth/register", json={
        "email": "bob@audit.com",
        "password": "Password123!",
        "full_name": "Bob Auditor"
    })
    bob_token = bob_res.json()["access_token"]
    bob_ws_list = await client.get("/api/v1/workspaces", headers={"Authorization": f"Bearer {bob_token}"})
    bob_ws_id = bob_ws_list.json()[0]["id"]
    bob_headers = {
        "Authorization": f"Bearer {bob_token}",
        "X-Workspace-ID": bob_ws_id
    }

    bob_logs = await client.get("/api/v1/audit-logs", headers=bob_headers)
    assert bob_logs.status_code == 200
    # Bob should NOT see Alice's events
    for item in bob_logs.json():
        assert item["workspace_id"] == bob_ws_id
