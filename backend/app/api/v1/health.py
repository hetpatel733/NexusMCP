from typing import Any, Dict

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from ...config import get_settings
from ...db import check_db_health

router = APIRouter(tags=["Health"])

class HealthResponse(BaseModel):
    status: str
    version: str = "0.1.0"
    environment: str

class ReadinessResponse(BaseModel):
    status: str
    dependencies: Dict[str, Any]

@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Liveness probe: verifies the service is up and serving requests."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        environment=settings.ENV
    )

@router.get("/ready", response_model=ReadinessResponse)
async def readiness_check(response: Response):
    """Readiness probe: verifies external dependencies like MongoDB are available."""
    dependencies = {}
    is_ready = True

    # MongoDB Health
    mongo_status = await check_db_health()
    dependencies["mongodb"] = mongo_status
    if mongo_status.get("status") != "ok":
        is_ready = False

    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="unavailable", dependencies=dependencies)

    return ReadinessResponse(status="ready", dependencies=dependencies)
