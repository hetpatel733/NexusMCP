"""Versioned Workflow Definition and Schema Models for NexusMCP.

Follows Phase 5 specifications and rules.md:
- Explicitly represents triggers, conditions, actions, data mappings,
  dependencies, error policies, and required integrations.
- Maintains strict versioning (schema_version).
- Supports JSON schema generation and deterministic validation.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


CURRENT_WORKFLOW_SCHEMA_VERSION = "1.0.0"


class TriggerType(str, Enum):
    WEBHOOK = "webhook"
    POLLING = "polling"
    SCHEDULE = "schedule"
    MANUAL = "manual"


class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    ACTIVE = "active"
    PAUSED = "paused"
    NEEDS_ATTENTION = "needs_attention"
    ARCHIVED = "archived"


class ErrorHandlingStrategy(str, Enum):
    STOP = "stop"
    CONTINUE = "continue"
    RETRY = "retry"
    FALLBACK = "fallback"


class TriggerConfig(BaseModel):
    """Trigger specification for a workflow."""
    type: TriggerType = Field(description="Trigger category: webhook, polling, schedule, manual")
    provider: Optional[str] = Field(default=None, description="App provider associated with trigger, e.g. 'github', 'slack'")
    event: Optional[str] = Field(default=None, description="Event name, e.g. 'new_issue', 'pull_request'")
    cron: Optional[str] = Field(default=None, description="Cron expression for schedule triggers")
    interval_seconds: Optional[int] = Field(default=None, ge=10, description="Interval in seconds for polling")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Filter or query params for trigger")

    model_config = ConfigDict(extra="forbid")


class DataMapping(BaseModel):
    """Data mapping from triggers, previous steps, or literal constants."""
    target_field: str = Field(description="Target argument field name for action/step")
    source: str = Field(description="Source reference, e.g. 'steps.step_1.output.id' or 'trigger.data.issue.title' or 'literal'")
    value: Optional[Any] = Field(default=None, description="Static literal value when source is 'literal'")

    model_config = ConfigDict(extra="forbid")


class StepCondition(BaseModel):
    """Condition that must evaluate to true for step to execute."""
    field: str = Field(description="Field path to evaluate, e.g. 'steps.step_1.output.state'")
    operator: str = Field(description="Comparison operator: eq, neq, contains, in, gt, lt, is_null, not_null")
    value: Any = Field(default=None, description="Comparison value")

    model_config = ConfigDict(extra="forbid")


class ErrorPolicy(BaseModel):
    """Error handling and retry policy for a workflow step."""
    strategy: ErrorHandlingStrategy = Field(default=ErrorHandlingStrategy.STOP)
    max_retries: int = Field(default=3, ge=0, le=10)
    retry_delay_seconds: int = Field(default=5, ge=1, le=3600)
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=5.0)

    model_config = ConfigDict(extra="forbid")


class WorkflowStep(BaseModel):
    """Action step in a workflow."""
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$", description="Unique identifier for step, e.g. 'step_1'")
    name: str = Field(min_length=1, max_length=100, description="Descriptive human-readable step name")
    tool_id: str = Field(description="Fully-qualified tool identifier in tool registry (e.g. composio:github:create_issue)")
    provider: str = Field(description="Tool provider, e.g. 'composio' or 'mcp'")
    app_name: Optional[str] = Field(default=None, description="App or server name, e.g. 'github'")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Static arguments or parameters")
    data_mappings: List[DataMapping] = Field(default_factory=list, description="Dynamic input mappings from previous steps/triggers")
    dependencies: List[str] = Field(default_factory=list, description="IDs of steps that must complete before this step")
    conditions: List[StepCondition] = Field(default_factory=list, description="Conditions to evaluate before executing step")
    error_policy: ErrorPolicy = Field(default_factory=ErrorPolicy, description="Retry and error handling policy")

    model_config = ConfigDict(extra="forbid")


class ClarificationQuestion(BaseModel):
    """Request for missing essential information required to complete a workflow."""
    parameter: str = Field(description="Parameter name or resource identifier needed (e.g. channel_id, repo_name)")
    target: str = Field(description="Target tool or context requiring this parameter")
    prompt: str = Field(description="Question to present to the user")
    description: Optional[str] = Field(default=None, description="Why this information is required")

    model_config = ConfigDict(extra="forbid")


class StepExplanation(BaseModel):
    """Human-readable explanation of an individual workflow step."""
    step_id: str
    step_name: str
    tool_id: str
    action_summary: str
    permissions_required: List[str] = Field(default_factory=list)
    risk_level: str = Field(default="low")


class WorkflowExplanation(BaseModel):
    """Human-readable explanation of the full generated workflow."""
    overview: str = Field(description="High-level description of what the workflow accomplishes")
    trigger_summary: str = Field(description="Explanation of how the workflow is triggered")
    steps: List[StepExplanation] = Field(default_factory=list, description="Step-by-step breakdown")
    required_integrations: List[str] = Field(default_factory=list, description="List of apps/integrations required")
    potential_risks: List[str] = Field(default_factory=list, description="Potential risks or side-effects")

    model_config = ConfigDict(extra="forbid")


class WorkflowDefinition(BaseModel):
    """Strict canonical Workflow Definition Schema for NexusMCP."""
    schema_version: str = Field(default=CURRENT_WORKFLOW_SCHEMA_VERSION, description="Version of the workflow schema")
    name: str = Field(min_length=1, max_length=100, description="Workflow name")
    description: Optional[str] = Field(default=None, max_length=500, description="Workflow description")
    required_integrations: List[str] = Field(default_factory=list, description="Apps/services required, e.g. ['github', 'slack']")
    trigger: TriggerConfig = Field(description="Workflow trigger specification")
    steps: List[WorkflowStep] = Field(default_factory=list, description="Ordered or DAG steps")
    status: WorkflowStatus = Field(default=WorkflowStatus.DRAFT, description="Workflow lifecycle state")

    model_config = ConfigDict(extra="forbid")


class WorkflowGenerationResult(BaseModel):
    """Structured result from the AI Workflow Generation pipeline."""
    success: bool
    status: str = Field(description="'complete', 'needs_clarification', or 'error'")
    workflow: Optional[WorkflowDefinition] = None
    clarifications: List[ClarificationQuestion] = Field(default_factory=list)
    explanation: Optional[WorkflowExplanation] = None
    errors: List[str] = Field(default_factory=list)
    raw_prompt: str
    sanitized_prompt: str
    model_metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


def get_workflow_json_schema() -> Dict[str, Any]:
    """Return the JSON schema representation of the WorkflowDefinition."""
    return WorkflowDefinition.model_json_schema()
