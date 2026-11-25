from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class AuditAction:
    AUTH_REGISTER = "auth.register"
    AUTH_LOGIN_SUCCESS = "auth.login.success"
    AUTH_LOGIN_FAILED = "auth.login.failed"
    AUTH_LOGOUT = "auth.logout"
    AUTH_TOKEN_REFRESH = "auth.token.refresh"
    AUTH_PROFILE_UPDATE = "auth.profile.update"

    WORKSPACE_CREATE = "workspace.create"
    WORKSPACE_UPDATE = "workspace.update"
    WORKSPACE_DELETE = "workspace.delete"
    WORKSPACE_MEMBER_ADD = "workspace.member.add"
    WORKSPACE_MEMBER_REMOVE = "workspace.member.remove"
    WORKSPACE_MEMBER_ROLE_UPDATE = "workspace.member.role_update"

    WORKFLOW_CREATE = "workflow.create"
    WORKFLOW_UPDATE = "workflow.update"
    WORKFLOW_DELETE = "workflow.delete"
    WORKFLOW_ENABLE = "workflow.enable"
    WORKFLOW_DISABLE = "workflow.disable"
    WORKFLOW_GENERATE = "workflow.generate"
    WORKFLOW_PROMPT_EDIT = "workflow.prompt.edit"
    WORKFLOW_VERSION_CREATE = "workflow.version.create"
    WORKFLOW_APPROVE = "workflow.approve"
    WORKFLOW_PAUSE = "workflow.pause"
    WORKFLOW_RESUME = "workflow.resume"
    WORKFLOW_ARCHIVE = "workflow.archive"

    INTEGRATION_CONNECT_INITIATED = "integration.connect.initiated"
    INTEGRATION_CONNECT_REVOKED = "integration.connect.revoked"


class AuditLogResponse(BaseModel):
    id: str
    workspace_id: Optional[str] = None
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    details: Dict[str, Any] = {}
    ip_address: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
