"""Pydantic models for third-party connection management.

Connections represent a user's linked third-party application account
(via Composio managed OAuth). NexusMCP never stores plaintext provider
OAuth tokens — only provider-managed connection references.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ConnectionStatus(str, Enum):
    """Lifecycle status of a third-party connection."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    EXPIRED = "expired"
    ACTION_REQUIRED = "action_required"
    UNKNOWN = "unknown"


class ConnectionCreateRequest(BaseModel):
    app_name: str = Field(min_length=1, max_length=100, description="Composio app name")
    redirect_url: Optional[str] = Field(default=None, description="OAuth callback URL")


class ConnectionResponse(BaseModel):
    id: str
    workspace_id: str
    user_id: str
    app_name: str
    connection_id: str
    status: ConnectionStatus
    connected_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConnectionStatusResponse(BaseModel):
    connection_id: str
    app_name: str
    status: ConnectionStatus
    external_user_id: str
    connected_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
