"""Real-time Event Broadcaster for NexusMCP Observability.

Provides in-process pub/sub for Server-Sent Events (SSE) and WebSockets.
Allows live streaming of workflow execution status, step logs, metrics, and alerts.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from ..logger import logger
from ..security.redaction import redact_sensitive_data


class ExecutionEventBroadcaster:
    """Manages active live-stream subscribers for SSE and WebSockets."""

    def __init__(self):
        # Maps subscriber queue to (workspace_id, execution_id_or_none)
        self._subscribers: Dict[asyncio.Queue, tuple[str, Optional[str]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, workspace_id: str, execution_id: Optional[str] = None) -> asyncio.Queue:
        """Register a new listener queue for workspace or specific execution."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers[queue] = (workspace_id, execution_id)
        logger.debug("Live stream subscriber connected", workspace_id=workspace_id, execution_id=execution_id)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Unregister a listener queue."""
        async with self._lock:
            if queue in self._subscribers:
                ws_id, ex_id = self._subscribers.pop(queue)
                logger.debug("Live stream subscriber disconnected", workspace_id=ws_id, execution_id=ex_id)

    async def broadcast(
        self,
        workspace_id: str,
        event_type: str,
        data: Dict[str, Any],
        execution_id: Optional[str] = None,
    ) -> None:
        """Publish an event to all matching active subscribers."""
        safe_data = redact_sensitive_data(data)
        message = {
            "event_type": event_type,
            "workspace_id": workspace_id,
            "execution_id": execution_id or safe_data.get("execution_id"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": safe_data,
        }

        async with self._lock:
            subscribers_snapshot = list(self._subscribers.items())

        for queue, (sub_ws, sub_ex) in subscribers_snapshot:
            # Tenant isolation: workspace must match
            if sub_ws != workspace_id:
                continue

            # Execution filter: if subscriber is listening for a specific execution, only send matching ones
            if sub_ex is not None and execution_id is not None and sub_ex != execution_id:
                continue

            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Drop oldest or skip if subscriber is slow
                try:
                    queue.get_nowait()
                    queue.put_nowait(message)
                except Exception:
                    pass

    async def event_generator(
        self,
        workspace_id: str,
        execution_id: Optional[str] = None,
        keepalive_seconds: float = 15.0,
    ) -> AsyncGenerator[str, None]:
        """Async generator yielding SSE formatted text frames."""
        queue = await self.subscribe(workspace_id, execution_id)
        try:
            # Initial connection handshake event
            handshake = {
                "event_type": "connected",
                "workspace_id": workspace_id,
                "execution_id": execution_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            yield f"event: connected\ndata: {json.dumps(handshake)}\n\n"

            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
                    event_type = msg.get("event_type", "message")
                    yield f"event: {event_type}\ndata: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive ping comment to prevent HTTP connection timeouts
                    yield ": keepalive\n\n"
        finally:
            await self.unsubscribe(queue)


# Global singleton instance
_broadcaster: Optional[ExecutionEventBroadcaster] = None


def get_broadcaster() -> ExecutionEventBroadcaster:
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = ExecutionEventBroadcaster()
    return _broadcaster
