"""Metrics and Observability Service for NexusMCP.

Tracks and aggregates:
- Trigger delays (time from trigger intake to execution start)
- Queue latency (time between dispatch and step execution)
- Workflow and step success/failure rates
- Retry counts and attempt distributions
- Activity duration analytics (per tool / step)
- Provider errors grouped by provider and error category
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..logger import logger


class MetricsService:
    """Computes real-time and aggregate metrics across executions, steps, and triggers."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.executions = db.workflow_executions
        self.step_executions = db.step_executions
        self.trigger_events = db.trigger_events

    async def get_execution_metrics(
        self,
        workspace_id: str,
        workflow_id: Optional[str] = None,
        timeframe_hours: int = 24,
    ) -> Dict[str, Any]:
        """Aggregate workflow execution metrics: success/failure rates, duration, and latency."""
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=timeframe_hours)

        match_stage: Dict[str, Any] = {
            "workspace_id": workspace_id,
            "started_at": {"$gte": since},
        }
        if workflow_id:
            match_stage["workflow_id"] = workflow_id

        # 1. Status and counts aggregation
        pipeline = [
            {"$match": match_stage},
            {
                "$group": {
                    "_id": "$status",
                    "count": {"$sum": 1},
                    "avg_duration_ms": {"$avg": "$duration_ms"},
                }
            }
        ]
        status_counts = await self.executions.aggregate(pipeline).to_list(length=100)

        total_executions = 0
        completed_count = 0
        failed_count = 0
        running_count = 0
        pending_count = 0
        cancelled_count = 0
        total_duration = 0.0
        dur_samples = 0

        for item in status_counts:
            st = item["_id"]
            cnt = item["count"]
            total_executions += cnt
            if st == "completed":
                completed_count = cnt
            elif st == "failed":
                failed_count = cnt
            elif st == "running":
                running_count = cnt
            elif st == "pending":
                pending_count = cnt
            elif st == "cancelled":
                cancelled_count = cnt

            if item.get("avg_duration_ms") is not None:
                total_duration += item["avg_duration_ms"] * cnt
                dur_samples += cnt

        success_rate = (completed_count / total_executions * 100) if total_executions > 0 else 0.0
        failure_rate = (failed_count / total_executions * 100) if total_executions > 0 else 0.0
        avg_execution_ms = (total_duration / dur_samples) if dur_samples > 0 else 0.0

        # 2. Trigger delay and queue latency computation
        latency_pipeline = [
            {"$match": match_stage},
            {
                "$project": {
                    "started_at": 1,
                    "created_at": 1,
                    "trigger_event": 1,
                }
            },
            {"$sort": {"started_at": -1}},
            {"$limit": 50},
        ]
        recent_execs = await self.executions.aggregate(latency_pipeline).to_list(length=50)

        trigger_delays: List[float] = []
        queue_latencies: List[float] = []

        for ex in recent_execs:
            st = ex.get("started_at")
            cr = ex.get("created_at")
            trig = ex.get("trigger_event") or {}

            # Queue latency: created_at -> started_at
            if isinstance(st, datetime) and isinstance(cr, datetime):
                ql = (st - cr).total_seconds() * 1000
                if ql >= 0:
                    queue_latencies.append(ql)

            # Trigger delay: trigger_event.received_at -> started_at
            recv_str = trig.get("received_at")
            if recv_str and isinstance(st, datetime):
                try:
                    if isinstance(recv_str, str):
                        recv_dt = datetime.fromisoformat(recv_str.replace("Z", "+00:00"))
                    elif isinstance(recv_str, datetime):
                        recv_dt = recv_str
                    else:
                        recv_dt = None
                    if recv_dt:
                        td = (st - recv_dt).total_seconds() * 1000
                        if td >= 0:
                            trigger_delays.append(td)
                except Exception:
                    pass

        avg_trigger_delay_ms = sum(trigger_delays) / len(trigger_delays) if trigger_delays else 0.0
        avg_queue_latency_ms = sum(queue_latencies) / len(queue_latencies) if queue_latencies else 0.0

        return {
            "workspace_id": workspace_id,
            "workflow_id": workflow_id,
            "timeframe_hours": timeframe_hours,
            "total_executions": total_executions,
            "completed": completed_count,
            "failed": failed_count,
            "running": running_count,
            "pending": pending_count,
            "cancelled": cancelled_count,
            "success_rate_percent": round(success_rate, 2),
            "failure_rate_percent": round(failure_rate, 2),
            "avg_duration_ms": round(avg_execution_ms, 2),
            "avg_trigger_delay_ms": round(avg_trigger_delay_ms, 2),
            "avg_queue_latency_ms": round(avg_queue_latency_ms, 2),
        }

    async def get_step_and_provider_metrics(
        self,
        workspace_id: str,
        workflow_id: Optional[str] = None,
        timeframe_hours: int = 24,
    ) -> Dict[str, Any]:
        """Aggregate step-level metrics: duration per step, retry distributions, and provider errors."""
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=timeframe_hours)

        match_stage: Dict[str, Any] = {
            "workspace_id": workspace_id,
            "started_at": {"$gte": since},
        }
        if workflow_id:
            match_stage["workflow_id"] = workflow_id

        # 1. Step activity durations and status
        step_pipeline = [
            {"$match": match_stage},
            {
                "$group": {
                    "_id": "$step_id",
                    "step_name": {"$first": "$step_name"},
                    "total_attempts": {"$sum": 1},
                    "retries": {
                        "$sum": {"$cond": [{"$gt": ["$attempt", 1]}, 1, 0]}
                    },
                    "avg_duration_ms": {"$avg": "$execution_time_ms"},
                    "max_duration_ms": {"$max": "$execution_time_ms"},
                    "min_duration_ms": {"$min": "$execution_time_ms"},
                    "failures": {
                        "$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}
                    },
                    "completed": {
                        "$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}
                    },
                }
            },
            {"$sort": {"total_attempts": -1}},
            {"$limit": 50},
        ]
        step_metrics = await self.step_executions.aggregate(step_pipeline).to_list(length=50)

        # 2. Provider errors
        error_pipeline = [
            {
                "$match": {
                    **match_stage,
                    "status": "failed",
                    "error": {"$ne": None},
                }
            },
            {
                "$project": {
                    "step_id": 1,
                    "step_name": 1,
                    "error": 1,
                    "attempt": 1,
                }
            },
            {"$limit": 100},
        ]
        failed_steps = await self.step_executions.aggregate(error_pipeline).to_list(length=100)

        provider_errors: Dict[str, int] = {}
        for fs in failed_steps:
            err = str(fs.get("error", ""))
            # Classify error
            err_category = "execution_error"
            if "rate_limit" in err.lower() or "429" in err:
                err_category = "rate_limit"
            elif "unauthorized" in err.lower() or "401" in err or "403" in err:
                err_category = "auth_error"
            elif "timeout" in err.lower():
                err_category = "timeout"
            elif "validation" in err.lower():
                err_category = "validation_error"
            elif "not found" in err.lower() or "404" in err:
                err_category = "not_found"

            provider_errors[err_category] = provider_errors.get(err_category, 0) + 1

        total_retries = sum(item.get("retries", 0) for item in step_metrics)

        return {
            "workspace_id": workspace_id,
            "workflow_id": workflow_id,
            "timeframe_hours": timeframe_hours,
            "total_retries": total_retries,
            "step_metrics": [
                {
                    "step_id": s["_id"],
                    "step_name": s.get("step_name") or s["_id"],
                    "total_attempts": s["total_attempts"],
                    "retries": s["retries"],
                    "completed": s["completed"],
                    "failures": s["failures"],
                    "avg_duration_ms": round(s.get("avg_duration_ms") or 0.0, 2),
                    "min_duration_ms": s.get("min_duration_ms") or 0,
                    "max_duration_ms": s.get("max_duration_ms") or 0,
                }
                for s in step_metrics
            ],
            "provider_error_breakdown": provider_errors,
        }
