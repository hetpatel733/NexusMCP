"""Composio integration adapter.

Implements the :class:`ConnectionProvider` protocol using the
`composio-core` SDK. All Composio-specific SDK calls are isolated
inside this adapter; the rest of the application interacts through the
provider interface.
"""

from typing import Any, Dict, List, Optional

from composio import Composio
from pydantic import SecretStr

from ..config import get_settings
from ..logger import logger
from .provider import ConnectionProvider


class ComposioAdapter(ConnectionProvider):
    """Composio-backed connection provider.

    Responsibilities:
      - Initiate OAuth connections via Composio's managed OAuth flow.
      - Query connection status.
      - List available apps and tools.
      - Revoke connections.

    No plaintext provider OAuth tokens are stored by this adapter or
    by NexusMCP. Composio manages the OAuth token lifecycle.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        api_key = self.settings.COMPOSIO_API_KEY
        self._api_key = SecretStr(api_key).get_secret_value() if api_key else None
        self._client = None
        self.entity_id = self.settings.COMPOSIO_ENTITY_ID or "nexusmcp-default"
        self.entity_type = self.settings.COMPOSIO_ENTITY_TYPE
        self.base_url = self.settings.COMPOSIO_BASE_URL
        self.callback_url = self.settings.COMPOSIO_CALLBACK_URL

    def _ensure_client(self) -> Composio:
        """Lazily create the Composio client and validate the API key."""
        if self._client is None:
            if not self._api_key:
                raise ValueError(
                    "COMPOSIO_API_KEY is required to use the Composio adapter. "
                    "Set it in your environment or .env file."
                )
            self._client = Composio(api_key=self._api_key)
        return self._client

    def _enabled_apps(self) -> List[str]:
        """Return the list of apps permitted for connection."""
        raw = self.settings.COMPOSIO_ENABLED_APPS.strip()
        if not raw:
            return []
        return [app.strip() for app in raw.split(",") if app.strip()]

    async def list_apps(self) -> List[Dict[str, Any]]:
        """Return supported apps, filtered by enabled apps config."""
        try:
            apps = self._ensure_client().apps.list()
            enabled = set(self._enabled_apps())
            result: List[Dict[str, Any]] = []
            for app in apps:
                name = getattr(app, "name", None) or app.get("name", "")
                if enabled and name not in enabled:
                    continue
                result.append(
                    {
                        "name": name,
                        "slug": getattr(app, "slug", None) or app.get("slug", name.lower()),
                        "description": getattr(app, "description", None) or app.get("description", ""),
                        "auth_mode": getattr(app, "auth_mode", None) or app.get("auth_mode", "OAUTH2"),
                        "is_connected": False,
                    }
                )
            return result
        except Exception as exc:
            logger.error("Failed to list Composio apps", error=str(exc))
            return []

    async def create_connection_link(
        self,
        app_name: str,
        external_user_id: str,
        redirect_url: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Initiate a Composio OAuth connection for the given app/user.

        Uses Composio's connected account initiation flow. Returns the
        redirect URL the frontend should send the user to.
        """
        try:
            callback = redirect_url or self.callback_url
            init_result = self._ensure_client().connected_accounts.initiate(
                app_name=app_name,
                entity_id=external_user_id,
                entity_type=self.entity_type,
                callback_url=callback,
                scopes=scopes,
            )

            redirect_url_value = (
                getattr(init_result, "redirect_url", None)
                or init_result.get("redirect_url")
                if isinstance(init_result, dict)
                else None
            )
            connection_id = (
                getattr(init_result, "connection_id", None)
                or getattr(init_result, "id", None)
                or init_result.get("connection_id")
                if isinstance(init_result, dict)
                else None
            )

            logger.info(
                "Composio connection initiated",
                app_name=app_name,
                external_user_id=external_user_id,
                connection_id=connection_id,
            )

            return {
                "connection_id": connection_id or "",
                "redirect_url": redirect_url_value,
                "app_name": app_name,
                "external_user_id": external_user_id,
            }
        except Exception as exc:
            logger.error(
                "Failed to initiate Composio connection",
                app_name=app_name,
                external_user_id=external_user_id,
                error=str(exc),
            )
            raise

    async def get_connection_status(
        self,
        external_user_id: str,
        connection_id: str,
    ) -> Dict[str, Any]:
        """Return the current status of a Composio connection."""
        try:
            accounts = self._ensure_client().connected_accounts.get(
                entity_id=external_user_id,
                connected_account_id=connection_id,
            )

            status_value = (
                getattr(accounts, "status", None)
                or accounts.get("status")
                if isinstance(accounts, dict)
                else "unknown"
            )
            app_name = (
                getattr(accounts, "app_name", None)
                or accounts.get("app_name")
                if isinstance(accounts, dict)
                else ""
            )

            return {
                "connection_id": connection_id,
                "status": status_value,
                "app_name": app_name,
                "external_user_id": external_user_id,
            }
        except Exception as exc:
            logger.error(
                "Failed to get Composio connection status",
                connection_id=connection_id,
                external_user_id=external_user_id,
                error=str(exc),
            )
            return {
                "connection_id": connection_id,
                "status": "unknown",
                "app_name": "",
                "external_user_id": external_user_id,
            }

    async def list_tools(
        self,
        app_name: str,
        external_user_id: str,
    ) -> List[Dict[str, Any]]:
        """Return available tools/actions for a connected app."""
        try:
            tools = self._ensure_client().tools.list(
                app_names=[app_name],
                entity_id=external_user_id,
                entity_type=self.entity_type,
            )
            result: List[Dict[str, Any]] = []
            for tool in tools:
                result.append(
                    {
                        "name": getattr(tool, "name", None) or tool.get("name", ""),
                        "description": getattr(tool, "description", None) or tool.get("description", ""),
                        "app_name": app_name,
                    }
                )
            return result
        except Exception as exc:
            logger.error(
                "Failed to list Composio tools",
                app_name=app_name,
                external_user_id=external_user_id,
                error=str(exc),
            )
            return []

    async def revoke_connection(
        self,
        external_user_id: str,
        connection_id: str,
    ) -> Dict[str, Any]:
        """Revoke/disconnect a Composio connection."""
        try:
            self._ensure_client().connected_accounts.delete(
                entity_id=external_user_id,
                connected_account_id=connection_id,
            )
            logger.info(
                "Composio connection revoked",
                connection_id=connection_id,
                external_user_id=external_user_id,
            )
            return {"connection_id": connection_id, "status": "revoked"}
        except Exception as exc:
            logger.error(
                "Failed to revoke Composio connection",
                connection_id=connection_id,
                external_user_id=external_user_id,
                error=str(exc),
            )
            raise
