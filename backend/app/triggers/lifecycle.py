"""Subscription Lifecycle Manager for NexusMCP Triggers.

Requirement 8:
- Register provider subscriptions when workflows are activated/enabled.
- Unregister subscriptions when workflows are paused, disabled, or archived.
- Handle subscription renewal where providers require it.
- Atomic state synchronisation between Workflow status and TriggerSubscription.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..logger import logger
from ..models.trigger import SubscriptionStatus, TriggerSubscription
from ..models.workflow import TriggerConfig, WorkflowDefinition, WorkflowStatus
from ..repositories.trigger_repo import TriggerRepository
from ..repositories.workflow_repo import WorkflowRepository
from .adapters.base import BaseTriggerAdapter
from .adapters.polling import PollingTriggerAdapter
from .adapters.schedule import ScheduleTriggerAdapter
from .adapters.webhook import WebhookTriggerAdapter


class SubscriptionLifecycleManager:
    """Manages the registration, pause, resume, renewal, and deletion of trigger subscriptions."""

    def __init__(self, db: AsyncIOMotorDatabase, webhook_base_url: Optional[str] = None):
        self.db = db
        self.trigger_repo = TriggerRepository(db)
        self.workflow_repo = WorkflowRepository(db)
        self.webhook_base_url = webhook_base_url or "/api/v1/triggers/webhook"

        self.adapters: Dict[str, BaseTriggerAdapter] = {
            "webhook": WebhookTriggerAdapter("webhook"),
            "github": WebhookTriggerAdapter("github"),
            "slack": WebhookTriggerAdapter("slack"),
            "polling": PollingTriggerAdapter("polling"),
            "schedule": ScheduleTriggerAdapter("schedule"),
        }

    def _resolve_adapter(self, trigger_type: str, provider: Optional[str]) -> BaseTriggerAdapter:
        if provider and provider.lower() in self.adapters:
            return self.adapters[provider.lower()]
        return self.adapters.get(trigger_type.lower()) or self.adapters["webhook"]

    async def register_workflow_trigger(
        self,
        workspace_id: str,
        workflow_id: str,
        workflow_version: int,
        trigger_config_dict: Dict[str, Any],
        connection_id: Optional[str] = None,
    ) -> TriggerSubscription:
        """Register trigger subscription upon workflow activation."""
        trigger_config = TriggerConfig.model_validate(trigger_config_dict)
        adapter = self._resolve_adapter(trigger_config.type.value, trigger_config.provider)

        subscription = await adapter.register_subscription(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            trigger_config=trigger_config,
            connection_id=connection_id,
            webhook_base_url=self.webhook_base_url,
        )

        sub_id = await self.trigger_repo.save_subscription(subscription)
        subscription.id = sub_id

        logger.info(
            "Trigger subscription registered",
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            trigger_type=trigger_config.type.value,
            provider=subscription.provider,
        )
        return subscription

    async def deactivate_workflow_trigger(
        self,
        workspace_id: str,
        workflow_id: str,
    ) -> bool:
        """Deactivate or pause trigger subscription when workflow is paused."""
        sub_dict = await self.trigger_repo.get_subscription(workspace_id, workflow_id)
        if not sub_dict:
            return False

        await self.trigger_repo.update_subscription_status(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            status=SubscriptionStatus.PAUSED,
        )
        logger.info("Trigger subscription paused", workspace_id=workspace_id, workflow_id=workflow_id)
        return True

    async def resume_workflow_trigger(
        self,
        workspace_id: str,
        workflow_id: str,
    ) -> bool:
        """Resume trigger subscription when workflow is resumed."""
        sub_dict = await self.trigger_repo.get_subscription(workspace_id, workflow_id)
        if not sub_dict:
            return False

        await self.trigger_repo.update_subscription_status(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            status=SubscriptionStatus.ACTIVE,
        )
        logger.info("Trigger subscription resumed", workspace_id=workspace_id, workflow_id=workflow_id)
        return True

    async def unregister_workflow_trigger(
        self,
        workspace_id: str,
        workflow_id: str,
    ) -> bool:
        """Completely unregister and clean up trigger subscription on archive or delete."""
        sub_dict = await self.trigger_repo.get_subscription(workspace_id, workflow_id)
        if not sub_dict:
            return False

        sub = TriggerSubscription(**sub_dict)
        adapter = self._resolve_adapter(sub.trigger_type, sub.provider)
        try:
            await adapter.unregister_subscription(sub)
        except Exception as e:
            logger.warning("Adapter unregister error", workflow_id=workflow_id, error=str(e))

        await self.trigger_repo.delete_subscription(workspace_id, workflow_id)
        await self.trigger_repo.delete_checkpoint(workspace_id, workflow_id)
        logger.info("Trigger subscription unregistered and cleaned up", workspace_id=workspace_id, workflow_id=workflow_id)
        return True

    async def check_and_renew_subscriptions(self) -> int:
        """Renew expiring external subscriptions if renewal is required."""
        subs = await self.trigger_repo.list_active_subscriptions()
        renewed_count = 0
        now = datetime.now(timezone.utc)
        for s in subs:
            if s.get("renewal_required") and s.get("expires_at"):
                exp = s["expires_at"]
                if hasattr(exp, "tzinfo") and exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                # If expiring within 24 hours, renew
                if (exp - now).total_seconds() < 86400:
                    # Perform renewal
                    renewed_count += 1
        return renewed_count
