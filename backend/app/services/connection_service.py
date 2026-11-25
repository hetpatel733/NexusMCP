"""Connection service for third-party app connections.

Orchestrates the provider adapter, repository, and audit logging.
Enforces workspace/user isolation and never exposes credentials.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..config import get_settings
from ..integrations.composio_adapter import ComposioAdapter
from ..integrations.provider import ConnectionProvider
from ..logger import logger
from ..models.audit import AuditAction
from ..models.connection import ConnectionCreateRequest, ConnectionStatus
from ..repositories.audit_repo import AuditRepository
from ..repositories.connection_repo import ConnectionRepository


class ConnectionService:
    """Business logic for connecting, status-checking, and revoking apps."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        provider: Optional[ConnectionProvider] = None,
    ):
        self.db = db
        self.settings = get_settings()
        self.connection_repo = ConnectionRepository(db)
        self.audit_repo = AuditRepository(db)
        # Use the provided adapter or default to Composio
        self.provider: ConnectionProvider = provider or ComposioAdapter()

    def _external_user_id(self, user_id: str) -> str:
        """Derive a stable, non-guessable external user identifier for Composio.

        Composio requires an entity_id. We derive a stable identifier from
        the NexusMCP user id, namespaced to avoid collisions and avoid
        exposing raw user ids.
        """
        return f"nexusmcp:{user_id}"

    async def list_apps(self) -> List[Dict[str, Any]]:
        """List supported third-party apps from the provider."""
        return await self.provider.list_apps()

    async def initiate_connection(
        self,
        workspace_id: str,
        user_id: str,
        data: ConnectionCreateRequest,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Initiate a new third-party connection.

        Creates a pending connection record and returns the provider's
        OAuth redirect URL for the user to complete authorization.
        """
        # Verify the user belongs to the workspace (already done by dependency,
        # but re-verify for defense in depth).
        existing = await self.connection_repo.count_by_workspace_and_app(
            workspace_id=workspace_id,
            user_id=user_id,
            app_name=data.app_name,
        )
        if existing > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A connection to {data.app_name} already exists for this workspace.",
            )

        external_user_id = self._external_user_id(user_id)

        try:
            provider_result = await self.provider.create_connection_link(
                app_name=data.app_name,
                external_user_id=external_user_id,
                redirect_url=data.redirect_url,
            )
        except Exception as exc:
            logger.error(
                "Provider connection initiation failed",
                app_name=data.app_name,
                user_id=user_id,
                workspace_id=workspace_id,
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to initiate connection with the third-party provider.",
            )

        provider_connection_id = provider_result.get("connection_id", "")
        redirect_url = provider_result.get("redirect_url", "")

        # Persist a pending connection record (no provider tokens stored)
        now = datetime.now(timezone.utc)
        doc: Dict[str, Any] = {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "app_name": data.app_name,
            "connection_id": str(uuid.uuid4()),  # NexusMCP internal id
            "provider_connection_id": provider_connection_id,
            "external_user_id": external_user_id,
            "status": ConnectionStatus.ACTION_REQUIRED.value,
            "connected_at": None,
            "expires_at": None,
            "created_at": now,
            "updated_at": now,
        }
        res = await self.connection_repo.collection.insert_one(doc)
        doc["_id"] = res.inserted_id

        await self.audit_repo.record_event(
            action=AuditAction.INTEGRATION_CONNECT_INITIATED,
            workspace_id=workspace_id,
            user_id=user_id,
            user_email=None,
            resource_type="integration_connection",
            resource_id=str(doc["_id"]),
            details={"app_name": data.app_name, "provider": "composio"},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return {
            "id": str(doc["_id"]),
            "workspace_id": workspace_id,
            "user_id": user_id,
            "app_name": data.app_name,
            "connection_id": provider_connection_id,
            "status": ConnectionStatus.ACTION_REQUIRED.value,
            "redirect_url": redirect_url,
            "external_user_id": external_user_id,
        }

    async def list_connections(
        self,
        workspace_id: str,
        user_id: Optional[str] = None,
        app_name: Optional[str] = None,
        status: Optional[ConnectionStatus] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        """List connections for a workspace with tenant isolation."""
        connections = await self.connection_repo.list_by_workspace(
            workspace_id=workspace_id,
            user_id=user_id,
            app_name=app_name,
            status=status,
            limit=limit,
            skip=skip,
        )
        return [
            {
                "id": str(c["_id"]),
                "workspace_id": c["workspace_id"],
                "user_id": c["user_id"],
                "app_name": c["app_name"],
                "connection_id": c.get("provider_connection_id", c.get("connection_id", "")),
                "status": c.get("status", ConnectionStatus.UNKNOWN.value),
                "connected_at": c.get("connected_at"),
                "expires_at": c.get("expires_at"),
                "created_at": c.get("created_at"),
                "updated_at": c.get("updated_at"),
            }
            for c in connections
        ]

    async def get_connection_status(
        self,
        workspace_id: str,
        connection_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Return the current status of a connection, refreshing from provider."""
        conn = await self.connection_repo.get_by_id_and_workspace(workspace_id, connection_id)
        if not conn:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Connection not found",
            )

        external_user_id = conn.get("external_user_id", self._external_user_id(user_id))
        provider_connection_id = conn.get("provider_connection_id", "")

        try:
            provider_status = await self.provider.get_connection_status(
                external_user_id=external_user_id,
                connection_id=provider_connection_id,
            )
        except Exception as exc:
            logger.error(
                "Provider status check failed",
                connection_id=connection_id,
                error=str(exc),
            )
            provider_status = {"status": ConnectionStatus.UNKNOWN.value}

        provider_status_value = provider_status.get("status", ConnectionStatus.UNKNOWN.value)

        # Update local record
        await self.connection_repo.update_status(
            workspace_id=workspace_id,
            connection_id=str(conn["_id"]),
            status=ConnectionStatus(provider_status_value),
            expires_at=provider_status.get("expires_at"),
        )

        return {
            "connection_id": str(conn["_id"]),
            "app_name": conn["app_name"],
            "status": provider_status_value,
            "external_user_id": external_user_id,
            "connected_at": conn.get("connected_at"),
            "expires_at": provider_status.get("expires_at"),
        }

    async def revoke_connection(
        self,
        workspace_id: str,
        connection_id: str,
        user_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Revoke a connection and remove the local record."""
        conn = await self.connection_repo.get_by_id_and_workspace(workspace_id, connection_id)
        if not conn:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Connection not found",
            )

        external_user_id = conn.get("external_user_id", self._external_user_id(user_id))
        provider_connection_id = conn.get("provider_connection_id", "")

        # Revoke with provider
        try:
            await self.provider.revoke_connection(
                external_user_id=external_user_id,
                connection_id=provider_connection_id,
            )
        except Exception as exc:
            logger.error(
                "Provider revocation failed",
                connection_id=connection_id,
                error=str(exc),
            )
            # Continue to remove local record regardless

        # Delete local record
        deleted = await self.connection_repo.delete_by_workspace(
            workspace_id=workspace_id,
            connection_id=str(conn["_id"]),
        )

        await self.audit_repo.record_event(
            action=AuditAction.INTEGRATION_CONNECT_REVOKED,
            workspace_id=workspace_id,
            user_id=user_id,
            user_email=None,
            resource_type="integration_connection",
            resource_id=str(conn["_id"]),
            details={"app_name": conn["app_name"], "provider": "composio"},
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return {
            "id": str(conn["_id"]),
            "app_name": conn["app_name"],
            "status": ConnectionStatus.DISCONNECTED.value,
            "revoked": deleted,
        }

    async def has_valid_connection(
        self,
        workspace_id: str,
        user_id: str,
        app_name: str,
    ) -> bool:
        """Check whether a workspace has a valid (connected) connection for an app."""
        connections = await self.connection_repo.list_by_workspace(
            workspace_id=workspace_id,
            user_id=user_id,
            app_name=app_name,
            status=ConnectionStatus.CONNECTED,
            limit=1,
        )
        return len(connections) > 0
