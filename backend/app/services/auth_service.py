import re
from typing import Any, Dict, Optional

import jwt
from fastapi import HTTPException, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..config import get_settings
from ..models.audit import AuditAction
from ..models.user import TokenResponse, UserCreate, UserLogin, UserProfileUpdate, UserResponse
from ..repositories.audit_repo import AuditRepository
from ..repositories.token_repo import TokenRepository
from ..repositories.user_repo import UserRepository
from ..repositories.workspace_repo import WorkspaceRepository
from ..security.jwt import create_access_token, create_refresh_token, decode_token, hash_token
from ..security.password import hash_password, verify_password


def slugify(text: str) -> str:
    """Convert text to URL-safe alphanumeric slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-") or "workspace"


class AuthService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.user_repo = UserRepository(db)
        self.token_repo = TokenRepository(db)
        self.workspace_repo = WorkspaceRepository(db)
        self.audit_repo = AuditRepository(db)
        self.settings = get_settings()

    async def register(
        self,
        data: UserCreate,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        response: Optional[Response] = None
    ) -> TokenResponse:
        # Check if user already exists
        existing = await self.user_repo.get_by_email(data.email)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email address already exists.",
            )

        # Hash password securely
        pwd_hash = hash_password(data.password)

        # Create user
        user = await self.user_repo.create_user(
            email=data.email,
            hashed_password=pwd_hash,
            full_name=data.full_name
        )
        user_id = str(user["_id"])

        # Auto-create default workspace for user
        first_name = data.full_name.strip().split()[0]
        ws_name = f"{first_name}'s Workspace"
        base_slug = slugify(first_name)
        slug = f"{base_slug}-{user_id[-6:]}"

        workspace = await self.workspace_repo.create_workspace(
            name=ws_name,
            slug=slug,
            description="Personal default workspace",
            created_by=user_id
        )
        workspace_id = str(workspace["_id"])

        # Record audit log
        await self.audit_repo.record_event(
            action=AuditAction.AUTH_REGISTER,
            workspace_id=workspace_id,
            user_id=user_id,
            user_email=data.email,
            resource_type="user",
            resource_id=user_id,
            details={"email": data.email, "workspace_id": workspace_id},
            ip_address=ip_address,
            user_agent=user_agent
        )

        # Generate tokens
        return await self._create_tokens_and_session(user, ip_address, user_agent, response)

    async def login(
        self,
        data: UserLogin,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        response: Optional[Response] = None
    ) -> TokenResponse:
        user = await self.user_repo.get_by_email(data.email)
        if not user or not verify_password(data.password, user.get("hashed_password", "")):
            # Record failed login attempt in audit logs
            await self.audit_repo.record_event(
                action=AuditAction.AUTH_LOGIN_FAILED,
                user_email=data.email,
                resource_type="user",
                details={"email": data.email, "reason": "invalid_credentials"},
                ip_address=ip_address,
                user_agent=user_agent
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not user.get("is_active", True):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account has been deactivated.",
            )

        user_id = str(user["_id"])

        # Record successful login
        await self.audit_repo.record_event(
            action=AuditAction.AUTH_LOGIN_SUCCESS,
            user_id=user_id,
            user_email=user["email"],
            resource_type="user",
            resource_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent
        )

        return await self._create_tokens_and_session(user, ip_address, user_agent, response)

    async def refresh_tokens(
        self,
        refresh_token_str: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        response: Optional[Response] = None
    ) -> TokenResponse:
        try:
            payload = decode_token(refresh_token_str, expected_type="refresh")
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token_hash_val = hash_token(refresh_token_str)
        token_doc = await self.token_repo.get_valid_refresh_token(token_hash_val)
        if not token_doc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token has been revoked or expired.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Invalidate the used refresh token (token rotation)
        await self.token_repo.revoke_token(token_hash_val)

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Malformed token claims.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user = await self.user_repo.get_by_id(str(user_id))
        if not user or not user.get("is_active", True):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account no longer active.",
            )

        # Issue new token pair
        token_response = await self._create_tokens_and_session(user, ip_address, user_agent, response)

        # Record audit log
        await self.audit_repo.record_event(
            action=AuditAction.AUTH_TOKEN_REFRESH,
            user_id=str(user["_id"]),
            user_email=user["email"],
            resource_type="token",
            ip_address=ip_address,
            user_agent=user_agent
        )

        return token_response

    async def logout(
        self,
        refresh_token_str: Optional[str],
        user_id: Optional[str],
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        response: Optional[Response] = None
    ) -> bool:
        if refresh_token_str:
            token_hash_val = hash_token(refresh_token_str)
            await self.token_repo.revoke_token(token_hash_val)

        if response:
            # Clear cookies if present
            response.delete_cookie("nexus_access_token")
            response.delete_cookie("nexus_refresh_token")

        if user_id:
            user = await self.user_repo.get_by_id(user_id)
            user_email = user["email"] if user else None
            await self.audit_repo.record_event(
                action=AuditAction.AUTH_LOGOUT,
                user_id=user_id,
                user_email=user_email,
                resource_type="user",
                resource_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent
            )
        return True

    async def update_profile(
        self,
        user_id: str,
        data: UserProfileUpdate,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> UserResponse:
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        hashed_pwd: Optional[str] = None
        if data.new_password:
            if not data.current_password or not verify_password(data.current_password, user.get("hashed_password", "")):
                raise HTTPException(status_code=400, detail="Current password incorrect")
            hashed_pwd = hash_password(data.new_password)

        await self.user_repo.update_profile(
            user_id=user_id,
            full_name=data.full_name,
            hashed_password=hashed_pwd
        )

        # Record audit log
        await self.audit_repo.record_event(
            action=AuditAction.AUTH_PROFILE_UPDATE,
            user_id=user_id,
            user_email=user["email"],
            resource_type="user",
            resource_id=user_id,
            details={"updated_fields": [k for k, v in data.model_dump().items() if v is not None and k != "current_password" and k != "new_password"]},
            ip_address=ip_address,
            user_agent=user_agent
        )

        updated_user = await self.user_repo.get_by_id(user_id)
        assert updated_user is not None
        return UserResponse(
            id=str(updated_user["_id"]),
            email=updated_user["email"],
            full_name=updated_user["full_name"],
            is_active=updated_user["is_active"],
            created_at=updated_user["created_at"]
        )

    async def _create_tokens_and_session(
        self,
        user: Dict[str, Any],
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        response: Optional[Response] = None
    ) -> TokenResponse:
        user_id = str(user["_id"])
        email = user["email"]

        # Create access token
        access_token, expires_in = create_access_token(user_id, email)

        # Create refresh token
        refresh_token, jti, expire_time = create_refresh_token(user_id, email)
        token_hash_val = hash_token(refresh_token)

        # Store refresh token hash in DB
        await self.token_repo.store_refresh_token(
            user_id=user_id,
            token_hash=token_hash_val,
            jti=jti,
            expires_at=expire_time
        )

        # Optional: set secure cookies if response object provided
        if response:
            response.set_cookie(
                key="nexus_access_token",
                value=access_token,
                httponly=True,
                secure=self.settings.SECURE_COOKIES,
                samesite="lax",
                max_age=expires_in,
            )
            response.set_cookie(
                key="nexus_refresh_token",
                value=refresh_token,
                httponly=True,
                secure=self.settings.SECURE_COOKIES,
                samesite="lax",
                max_age=self.settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
            )

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=expires_in,
            user=UserResponse(
                id=user_id,
                email=user["email"],
                full_name=user["full_name"],
                is_active=user["is_active"],
                created_at=user["created_at"]
            )
        )
