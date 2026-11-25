from typing import Any, Dict, Optional

from fastapi import APIRouter, Cookie, Depends, Request, Response, status

from ...db import get_db
from ...models.user import (
    RefreshTokenRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserProfileUpdate,
    UserResponse,
)
from ...security.dependencies import get_current_user
from ...security.rate_limiter import rate_limit_auth
from ...services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


def get_client_info(request: Request) -> tuple[Optional[str], Optional[str]]:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit_auth)])
async def register(
    data: UserCreate,
    request: Request,
    response: Response,
    database=Depends(get_db)
):
    """Register a new user account, create a personal workspace, and return access tokens."""
    ip, ua = get_client_info(request)
    auth_service = AuthService(database)
    return await auth_service.register(data, ip_address=ip, user_agent=ua, response=response)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit_auth)])
async def login(
    data: UserLogin,
    request: Request,
    response: Response,
    database=Depends(get_db)
):
    """Authenticate with email and password and return access tokens."""
    ip, ua = get_client_info(request)
    auth_service = AuthService(database)
    return await auth_service.login(data, ip_address=ip, user_agent=ua, response=response)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    request: Request,
    response: Response,
    body: Optional[RefreshTokenRequest] = None,
    nexus_refresh_token: Optional[str] = Cookie(default=None),
    database=Depends(get_db)
):
    """Refresh an access token using a valid, unrevoked refresh token."""
    ip, ua = get_client_info(request)
    token = (body.refresh_token if body and body.refresh_token else None) or nexus_refresh_token
    if not token:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Refresh token required in body or cookie.",
        )

    auth_service = AuthService(database)
    return await auth_service.refresh_tokens(token, ip_address=ip, user_agent=ua, response=response)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    body: Optional[RefreshTokenRequest] = None,
    nexus_refresh_token: Optional[str] = Cookie(default=None),
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Revoke refresh token and invalidate the active session."""
    ip, ua = get_client_info(request)
    token = (body.refresh_token if body and body.refresh_token else None) or nexus_refresh_token
    user_id = current_user["id"] if current_user else None

    auth_service = AuthService(database)
    await auth_service.logout(token, user_id=user_id, ip_address=ip, user_agent=ua, response=response)
    return {"message": "Successfully logged out"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Retrieve the profile of the currently authenticated user."""
    return UserResponse(
        id=current_user["id"],
        email=current_user["email"],
        full_name=current_user["full_name"],
        is_active=current_user["is_active"],
        created_at=current_user["created_at"]
    )


@router.patch("/profile", response_model=UserResponse)
async def update_profile(
    data: UserProfileUpdate,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
    database=Depends(get_db)
):
    """Update profile information or password for the current user."""
    ip, ua = get_client_info(request)
    auth_service = AuthService(database)
    return await auth_service.update_profile(
        user_id=current_user["id"],
        data=data,
        ip_address=ip,
        user_agent=ua
    )
