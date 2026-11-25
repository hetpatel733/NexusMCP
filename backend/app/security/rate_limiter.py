import asyncio
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, Request, status

from ..config import get_settings


class SlidingWindowRateLimiter:
    """In-memory sliding window rate limiter for security endpoints."""

    def __init__(self, limit: int = 10, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._records: Dict[str, List[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> None:
        async with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            timestamps = [t for t in self._records[key] if t > cutoff]
            self._records[key] = timestamps

            if len(timestamps) >= self.limit:
                oldest = timestamps[0]
                retry_after = max(1, int(oldest + self.window_seconds - now))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                    headers={"Retry-After": str(retry_after)}
                )

            self._records[key].append(now)

    def reset(self) -> None:
        """Reset all rate limiter records (useful for test isolation)."""
        self._records.clear()


auth_limiter = SlidingWindowRateLimiter(
    limit=get_settings().AUTH_RATE_LIMIT_PER_MINUTE,
    window_seconds=60
)


async def rate_limit_auth(request: Request) -> None:
    """Dependency that applies rate limiting by client IP."""
    client_ip = request.client.host if request.client else "unknown"
    await auth_limiter.check(client_ip)


# ---------------------------------------------------------------------------
# Per-user / per-workspace rate limiter for general API protection
# ---------------------------------------------------------------------------

class APIRateLimiter:
    """Per-workspace sliding window rate limiter for general API endpoints."""

    def __init__(self, limit: int = 60, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._records: Dict[str, List[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def check(self, workspace_id: str) -> None:
        async with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            timestamps = [t for t in self._records[workspace_id] if t > cutoff]
            self._records[workspace_id] = timestamps

            if len(timestamps) >= self.limit:
                oldest = timestamps[0]
                retry_after = max(1, int(oldest + self.window_seconds - now))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"API rate limit exceeded for workspace. Try again in {retry_after} seconds.",
                    headers={"Retry-After": str(retry_after)}
                )

            self._records[workspace_id].append(now)

    def reset(self) -> None:
        self._records.clear()


api_limiter = APIRateLimiter(
    limit=get_settings().API_RATE_LIMIT_PER_MINUTE,
    window_seconds=60
)


async def rate_limit_api(
    workspace_and_role: Tuple[dict, str] = None,
) -> None:
    """Dependency that applies rate limiting per workspace."""
    if workspace_and_role is None:
        return
    workspace, _ = workspace_and_role
    workspace_id = workspace.get("id", "unknown")
    await api_limiter.check(workspace_id)


# ---------------------------------------------------------------------------
# Execution quota enforcement (per user per hour)
# ---------------------------------------------------------------------------

class ExecutionQuotaTracker:
    """Tracks workflow execution counts per user/workspace within a time window."""

    def __init__(self, max_executions_per_hour: int = 1000):
        self.max_executions_per_hour = max_executions_per_hour
        self.window_seconds = 3600  # 1 hour
        self._records: Dict[str, List[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def check_and_record(self, workspace_id: str, user_id: str) -> None:
        key = f"{workspace_id}:{user_id}"
        async with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            timestamps = [t for t in self._records[key] if t > cutoff]
            self._records[key] = timestamps

            if len(timestamps) >= self.max_executions_per_hour:
                oldest = timestamps[0]
                retry_after = max(1, int(oldest + self.window_seconds - now))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Execution quota exceeded ({self.max_executions_per_hour}/hour). "
                        f"Try again in {retry_after} seconds."
                    ),
                    headers={"Retry-After": str(retry_after)}
                )

            self._records[key].append(now)

    def reset(self) -> None:
        self._records.clear()


execution_quota = ExecutionQuotaTracker(
    max_executions_per_hour=get_settings().EXECUTION_QUOTA_PER_USER_PER_HOUR
)


async def check_execution_quota(
    workspace_id: str,
    user_id: Optional[str] = None,
) -> None:
    """Enforce per-user/execution quota. Raises 429 if quota exceeded."""
    uid = user_id or "system"
    await execution_quota.check_and_record(workspace_id, uid)
