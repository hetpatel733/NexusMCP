from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "environment" in data
        assert data["version"] == "0.1.0"

@pytest.mark.asyncio
async def test_readiness_endpoint_healthy():
    with patch("app.api.v1.health.check_db_health", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = {"status": "ok"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/ready")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ready"
            assert data["dependencies"]["mongodb"]["status"] == "ok"

@pytest.mark.asyncio
async def test_readiness_endpoint_unhealthy():
    with patch("app.api.v1.health.check_db_health", new_callable=AsyncMock) as mock_check:
        mock_check.return_value = {"status": "down", "error": "Connection timeout"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/ready")
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "unavailable"
            assert data["dependencies"]["mongodb"]["status"] == "down"
