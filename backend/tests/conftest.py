import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db import get_db
from app.main import app
from app.security.rate_limiter import auth_limiter
from tests.mock_db import MockDatabase


@pytest_asyncio.fixture
async def mock_db():
    db = MockDatabase()
    auth_limiter.reset()
    return db


@pytest_asyncio.fixture
async def client(mock_db):
    app.dependency_overrides[get_db] = lambda: mock_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
