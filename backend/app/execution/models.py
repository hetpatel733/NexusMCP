"""Models and Data Transfer Objects for Durable Workflow Execution.

Defines schemas for:
- Workflow execution and step execution tracking
- Idempotency key generation
- Serialized activity inputs and outputs
- Payload references for large payloads
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PayloadReference(BaseModel):
    """Reference to externalized large payload."""
    payload_id: str
    storage: str = "mongodb"
    size_bytes: int
    content_type: str = "application/json"
    preview: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class StepExecutionRecord(BaseModel):
    """Detailed record of an individual workflow step execution attempt."""
    id: Optional[str] = None
    workspace_id: str
    execution_id: str
    workflow_id: str
    workflow_version: int
    step_id: str
    step_name: str
    attempt: int = 1
    status: StepExecutionStatus = StepExecutionStatus.PENDING
    idempotency_key: str
    correlation_id: Optional[str] = None
    input_data: Dict[str, Any] = Field(default_factory=dict)
    output_data: Optional[Dict[str, Any]] = None
    payload_ref: Optional[PayloadReference] = None
    error: Optional[str] = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    execution_time_ms: Optional[int] = None

    model_config = ConfigDict(extra="ignore")


class WorkflowExecutionInput(BaseModel):
    """Minimal, JSON-serializable input to initiate a Temporal workflow execution."""
    workspace_id: str
    workflow_id: str
    execution_id: str
    workflow_version: int
    trigger_event: Dict[str, Any] = Field(default_factory=dict)
    correlation_id: Optional[str] = None
    user_id: Optional[str] = None
    user_role: str = "member"

    model_config = ConfigDict(extra="ignore")


class WorkflowExecutionSummary(BaseModel):
    """Result summary returned by a completed workflow execution."""
    execution_id: str
    workspace_id: str
    workflow_id: str
    workflow_version: int
    status: ExecutionStatus
    step_count: int = 0
    completed_steps: List[str] = Field(default_factory=list)
    skipped_steps: List[str] = Field(default_factory=list)
    failed_step: Optional[str] = None
    error: Optional[str] = None
    correlation_id: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None

    model_config = ConfigDict(extra="ignore")


class StepExecutionInput(BaseModel):
    """Serialized input passed to execute_step_activity."""
    workspace_id: str
    workflow_id: str
    execution_id: str
    workflow_version: int
    step: Dict[str, Any]
    context_data: Dict[str, Any] = Field(default_factory=dict)
    attempt: int = 1
    correlation_id: Optional[str] = None
    user_id: Optional[str] = None
    user_role: str = "member"

    model_config = ConfigDict(extra="ignore")


class StepExecutionOutput(BaseModel):
    """Serialized output returned by execute_step_activity."""
    step_id: str
    status: StepExecutionStatus
    output: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    is_retryable: bool = False
    execution_time_ms: int = 0
    idempotency_key: str
    skipped: bool = False
    skip_reason: Optional[str] = None

    model_config = ConfigDict(extra="ignore")
