import pytest
from httpx import AsyncClient

from app.security.password import hash_password, verify_password
from app.security.rate_limiter import auth_limiter


def test_password_hashing():
    pwd = "SecurePassword123!"
    hashed = hash_password(pwd)
    assert hashed != pwd
    assert verify_password(pwd, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False


@pytest.mark.asyncio
async def test_register_success(client: AsyncClient):
    auth_limiter.reset()
    payload = {
        "email": "alice@example.com",
        "password": "Password123!",
        "full_name": "Alice Cooper"
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "alice@example.com"
    assert data["user"]["full_name"] == "Alice Cooper"
    assert "password" not in data["user"]
    assert "hashed_password" not in data["user"]


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient):
    auth_limiter.reset()
    payload = {
        "email": "duplicate@example.com",
        "password": "Password123!",
        "full_name": "Duplicate User"
    }
    res1 = await client.post("/api/v1/auth/register", json=payload)
    assert res1.status_code == 201

    res2 = await client.post("/api/v1/auth/register", json=payload)
    assert res2.status_code == 409
    assert "already exists" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_register_invalid_password(client: AsyncClient):
    auth_limiter.reset()
    payload = {
        "email": "short@example.com",
        "password": "short",
        "full_name": "Short Pwd"
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_success_and_me(client: AsyncClient):
    auth_limiter.reset()
    reg_payload = {
        "email": "bob@example.com",
        "password": "Password123!",
        "full_name": "Bob Builder"
    }
    await client.post("/api/v1/auth/register", json=reg_payload)

    login_res = await client.post("/api/v1/auth/login", json={
        "email": "bob@example.com",
        "password": "Password123!"
    })
    assert login_res.status_code == 200
    tokens = login_res.json()
    access_token = tokens["access_token"]

    # Test /auth/me with Bearer token
    me_res = await client.get("/api/v1/auth/me", headers={
        "Authorization": f"Bearer {access_token}"
    })
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "bob@example.com"


@pytest.mark.asyncio
async def test_login_failed_credentials(client: AsyncClient):
    auth_limiter.reset()
    reg_payload = {
        "email": "charlie@example.com",
        "password": "Password123!",
        "full_name": "Charlie Chaplin"
    }
    await client.post("/api/v1/auth/register", json=reg_payload)

    # Wrong password
    login_res = await client.post("/api/v1/auth/login", json={
        "email": "charlie@example.com",
        "password": "WrongPassword!"
    })
    assert login_res.status_code == 401
    assert "Incorrect email or password" in login_res.json()["detail"]

    # Non-existent email
    res2 = await client.post("/api/v1/auth/login", json={
        "email": "nonexistent@example.com",
        "password": "Password123!"
    })
    assert res2.status_code == 401


@pytest.mark.asyncio
async def test_token_refresh_and_revocation(client: AsyncClient):
    auth_limiter.reset()
    reg = await client.post("/api/v1/auth/register", json={
        "email": "dan@example.com",
        "password": "Password123!",
        "full_name": "Dan Brown"
    })
    tokens = reg.json()
    refresh_token = tokens["refresh_token"]

    # Refresh token
    refresh_res = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": refresh_token
    })
    assert refresh_res.status_code == 200
    new_tokens = refresh_res.json()
    assert "access_token" in new_tokens
    new_refresh_token = new_tokens["refresh_token"]

    # Old refresh token should now be revoked (rotation)
    old_try = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": refresh_token
    })
    assert old_try.status_code == 401

    # Logout with current refresh token
    logout_res = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": new_refresh_token},
        headers={"Authorization": f"Bearer {new_tokens['access_token']}"}
    )
    assert logout_res.status_code == 200

    # Refresh after logout should fail
    after_logout = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": new_refresh_token
    })
    assert after_logout.status_code == 401


@pytest.mark.asyncio
async def test_rate_limiting(client: AsyncClient):
    auth_limiter.reset()
    auth_limiter.limit = 3  # Lower limit for testing

    payload = {"email": "test@example.com", "password": "wrong"}
    for _ in range(3):
        res = await client.post("/api/v1/auth/login", json=payload)
        assert res.status_code in [401, 200]

    # 4th request must be rate limited
    limited_res = await client.post("/api/v1/auth/login", json=payload)
    assert limited_res.status_code == 429
    assert "Rate limit exceeded" in limited_res.json()["detail"]
    assert "Retry-After" in limited_res.headers
    auth_limiter.reset()
