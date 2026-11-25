"""Workflow Generation and Management Service.

Coordinates:
1. LangGraph workflow generation pipeline.
2. Prompt injection safety checks.
3. Tool discovery and permissions via ToolRegistry.
4. Schema validation and verification.
5. MongoDB persistence for generation audits, reproducibility, and workflow drafts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..ai.llm import LLMClient, get_llm_client
from ..ai.pipeline import WorkflowGenerationPipeline
from ..ai.validator import validate_workflow_definition
from ..integrations.registry import ToolRegistry, get_tool_registry
from ..logger import logger
from ..models.workflow import (
    CURRENT_WORKFLOW_SCHEMA_VERSION,
    WorkflowDefinition,
    WorkflowExplanation,
    WorkflowGenerationResult,
    WorkflowStatus,
    get_workflow_json_schema,
)
from ..repositories.workflow_repo import WorkflowRepository


class WorkflowService:
    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        tool_registry: Optional[ToolRegistry] = None,
        llm_client: Optional[LLMClient] = None,
    ):
        self.db = db
        self.repo = WorkflowRepository(db)
        self.tool_registry = tool_registry or get_tool_registry(db)
        self.llm_client = llm_client or get_llm_client()
        self.pipeline = WorkflowGenerationPipeline(self.llm_client, self.tool_registry)

    async def generate_workflow(
        self,
        prompt: str,
        workspace_id: str,
        user_id: str,
        save_draft: bool = False,
    ) -> Dict[str, Any]:
        """Execute the LangGraph generation pipeline, validate, explain, and persist metadata."""
        start_time = datetime.now(timezone.utc)

        # Run bounded LangGraph generation pipeline
        result: WorkflowGenerationResult = await self.pipeline.run(
            prompt=prompt,
            workspace_id=workspace_id,
            user_id=user_id,
        )

        end_time = datetime.now(timezone.utc)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Persist generation record for reproducibility (Requirement 8)
        generation_doc: Dict[str, Any] = {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "raw_prompt": result.raw_prompt,
            "sanitized_prompt": result.sanitized_prompt,
            "status": result.status,
            "success": result.success,
            "schema_version": CURRENT_WORKFLOW_SCHEMA_VERSION,
            "model_metadata": result.model_metadata,
            "generated_workflow": result.workflow.model_dump() if result.workflow else None,
            "clarifications": [c.model_dump() for c in result.clarifications],
            "explanation": result.explanation.model_dump() if result.explanation else None,
            "errors": result.errors,
            "duration_ms": duration_ms,
        }

        generation_id = await self.repo.record_generation(generation_doc)

        saved_workflow_id = None
        if save_draft and result.success and result.workflow:
            # Save as draft workflow in workspace
            wf_doc = {
                "workspace_id": workspace_id,
                "name": result.workflow.name,
                "description": result.workflow.description,
                "status": WorkflowStatus.DRAFT.value,
                "schema_version": result.workflow.schema_version,
                "required_integrations": result.workflow.required_integrations,
                "trigger": result.workflow.trigger.model_dump(),
                "steps": [s.model_dump() for s in result.workflow.steps],
                "explanation": result.explanation.model_dump() if result.explanation else None,
                "generation_id": generation_id,
                "created_by": user_id,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            saved_workflow_id = await self.repo.create(wf_doc)

        return {
            "generation_id": generation_id,
            "workflow_id": saved_workflow_id,
            "result": result.model_dump(),
        }

    async def get_generation_record(
        self,
        workspace_id: str,
        generation_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve stored generation metadata ensuring workspace isolation."""
        return await self.repo.get_generation(workspace_id, generation_id)

    async def list_generation_records(
        self,
        workspace_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List generation history for workspace."""
        records = await self.repo.list_generations(workspace_id, limit)
        for r in records:
            r["id"] = str(r["_id"])
            r.pop("_id", None)
        return records

    async def validate_custom_workflow(
        self,
        workspace_id: str,
        workflow_data: Dict[str, Any],
    ) -> List[str]:
        """Validate an arbitrary or updated workflow definition against workspace tools."""
        try:
            wf_obj = WorkflowDefinition.model_validate(workflow_data)
        except Exception as e:
            return [f"Schema validation error: {str(e)}"]

        tools = await self.tool_registry.list_tools(workspace_id=workspace_id, enabled_only=True)
        permitted_tool_ids = {t.id for t in tools}
        return validate_workflow_definition(wf_obj, permitted_tool_ids)
