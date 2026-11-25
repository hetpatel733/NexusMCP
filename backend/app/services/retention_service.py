"""Data Retention Service for NexusMCP.

Implements configurable retention and deletion policies for:
- Workflow execution logs and payloads
- Step execution records
- Deduplication records (TTL via MongoDB index)
- Dead letter queue events
- Audit logs

Retention periods are configured via Settings (RETENTION_*_DAYS).
Retention jobs can be invoked on-demand or scheduled periodically.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..config import get_settings
from ..logger import logger


class DataRetentionService:
    """Enforces data retention policies across MongoDB collections."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.settings = get_settings()

    # ------------------------------------------------------------------
    # Retention configuration
    # ------------------------------------------------------------------
    def _retention_delta(self, days: int) -> timedelta:
        return timedelta(days=days)

    def _cutoff(self, days: int) -> datetime:
        return datetime.now(timezone.utc) - self._retention_delta(days)

    # ------------------------------------------------------------------
    # Execution logs
    # ------------------------------------------------------------------
    async def purge_old_executions(self) -> int:
        """Delete execution records older than RETENTION_EXECUTION_LOGS_DAYS."""
        days = self.settings.RETENTION_EXECUTION_LOGS_DAYS
        cutoff = self._cutoff(days)
        result = await self.db.workflow_executions.delete_many(
            {"created_at": {"$lt": cutoff}}
        )
        logger.info(
            "Purged old executions",
            count=result.deleted_count,
            retention_days=days,
            cutoff=cutoff.isoformat(),
        )
        return result.deleted_count

    async def purge_old_step_executions(self) -> int:
        """Delete step execution records older than RETENTION_STEP_LOGS_DAYS."""
        days = self.settings.RETENTION_STEP_LOGS_DAYS
        cutoff = self._cutoff(days)
        result = await self.db.step_executions.delete_many(
            {"started_at": {"$lt": cutoff}}
        )
        logger.info(
            "Purged old step executions",
            count=result.deleted_count,
            retention_days=days,
            cutoff=cutoff.isoformat(),
        )
        return result.deleted_count

    async def purge_old_payloads(self) -> int:
        """Delete externalized payloads older than RETENTION_PAYLOADS_DAYS."""
        days = self.settings.RETENTION_PAYLOADS_DAYS
        cutoff = self._cutoff(days)
        result = await self.db.execution_payloads.delete_many(
            {"created_at": {"$lt": cutoff}}
        )
        logger.info(
            "Purged old payloads",
            count=result.deleted_count,
            retention_days=days,
            cutoff=cutoff.isoformat(),
        )
        return result.deleted_count

    # ------------------------------------------------------------------
    # Deduplication records
    # ------------------------------------------------------------------
    async def purge_expired_deduplication(self) -> int:
        """Delete deduplication records past their TTL (handled by MongoDB TTL index,
        but we also run an explicit cleanup for safety)."""
        days = self.settings.RETENTION_DEDUPLICATION_DAYS
        cutoff = self._cutoff(days)
        result = await self.db.deduplication_records.delete_many(
            {"expires_at": {"$lt": cutoff}}
        )
        logger.info(
            "Purged expired deduplication records",
            count=result.deleted_count,
            retention_days=days,
            cutoff=cutoff.isoformat(),
        )
        return result.deleted_count

    # ------------------------------------------------------------------
    # Dead letter queue
    # ------------------------------------------------------------------
    async def purge_old_dlq_events(self) -> int:
        """Delete DLQ events older than RETENTION_DLQ_DAYS."""
        days = self.settings.RETENTION_DLQ_DAYS
        cutoff = self._cutoff(days)
        result = await self.db.dead_letter_queue.delete_many(
            {"created_at": {"$lt": cutoff}}
        )
        logger.info(
            "Purged old DLQ events",
            count=result.deleted_count,
            retention_days=days,
            cutoff=cutoff.isoformat(),
        )
        return result.deleted_count

    async def purge_old_dlq_replays(self) -> int:
        """Delete DLQ replay records older than RETENTION_DLQ_DAYS."""
        days = self.settings.RETENTION_DLQ_DAYS
        cutoff = self._cutoff(days)
        result = await self.db.dlq_replays.delete_many(
            {"created_at": {"$lt": cutoff}}
        )
        logger.info(
            "Purged old DLQ replays",
            count=result.deleted_count,
            retention_days=days,
            cutoff=cutoff.isoformat(),
        )
        return result.deleted_count

    # ------------------------------------------------------------------
    # Audit logs
    # ------------------------------------------------------------------
    async def purge_old_audit_logs(self) -> int:
        """Delete audit logs older than RETENTION_AUDIT_LOGS_DAYS."""
        days = self.settings.RETENTION_AUDIT_LOGS_DAYS
        cutoff = self._cutoff(days)
        result = await self.db.audit_logs.delete_many(
            {"created_at": {"$lt": cutoff}}
        )
        logger.info(
            "Purged old audit logs",
            count=result.deleted_count,
            retention_days=days,
            cutoff=cutoff.isoformat(),
        )
        return result.deleted_count

    # ------------------------------------------------------------------
    # Full retention sweep
    # ------------------------------------------------------------------
    async def run_full_retention_sweep(self) -> Dict[str, int]:
        """Run all retention policies and return deletion counts per collection."""
        results: Dict[str, int] = {}

        results["workflow_executions"] = await self.purge_old_executions()
        results["step_executions"] = await self.purge_old_step_executions()
        results["execution_payloads"] = await self.purge_old_payloads()
        results["deduplication_records"] = await self.purge_expired_deduplication()
        results["dead_letter_queue"] = await self.purge_old_dlq_events()
        results["dlq_replays"] = await self.purge_old_dlq_replays()
        results["audit_logs"] = await self.purge_old_audit_logs()

        total = sum(results.values())
        logger.info(
            "Full retention sweep complete",
            total_deleted=total,
            details=results,
        )
        return results

    # ------------------------------------------------------------------
    # Retention status report
    # ------------------------------------------------------------------
    async def get_retention_report(self) -> Dict[str, Any]:
        """Return current counts and oldest record dates per collection for monitoring."""
        report: Dict[str, Any] = {
            "retention_policies": {
                "deduplication_days": self.settings.RETENTION_DEDUPLICATION_DAYS,
                "dlq_days": self.settings.RETENTION_DLQ_DAYS,
                "audit_logs_days": self.settings.RETENTION_AUDIT_LOGS_DAYS,
                "execution_logs_days": self.settings.RETENTION_EXECUTION_LOGS_DAYS,
                "step_logs_days": self.settings.RETENTION_STEP_LOGS_DAYS,
                "payloads_days": self.settings.RETENTION_PAYLOADS_DAYS,
            },
            "collections": {},
        }

        collections = [
            "workflow_executions",
            "step_executions",
            "execution_payloads",
            "deduplication_records",
            "dead_letter_queue",
            "dlq_replays",
            "audit_logs",
        ]

        for coll_name in collections:
            coll = self.db[coll_name]
            count = await coll.estimated_document_count()
            # Find oldest record
            oldest = await coll.find_one(
                {}, sort=[("created_at", 1)]
            )
            oldest_date = (
                oldest.get("created_at").isoformat()
                if oldest and oldest.get("created_at")
                else None
            )
            report["collections"][coll_name] = {
                "count": count,
                "oldest_record": oldest_date,
            }

        return report
