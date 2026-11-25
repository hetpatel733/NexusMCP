import asyncio
import time
from collections import defaultdict
from typing import Dict, List

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
