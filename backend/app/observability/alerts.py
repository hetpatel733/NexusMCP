"""Alerting Engine and Conditions for NexusMCP.

Evaluates operational health rules:
1. Repeated Failures: Workflow instances failing repeatedly within a recent window.
2. Expired Connections: Integrations whose OAuth tokens or credentials have expired or revoked.
3. Webhook Renewal Failures: Webhook subscriptions failing automatic renewal or validation.
4. Unavailable Workers: Worker nodes missing heartbeats or unable to poll task queues.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, ConfigDict, Field

from ..logger import logger


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertCategory(str, Enum):
    REPEATED_FAILURES = "repeated_failures"
    EXPIRED_CONNECTIONS = "expired_connections"
    WEBHOOK_RENEWAL_FAILURES = "webhook_renewal_failures"
    UNAVAILABLE_WORKERS = "unavailable_workers"


class AlertItem(BaseModel):
    id: str
    workspace_id: str
    category: AlertCategory
    severity: AlertSeverity
    title: str
    description: str
    resource_type: str
    resource_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


class AlertEvaluator:
    """Evaluates system and workspace conditions against defined alerting rules."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.executions = db.workflow_executions
        self.connections = db.connections
        self.subscriptions = db.trigger_subscriptions
        self.workers = db.worker_heartbeats

    async def record_worker_heartbeat(
        self,
        worker_id: str,
        task_queue: str = "nexusmcp-task-queue",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record or refresh worker node heartbeat."""
        now = datetime.now(timezone.utc)
        await self.workers.update_one(
            {"worker_id": worker_id},
            {
                "$set": {
                    "worker_id": worker_id,
                    "task_queue": task_queue,
                    "last_heartbeat": now,
                    "metadata": metadata or {},
                }
            },
            upsert=True,
        )

    async def evaluate_all_alerts(self, workspace_id: str) -> List[AlertItem]:
        """Evaluate all four alert conditions for the given workspace."""
        alerts: List[AlertItem] = []

        # 1. Condition: Repeated Failures (>= 3 failures for the same workflow in past 2 hours)
        try:
            rf_alerts = await self._check_repeated_failures(workspace_id)
            alerts.extend(rf_alerts)
        except Exception as e:
            logger.error("Error evaluating repeated failure alerts", error=str(e))

        # 2. Condition: Expired Connections
        try:
            conn_alerts = await self._check_expired_connections(workspace_id)
            alerts.extend(conn_alerts)
        except Exception as e:
            logger.error("Error evaluating expired connection alerts", error=str(e))

        # 3. Condition: Webhook Renewal Failures
        try:
            sub_alerts = await self._check_webhook_renewal_failures(workspace_id)
            alerts.extend(sub_alerts)
        except Exception as e:
            logger.error("Error evaluating webhook renewal alerts", error=str(e))

        # 4. Condition: Unavailable Workers (No heartbeat in past 90 seconds)
        try:
            worker_alerts = await self._check_worker_availability(workspace_id)
            alerts.extend(worker_alerts)
        except Exception as e:
            logger.error("Error evaluating worker availability alerts", error=str(e))

        return alerts

    async def _check_repeated_failures(self, workspace_id: str) -> List[AlertItem]:
        alerts: List[AlertItem] = []
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=2)

        pipeline = [
            {
                "$match": {
                    "workspace_id": workspace_id,
                    "status": "failed",
                    "started_at": {"$gte": window_start},
                }
            },
            {
                "$group": {
                    "_id": "$workflow_id",
                    "failure_count": {"$sum": 1},
                    "last_error": {"$last": "$error"},
                }
            },
            {"$match": {"failure_count": {"$gte": 3}}},
        ]

        cursor = self.executions.aggregate(pipeline)
        failures = await cursor.to_list(length=20)

        for item in failures:
            wf_id = item["_id"]
            count = item["failure_count"]
            last_err = item.get("last_error") or "Unknown execution error"
            alerts.append(
                AlertItem(
                    id=f"alert-fail-{workspace_id}-{wf_id}",
                    workspace_id=workspace_id,
                    category=AlertCategory.REPEATED_FAILURES,
                    severity=AlertSeverity.CRITICAL,
                    title=f"Repeated Workflow Failures ({count} attempts)",
                    description=f"Workflow '{wf_id}' failed {count} times in the last 2 hours. Last error: {last_err}",
                    resource_type="workflow",
                    resource_id=wf_id,
                    metadata={"failure_count": count, "last_error": last_err},
                )
            )

        return alerts

    async def _check_expired_connections(self, workspace_id: str) -> List[AlertItem]:
        alerts: List[AlertItem] = []
        now = datetime.now(timezone.utc)

        cursor = self.connections.find({
            "workspace_id": workspace_id,
            "$or": [
                {"status": {"$in": ["expired", "revoked", "error"]}},
                {"expires_at": {"$lt": now}},
            ],
        })
        conns = await cursor.to_list(length=20)

        for conn in conns:
            conn_id = str(conn.get("_id", conn.get("id", "unknown")))
            provider = conn.get("provider", "unknown")
            app_name = conn.get("app_name") or provider
            status_val = conn.get("status", "expired")

            alerts.append(
                AlertItem(
                    id=f"alert-conn-{conn_id}",
                    workspace_id=workspace_id,
                    category=AlertCategory.EXPIRED_CONNECTIONS,
                    severity=AlertSeverity.WARNING,
                    title=f"Integration Connection {status_val.capitalize()}: {app_name}",
                    description=f"Connection '{app_name}' ({provider}) is currently {status_val}. Workflows requiring this connection may fail.",
                    resource_type="connection",
                    resource_id=conn_id,
                    metadata={"provider": provider, "status": status_val},
                )
            )

        return alerts

    async def _check_webhook_renewal_failures(self, workspace_id: str) -> List[AlertItem]:
        alerts: List[AlertItem] = []

        cursor = self.subscriptions.find({
            "workspace_id": workspace_id,
            "status": {"$in": ["renewal_failed", "failed", "expired"]},
        })
        subs = await cursor.to_list(length=20)

        for sub in subs:
            sub_id = str(sub.get("_id", sub.get("id", "unknown")))
            provider = sub.get("provider", "webhook")
            wf_id = sub.get("workflow_id", "unknown")
            sub_status = sub.get("status", "failed")
            err_msg = sub.get("error_message") or "Failed to register or renew webhook subscription with provider"

            alerts.append(
                AlertItem(
                    id=f"alert-sub-{sub_id}",
                    workspace_id=workspace_id,
                    category=AlertCategory.WEBHOOK_RENEWAL_FAILURES,
                    severity=AlertSeverity.CRITICAL,
                    title=f"Webhook Renewal Failed: {provider}",
                    description=f"Trigger subscription for workflow '{wf_id}' failed: {err_msg}",
                    resource_type="trigger_subscription",
                    resource_id=sub_id,
                    metadata={"provider": provider, "workflow_id": wf_id, "status": sub_status},
                )
            )

        return alerts

    async def _check_worker_availability(self, workspace_id: str) -> List[AlertItem]:
        alerts: List[AlertItem] = []
        now = datetime.now(timezone.utc)
        stale_threshold = now - timedelta(seconds=90)

        active_workers = await self.workers.count_documents({
            "last_heartbeat": {"$gte": stale_threshold}
        })

        if active_workers == 0:
            alerts.append(
                AlertItem(
                    id=f"alert-worker-unavailable-{workspace_id}",
                    workspace_id=workspace_id,
                    category=AlertCategory.UNAVAILABLE_WORKERS,
                    severity=AlertSeverity.CRITICAL,
                    title="No Active Execution Workers Detected",
                    description="No background Temporal worker heartbeats received in the last 90 seconds. Workflows may be delayed in queue.",
                    resource_type="worker_pool",
                    resource_id="nexusmcp-task-queue",
                    metadata={"active_worker_count": 0},
                )
            )

        return alerts
