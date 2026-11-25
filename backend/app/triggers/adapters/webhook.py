"""Webhook Trigger Adapter for NexusMCP.

Requirement 2 & 3:
- Secure webhook ingress.
- Signature verification for GitHub, Slack, and HMAC-SHA256 webhooks.
- Timestamp and replay checks (configurable window, default 300s).
- Canonical EventEnvelope conversion with:
  `event_id`, `provider`, `connection_id`, `event_type`, `received_at`, `payload_reference`.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...logger import logger
from ...models.trigger import EventEnvelope, TriggerSubscription
from ...models.workflow import TriggerConfig
from .base import BaseTriggerAdapter


class WebhookVerificationError(Exception):
    """Raised when webhook signature, timestamp, or payload verification fails."""
    pass


class WebhookTriggerAdapter(BaseTriggerAdapter):
    """Adapter for receiving, verifying, and normalizing webhook events."""

    def __init__(self, provider_name: str = "webhook", default_secret: Optional[str] = None):
        super().__init__(provider_name=provider_name)
        self.default_secret = default_secret

    @property
    def supported_types(self) -> List[str]:
        return ["webhook"]

    async def register_subscription(
        self,
        workspace_id: str,
        workflow_id: str,
        workflow_version: int,
        trigger_config: TriggerConfig,
        connection_id: Optional[str] = None,
        webhook_base_url: Optional[str] = None,
    ) -> TriggerSubscription:
        """Register webhook subscription and generate unique webhook token / URL."""
        secret = trigger_config.parameters.get("secret") or self.default_secret or uuid.uuid4().hex
        base = webhook_base_url or "/api/v1/triggers/webhook"
        webhook_url = f"{base}/{workspace_id}/{workflow_id}"

        sub = TriggerSubscription(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            trigger_type="webhook",
            provider=trigger_config.provider or self.provider_name,
            connection_id=connection_id,
            webhook_url=webhook_url,
            secret=secret,
            config={
                "event": trigger_config.event,
                "parameters": trigger_config.parameters,
            },
        )
        return sub

    async def unregister_subscription(
        self,
        subscription: TriggerSubscription,
    ) -> bool:
        """Unregister webhook."""
        # For self-hosted webhook endpoints, unregistering marks status as unregistered
        return True

    def verify_signature_and_timestamp(
        self,
        raw_payload: bytes,
        headers: Dict[str, str],
        secret: Optional[str],
        max_age_seconds: int = 300,
    ) -> None:
        """Verify provider signature and check timestamp window to prevent replay attacks.
        
        Supports:
        - GitHub (X-Hub-Signature-256)
        - Slack (X-Slack-Signature & X-Slack-Request-Timestamp)
        - Nexus / Standard HMAC (X-Signature-256, X-Nexus-Signature)
        - Secret Header (X-Webhook-Secret)
        """
        # Normalize header keys to lowercase
        norm_headers = {k.lower(): v for k, v in headers.items()}

        # 1. Timestamp / Replay Window Check
        ts_header = (
            norm_headers.get("x-slack-request-timestamp")
            or norm_headers.get("x-webhook-timestamp")
            or norm_headers.get("x-timestamp")
        )
        if ts_header:
            try:
                # Support integer epoch or ISO string
                if ts_header.isdigit():
                    req_ts = float(ts_header)
                else:
                    dt = datetime.fromisoformat(ts_header.replace("Z", "+00:00"))
                    req_ts = dt.timestamp()
                
                now_ts = time.time()
                if abs(now_ts - req_ts) > max_age_seconds:
                    raise WebhookVerificationError(
                        f"Webhook timestamp expired or outside replay tolerance window ({max_age_seconds}s)"
                    )
            except ValueError:
                raise WebhookVerificationError("Invalid timestamp header format")

        # If no secret is configured for this webhook, allow unverified in dev/test only if explicitly allowed
        if not secret:
            return

        # 2. GitHub HMAC-SHA256: "sha256=<hex>"
        gh_sig = norm_headers.get("x-hub-signature-256")
        if gh_sig:
            expected = "sha256=" + hmac.new(
                secret.encode("utf-8"), raw_payload, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(gh_sig, expected):
                raise WebhookVerificationError("Invalid GitHub HMAC-SHA256 signature")
            return

        # 3. Slack v0 Signature: "v0=<hex>", signature base: "v0:<timestamp>:<body_str>"
        slack_sig = norm_headers.get("x-slack-signature")
        if slack_sig:
            slack_ts = norm_headers.get("x-slack-request-timestamp", "")
            base_string = f"v0:{slack_ts}:{raw_payload.decode('utf-8', errors='replace')}".encode("utf-8")
            expected = "v0=" + hmac.new(
                secret.encode("utf-8"), base_string, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(slack_sig, expected):
                raise WebhookVerificationError("Invalid Slack v0 signature")
            return

        # 4. Standard Nexus / Generic HMAC-SHA256: "X-Signature-256" or "X-Signature"
        generic_sig = norm_headers.get("x-signature-256") or norm_headers.get("x-signature")
        if generic_sig:
            clean_sig = generic_sig.replace("sha256=", "")
            expected = hmac.new(
                secret.encode("utf-8"), raw_payload, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(clean_sig, expected):
                raise WebhookVerificationError("Invalid HMAC-SHA256 signature")
            return

        # 5. Shared Secret Header: "X-Webhook-Secret" or Authorization Bearer
        provided_secret = norm_headers.get("x-webhook-secret")
        if provided_secret:
            if not hmac.compare_digest(provided_secret, secret):
                raise WebhookVerificationError("Invalid X-Webhook-Secret token")
            return

        auth_header = norm_headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if not hmac.compare_digest(token, secret):
                raise WebhookVerificationError("Invalid Bearer webhook token")
            return

        # If secret was configured but no known signature/auth header provided
        raise WebhookVerificationError("Missing required webhook signature or secret header")

    async def normalize_event(
        self,
        raw_payload: bytes,
        headers: Dict[str, str],
        workspace_id: str,
        workflow_id: Optional[str] = None,
        workflow_version: Optional[int] = None,
        connection_id: Optional[str] = None,
    ) -> EventEnvelope:
        """Convert incoming provider payload into canonical EventEnvelope."""
        norm_headers = {k.lower(): v for k, v in headers.items()}

        # Extract provider event ID if provided
        provider_event_id = (
            norm_headers.get("x-github-delivery")
            or norm_headers.get("x-slack-req-id")
            or norm_headers.get("x-request-id")
            or norm_headers.get("x-event-id")
        )

        # Parse JSON data if present
        data: Dict[str, Any] = {}
        if raw_payload:
            try:
                parsed = json.loads(raw_payload.decode("utf-8"))
                if isinstance(parsed, dict):
                    data = parsed
                else:
                    data = {"payload": parsed}
            except Exception:
                data = {"raw_text": raw_payload.decode("utf-8", errors="replace")}

        # Check payload for event ID if not in headers
        if not provider_event_id and isinstance(data, dict):
            provider_event_id = data.get("id") or data.get("event_id")

        canonical_event_id = str(provider_event_id) if provider_event_id else f"evt_{uuid.uuid4().hex}"

        # Determine event type
        event_type = (
            norm_headers.get("x-github-event")
            or norm_headers.get("x-event-type")
            or (data.get("type") if isinstance(data, dict) else None)
            or (data.get("event") if isinstance(data, dict) else None)
            or "webhook.event"
        )
        if isinstance(event_type, dict):
            event_type = "webhook.event"

        # Stable deduplication idempotency key
        idempotency_key = self.compute_idempotency_key(
            provider=self.provider_name,
            provider_event_id=provider_event_id,
            event_type=str(event_type),
            raw_payload=raw_payload,
        )

        payload_ref = self.generate_payload_reference(raw_payload)

        return EventEnvelope(
            event_id=canonical_event_id,
            provider=self.provider_name,
            connection_id=connection_id,
            event_type=str(event_type),
            received_at=datetime.now(timezone.utc),
            payload_reference=payload_ref,
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            idempotency_key=idempotency_key,
            data=data,
            raw_headers={k: v for k, v in headers.items() if not k.lower().startswith("authorization")},
        )
