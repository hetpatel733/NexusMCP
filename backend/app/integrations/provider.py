"""Provider abstraction interface for third-party app connections.

All provider-specific logic is isolated behind this interface. The
Composio adapter implements this protocol.
"""

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class ConnectionProvider(Protocol):
    """Interface for third-party connection providers."""

    async def list_apps(self) -> List[Dict[str, Any]]:
        """Return the list of supported apps/actions available via this provider."""
        ...

    async def create_connection_link(
        self,
        app_name: str,
        external_user_id: str,
        redirect_url: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Initiate an OAuth connection for the given app and user.

        Returns a dict containing at least a `redirect_url` (or auth URL)
        and any provider-specific connection reference.
        """
        ...

    async def get_connection_status(
        self,
        external_user_id: str,
        connection_id: str,
    ) -> Dict[str, Any]:
        """Return the current status of a connection.

        Expected keys: ``status`` (connected/disconnected/expired/action_required),
        ``app_name``, ``connected_at``, ``expires_at``.
        """
        ...

    async def list_tools(
        self,
        app_name: str,
        external_user_id: str,
    ) -> List[Dict[str, Any]]:
        """Return available tools/actions for a connected app."""
        ...

    async def revoke_connection(
        self,
        external_user_id: str,
        connection_id: str,
    ) -> Dict[str, Any]:
        """Revoke/disconnect a connection for the given user."""
        ...
