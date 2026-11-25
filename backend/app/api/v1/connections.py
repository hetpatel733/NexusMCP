"""Connection management API routes.

Endpoints for initiating, listing, checking status, and revoking
third-party app connections via Composio. All routes enforce
authentication and workspace isolation.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request, status

from ...db import get_db
from ...models.connection import ConnectionCreateRequest, ConnectionStatus
from ...security.dependencies import get_current_user, get_current_workspace
from ...services.connection_service import ConnectionService

router = APIRouter(prefix="/connections", tags=["Integrations"])


def get_client_info(request: Request) -> tuple[Optional[str], Optional[str]]:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


def get_connection_service(database=Depends(get_db)) -> ConnectionService:
    return ConnectionService(database)


@router.get("", response_model=List[Dict[str, Any]])
async def list_connections(
    request: Request,
    app_name: Optional[str] = Query(default=None, description="Filter by app name"),
    status: Optional[ConnectionStatus] = Query(default=None, description="Filter by status"),
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: ConnectionService = Depends(get_connection_service),
):
    """List third-party connections for the active workspace."""
    workspace, _ = workspace_and_role
    ip, ua = get_client_info(request)
    connections = await service.list_connections(
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        app_name=app_name,
        status=status,
        limit=limit,
        skip=skip,
    )
    return connections


@router.post("", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def initiate_connection(
    data: ConnectionCreateRequest,
    request: Request,
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: ConnectionService = Depends(get_connection_service),
):
    """Initiate a third-party app connection via Composio.

    Returns the provider redirect URL the frontend should send the
    user to for OAuth authorization.
    """
    workspace, _ = workspace_and_role
    ip, ua = get_client_info(request)
    return await service.initiate_connection(
        workspace_id=workspace["id"],
        user_id=current_user["id"],
        data=data,
        ip_address=ip,
        user_agent=ua,
    )


@router.get("/{connection_id}/status", response_model=Dict[str, Any])
async def get_connection_status(
    connection_id: str,
    request: Request,
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: ConnectionService = Depends(get_connection_service),
):
    """Check the current status of a connection, refreshing from provider."""
    workspace, _ = workspace_and_role
    ip, ua = get_client_info(request)
    return await service.get_connection_status(
        workspace_id=workspace["id"],
        connection_id=connection_id,
        user_id=current_user["id"],
    )


@router.delete("/{connection_id}", response_model=Dict[str, Any])
async def revoke_connection(
    connection_id: str,
    request: Request,
    workspace_and_role=Depends(get_current_workspace),
    current_user: Dict[str, Any] = Depends(get_current_user),
    service: ConnectionService = Depends(get_connection_service),
):
    """Revoke/disconnect a third-party app connection."""
    workspace, _ = workspace_and_role
    ip, ua = get_client_info(request)
    return await service.revoke_connection(
        workspace_id=workspace["id"],
        connection_id=connection_id,
        user_id=current_user["id"],
        ip_address=ip,
        user_agent=ua,
    )
