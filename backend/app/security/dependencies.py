from typing import Any, Callable, Dict, List, Optional, Tuple

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..db import get_db
from ..models.workspace import WorkspaceRole
from ..repositories.user_repo import UserRepository
from ..repositories.workspace_repo import WorkspaceRepository
from .jwt import decode_token

security_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    access_token_cookie: Optional[str] = Cookie(default=None, alias="nexus_access_token"),
    database=Depends(get_db),
) -> Dict[str, Any]:
    """Validate JWT access token and return active user object."""
    token: Optional[str] = None
    if auth_header and auth_header.credentials:
        token = auth_header.credentials
    elif access_token_cookie:
        token = access_token_cookie

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(token, expected_type="access")
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token claims",
        )

    user_repo = UserRepository(database)
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    user["id"] = str(user["_id"])
    return user


async def get_current_workspace(
    x_workspace_id: Optional[str] = Header(default=None, alias="X-Workspace-ID"),
    query_workspace_id: Optional[str] = Query(default=None, alias="workspace_id"),
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db),
) -> Tuple[Dict[str, Any], WorkspaceRole]:
    """Validate workspace context and ensure caller is a member."""
    workspace_id = x_workspace_id or query_workspace_id

    workspace_repo = WorkspaceRepository(database)

    if not workspace_id:
        # Check if user has an existing workspace to default to
        user_workspaces = await workspace_repo.list_user_workspaces(current_user["id"])
        if user_workspaces:
            workspace = user_workspaces[0]
            role = WorkspaceRole(workspace["role"])
            workspace["id"] = str(workspace["_id"])
            return workspace, role

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace context required. Provide X-Workspace-ID header.",
        )

    membership = await workspace_repo.get_membership(workspace_id, current_user["id"])
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: You are not a member of this workspace",
        )

    workspace_doc = await workspace_repo.get_by_id(workspace_id)
    if not workspace_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )

    workspace = dict(workspace_doc)
    workspace["id"] = str(workspace["_id"])
    role = WorkspaceRole(membership["role"])
    return workspace, role


def require_role(allowed_roles: List[WorkspaceRole]) -> Callable:
    """Dependency factory that enforces workspace role permissions (RBAC)."""
    async def role_checker(
        workspace_and_role: Tuple[Dict[str, Any], WorkspaceRole] = Depends(get_current_workspace)
    ) -> Tuple[Dict[str, Any], WorkspaceRole]:
        workspace, role = workspace_and_role
        if role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Action requires one of the following roles: {[r.value for r in allowed_roles]}",
            )
        return workspace, role

    return role_checker
