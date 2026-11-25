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

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..ai.llm import LLMClient, get_llm_client
from ..ai.pipeline import WorkflowGenerationPipeline
from ..ai.validator import validate_workflow_definition
from ..integrations.registry import ToolRegistry, get_tool_registry
from ..logger import logger
from ..models.connection import ConnectionStatus
from ..models.workflow import (
    CURRENT_WORKFLOW_SCHEMA_VERSION,
    WorkflowDefinition,
    WorkflowExplanation,
    WorkflowGenerationResult,
    WorkflowStatus,
    get_workflow_json_schema,
)
from ..repositories.connection_repo import ConnectionRepository
from ..repositories.workflow_repo import WorkflowRepository
from ..triggers.lifecycle import SubscriptionLifecycleManager


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
            # Save as draft workflow in workspace with version 1
            wf_doc = {
                "workspace_id": workspace_id,
                "name": result.workflow.name,
                "description": result.workflow.description,
                "status": WorkflowStatus.DRAFT.value,
                "schema_version": result.workflow.schema_version,
                "current_version": 1,
                "active_version": None,
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

            # Persist immutable version 1
            version_doc = {
                "workspace_id": workspace_id,
                "workflow_id": saved_workflow_id,
                "version": 1,
                "definition": {
                    "schema_version": result.workflow.schema_version,
                    "name": result.workflow.name,
                    "description": result.workflow.description,
                    "status": WorkflowStatus.DRAFT.value,
                    "required_integrations": result.workflow.required_integrations,
                    "trigger": result.workflow.trigger.model_dump(),
                    "steps": [s.model_dump() for s in result.workflow.steps],
                },
                "change_summary": "Initial draft generated by AI",
                "created_by": user_id,
                "created_at": datetime.now(timezone.utc),
            }
            await self.repo.create_version(version_doc)

        return {
            "generation_id": generation_id,
            "workflow_id": saved_workflow_id,
            "result": result.model_dump(),
        }

    async def edit_workflow_via_prompt(
        self,
        workspace_id: str,
        user_id: str,
        workflow_id: str,
        prompt: str,
        save_draft: bool = True,
    ) -> Dict[str, Any]:
        """Revise an existing workflow using natural-language prompt instructions."""
        existing = await self.repo.get_workflow(workspace_id, workflow_id)
        if not existing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        if existing.get("status") == WorkflowStatus.ARCHIVED.value:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot edit an archived workflow")

        # Get latest definition
        current_version = existing.get("current_version", 1)
        version_doc = await self.repo.get_version(workspace_id, workflow_id, current_version)
        if version_doc and version_doc.get("definition"):
            existing_def = version_doc["definition"]
        else:
            existing_def = {
                "schema_version": existing.get("schema_version", CURRENT_WORKFLOW_SCHEMA_VERSION),
                "name": existing.get("name"),
                "description": existing.get("description"),
                "required_integrations": existing.get("required_integrations", []),
                "trigger": existing.get("trigger", {"type": "manual"}),
                "steps": existing.get("steps", []),
                "status": existing.get("status", WorkflowStatus.DRAFT.value),
            }

        start_time = datetime.now(timezone.utc)
        result: WorkflowGenerationResult = await self.pipeline.run(
            prompt=prompt,
            workspace_id=workspace_id,
            user_id=user_id,
            existing_workflow=existing_def,
        )
        end_time = datetime.now(timezone.utc)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Record generation metadata
        generation_doc: Dict[str, Any] = {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "workflow_id": workflow_id,
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

        updated_workflow = None
        if save_draft and result.success and result.workflow:
            wf_data = {
                "name": result.workflow.name,
                "description": result.workflow.description,
                "trigger": result.workflow.trigger.model_dump(),
                "steps": [s.model_dump() for s in result.workflow.steps],
                "required_integrations": result.workflow.required_integrations,
            }
            change_summary = f"AI Prompt Edit: {prompt[:80]}"
            updated_workflow = await self.update_workflow_definition(
                workspace_id=workspace_id,
                user_id=user_id,
                workflow_id=workflow_id,
                data=wf_data,
                change_summary=change_summary,
            )

        return {
            "generation_id": generation_id,
            "workflow_id": workflow_id,
            "workflow": updated_workflow,
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
        tool_desc_map = {t.id: t for t in tools}
        return validate_workflow_definition(wf_obj, permitted_tool_ids, tool_descriptors=tool_desc_map)

    async def verify_workflow_integrations(
        self,
        workspace_id: str,
        required_integrations: List[str],
    ) -> List[str]:
        """Verify that all required third-party integrations are actively connected and healthy."""
        conn_repo = ConnectionRepository(self.db)
        errors: List[str] = []
        for app in required_integrations:
            conns = await conn_repo.list_by_workspace(
                workspace_id=workspace_id,
                app_name=app,
                status=ConnectionStatus.CONNECTED,
            )
            if not conns:
                errors.append(f"Required integration '{app}' is not connected or active in this workspace.")
        return errors

    async def create_workflow(
        self,
        workspace_id: str,
        user_id: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create a new workflow with initial version 1 in draft status."""
        name = data.get("name", "Untitled Workflow")
        description = data.get("description")
        trigger = data.get("trigger", {"type": "manual"})
        steps = data.get("steps", [])
        required_integrations = data.get("required_integrations", [])

        definition_data = {
            "schema_version": CURRENT_WORKFLOW_SCHEMA_VERSION,
            "name": name,
            "description": description,
            "required_integrations": required_integrations,
            "trigger": trigger,
            "steps": steps,
            "status": WorkflowStatus.DRAFT.value,
        }
        wf_obj = WorkflowDefinition.model_validate(definition_data)

        now = datetime.now(timezone.utc)
        wf_doc = {
            "workspace_id": workspace_id,
            "name": wf_obj.name,
            "description": wf_obj.description,
            "status": WorkflowStatus.DRAFT.value,
            "schema_version": wf_obj.schema_version,
            "current_version": 1,
            "active_version": None,
            "required_integrations": wf_obj.required_integrations,
            "trigger": wf_obj.trigger.model_dump(),
            "steps": [s.model_dump() for s in wf_obj.steps],
            "created_by": user_id,
            "created_at": now,
            "updated_at": now,
        }
        wf_id = await self.repo.create(wf_doc)

        version_doc = {
            "workspace_id": workspace_id,
            "workflow_id": wf_id,
            "version": 1,
            "definition": definition_data,
            "change_summary": "Initial workflow creation",
            "created_by": user_id,
            "created_at": now,
        }
        await self.repo.create_version(version_doc)

        saved = await self.repo.get_workflow(workspace_id, wf_id)
        assert saved is not None
        return saved

    async def update_workflow_definition(
        self,
        workspace_id: str,
        user_id: str,
        workflow_id: str,
        data: Dict[str, Any],
        change_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update workflow metadata or definition with strict immutable versioning.

        If the workflow is currently ACTIVE or PAUSED, editing creates a new immutable version
        rather than modifying the active version in place (Requirement 6).
        """
        existing = await self.repo.get_workflow(workspace_id, workflow_id)
        if not existing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")

        current_status = existing.get("status", WorkflowStatus.DRAFT.value)
        if current_status == WorkflowStatus.ARCHIVED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Archived workflow cannot be edited",
            )

        now = datetime.now(timezone.utc)
        current_version = existing.get("current_version", 1)
        active_version = existing.get("active_version")

        # Extract updated values or fallback to existing
        name = data.get("name", existing.get("name"))
        description = data.get("description", existing.get("description"))
        trigger = data.get("trigger", existing.get("trigger", {"type": "manual"}))
        steps = data.get("steps", existing.get("steps", []))
        required_integrations = data.get("required_integrations", existing.get("required_integrations", []))

        updated_definition = {
            "schema_version": CURRENT_WORKFLOW_SCHEMA_VERSION,
            "name": name,
            "description": description,
            "required_integrations": required_integrations,
            "trigger": trigger,
            "steps": steps,
            "status": WorkflowStatus.DRAFT.value,
        }
        # Validate Pydantic schema
        WorkflowDefinition.model_validate(updated_definition)

        is_active_or_paused = current_status in [WorkflowStatus.ACTIVE.value, WorkflowStatus.PAUSED.value]

        if is_active_or_paused:
            # Create a NEW immutable version (Requirement 6)
            new_version = current_version + 1
            version_doc = {
                "workspace_id": workspace_id,
                "workflow_id": workflow_id,
                "version": new_version,
                "definition": updated_definition,
                "change_summary": change_summary or f"Version {new_version} edit from active workflow",
                "created_by": user_id,
                "created_at": now,
            }
            await self.repo.create_version(version_doc)

            # Update workflow: definition & current_version updated; active_version stays unchanged!
            update_data = {
                "name": name,
                "description": description,
                "trigger": trigger,
                "steps": steps,
                "required_integrations": required_integrations,
                "current_version": new_version,
                # active_version remains unchanged so running executions/triggers keep using it
                "active_version": active_version,
                "updated_at": now,
            }
            await self.repo.update_workflow(workspace_id, workflow_id, update_data)
        else:
            # Workflow is in DRAFT, READY, or NEEDS_ATTENTION.
            # Reset status to DRAFT on definition change so it must be re-validated
            version_doc = {
                "workspace_id": workspace_id,
                "workflow_id": workflow_id,
                "version": current_version,
                "definition": updated_definition,
                "change_summary": change_summary or "Draft updated",
                "created_by": user_id,
                "created_at": now,
            }
            # Update or create version document
            existing_ver = await self.repo.get_version(workspace_id, workflow_id, current_version)
            if existing_ver:
                await self.repo.versions_collection.update_one(
                    {"workspace_id": workspace_id, "workflow_id": workflow_id, "version": current_version},
                    {"$set": {"definition": updated_definition, "change_summary": change_summary, "updated_at": now}},
                )
            else:
                await self.repo.create_version(version_doc)

            update_data = {
                "name": name,
                "description": description,
                "trigger": trigger,
                "steps": steps,
                "required_integrations": required_integrations,
                "status": WorkflowStatus.DRAFT.value,
                "updated_at": now,
            }
            await self.repo.update_workflow(workspace_id, workflow_id, update_data)

        updated = await self.repo.get_workflow(workspace_id, workflow_id)
        assert updated is not None
        return updated

    async def validate_workflow_by_id(
        self,
        workspace_id: str,
        workflow_id: str,
        version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Validate workflow steps, DAG, registry tools, parameters, reachability, and integrations."""
        wf = await self.repo.get_workflow(workspace_id, workflow_id)
        if not wf:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")

        target_version = version or wf.get("current_version", 1)
        version_doc = await self.repo.get_version(workspace_id, workflow_id, target_version)
        if version_doc:
            definition_data = version_doc.get("definition", {})
        else:
            definition_data = {
                "schema_version": wf.get("schema_version", CURRENT_WORKFLOW_SCHEMA_VERSION),
                "name": wf.get("name"),
                "description": wf.get("description"),
                "required_integrations": wf.get("required_integrations", []),
                "trigger": wf.get("trigger", {}),
                "steps": wf.get("steps", []),
            }

        definition_errors = await self.validate_custom_workflow(workspace_id, definition_data)
        integration_errors = await self.verify_workflow_integrations(
            workspace_id,
            definition_data.get("required_integrations", []),
        )

        all_errors = definition_errors + integration_errors
        is_valid = len(all_errors) == 0

        # If currently in draft or needs_attention and passes validation, transition to READY
        if is_valid and wf.get("status") in [WorkflowStatus.DRAFT.value, WorkflowStatus.NEEDS_ATTENTION.value]:
            await self.repo.update_workflow(workspace_id, workflow_id, {
                "status": WorkflowStatus.READY.value,
                "validation_errors": [],
                "updated_at": datetime.now(timezone.utc),
            })
        elif not is_valid and wf.get("status") not in [WorkflowStatus.ACTIVE.value, WorkflowStatus.ARCHIVED.value]:
            await self.repo.update_workflow(workspace_id, workflow_id, {
                "status": WorkflowStatus.NEEDS_ATTENTION.value,
                "validation_errors": all_errors,
                "updated_at": datetime.now(timezone.utc),
            })

        return {
            "valid": is_valid,
            "workflow_id": workflow_id,
            "version": target_version,
            "definition_errors": definition_errors,
            "integration_errors": integration_errors,
            "errors": all_errors,
        }

    async def activate_workflow(
        self,
        workspace_id: str,
        user_id: str,
        workflow_id: str,
        approved: bool,
        version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Activate workflow with explicit user approval and deterministic verification.

        Requirements 4, 6, 8.
        """
        wf = await self.repo.get_workflow(workspace_id, workflow_id)
        if not wf:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")

        if wf.get("status") == WorkflowStatus.ARCHIVED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot activate an archived workflow",
            )

        if not approved:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Explicit user approval is required before activating a workflow.",
            )

        target_version = version or wf.get("current_version", 1)

        # Retrieve version definition
        version_doc = await self.repo.get_version(workspace_id, workflow_id, target_version)
        if version_doc:
            definition_data = version_doc.get("definition", {})
        else:
            definition_data = {
                "schema_version": wf.get("schema_version", CURRENT_WORKFLOW_SCHEMA_VERSION),
                "name": wf.get("name"),
                "description": wf.get("description"),
                "required_integrations": wf.get("required_integrations", []),
                "trigger": wf.get("trigger", {}),
                "steps": wf.get("steps", []),
            }

        # Deterministic validation
        def_errors = await self.validate_custom_workflow(workspace_id, definition_data)
        if def_errors:
            await self.repo.update_workflow(workspace_id, workflow_id, {
                "status": WorkflowStatus.NEEDS_ATTENTION.value,
                "validation_errors": def_errors,
                "updated_at": datetime.now(timezone.utc),
            })
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Workflow validation failed: {'; '.join(def_errors)}",
            )

        # Integration verification
        integration_errors = await self.verify_workflow_integrations(
            workspace_id,
            definition_data.get("required_integrations", []),
        )
        if integration_errors:
            await self.repo.update_workflow(workspace_id, workflow_id, {
                "status": WorkflowStatus.NEEDS_ATTENTION.value,
                "validation_errors": integration_errors,
                "updated_at": datetime.now(timezone.utc),
            })
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Integration verification failed: {'; '.join(integration_errors)}",
            )

        now = datetime.now(timezone.utc)
        update_data = {
            "status": WorkflowStatus.ACTIVE.value,
            "active_version": target_version,
            "approved_at": now,
            "approved_by": user_id,
            "validation_errors": [],
            "updated_at": now,
        }
        await self.repo.update_workflow(workspace_id, workflow_id, update_data)

        # Register trigger subscription (Phase 7 Requirement 8)
        try:
            lifecycle = SubscriptionLifecycleManager(self.db)
            trigger_dict = definition_data.get("trigger", {"type": "manual"})
            await lifecycle.register_workflow_trigger(
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                workflow_version=target_version,
                trigger_config_dict=trigger_dict,
            )
        except Exception as e:
            logger.error("Failed to register trigger subscription on activation", error=str(e))

        updated = await self.repo.get_workflow(workspace_id, workflow_id)
        assert updated is not None
        return updated

    async def pause_workflow(
        self,
        workspace_id: str,
        user_id: str,
        workflow_id: str,
    ) -> Dict[str, Any]:
        """Pause an active workflow to temporarily halt triggers."""
        wf = await self.repo.get_workflow(workspace_id, workflow_id)
        if not wf:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        if wf.get("status") != WorkflowStatus.ACTIVE.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only active workflows can be paused (current status: {wf.get('status')})",
            )

        now = datetime.now(timezone.utc)
        await self.repo.update_workflow(workspace_id, workflow_id, {
            "status": WorkflowStatus.PAUSED.value,
            "paused_at": now,
            "paused_by": user_id,
            "updated_at": now,
        })
        # Pause trigger subscription
        try:
            lifecycle = SubscriptionLifecycleManager(self.db)
            await lifecycle.deactivate_workflow_trigger(workspace_id, workflow_id)
        except Exception as e:
            logger.error("Failed to deactivate trigger subscription", error=str(e))

        updated = await self.repo.get_workflow(workspace_id, workflow_id)
        assert updated is not None
        return updated

    async def resume_workflow(
        self,
        workspace_id: str,
        user_id: str,
        workflow_id: str,
    ) -> Dict[str, Any]:
        """Resume a paused workflow."""
        wf = await self.repo.get_workflow(workspace_id, workflow_id)
        if not wf:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        if wf.get("status") != WorkflowStatus.PAUSED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only paused workflows can be resumed (current status: {wf.get('status')})",
            )

        now = datetime.now(timezone.utc)
        await self.repo.update_workflow(workspace_id, workflow_id, {
            "status": WorkflowStatus.ACTIVE.value,
            "resumed_at": now,
            "resumed_by": user_id,
            "updated_at": now,
        })
        # Resume trigger subscription
        try:
            lifecycle = SubscriptionLifecycleManager(self.db)
            await lifecycle.resume_workflow_trigger(workspace_id, workflow_id)
        except Exception as e:
            logger.error("Failed to resume trigger subscription", error=str(e))

        updated = await self.repo.get_workflow(workspace_id, workflow_id)
        assert updated is not None
        return updated

    async def archive_workflow(
        self,
        workspace_id: str,
        user_id: str,
        workflow_id: str,
    ) -> Dict[str, Any]:
        """Archive a workflow, permanently disabling execution and triggers."""
        wf = await self.repo.get_workflow(workspace_id, workflow_id)
        if not wf:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        if wf.get("status") == WorkflowStatus.ARCHIVED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workflow is already archived",
            )

        now = datetime.now(timezone.utc)
        await self.repo.update_workflow(workspace_id, workflow_id, {
            "status": WorkflowStatus.ARCHIVED.value,
            "archived_at": now,
            "archived_by": user_id,
            "updated_at": now,
        })
        # Clean up trigger subscription and checkpoints
        try:
            lifecycle = SubscriptionLifecycleManager(self.db)
            await lifecycle.unregister_workflow_trigger(workspace_id, workflow_id)
        except Exception as e:
            logger.error("Failed to unregister trigger subscription", error=str(e))

        updated = await self.repo.get_workflow(workspace_id, workflow_id)
        assert updated is not None
        return updated

    async def list_versions(
        self,
        workspace_id: str,
        workflow_id: str,
    ) -> List[Dict[str, Any]]:
        """List immutable version history for a workflow."""
        versions = await self.repo.list_versions(workspace_id, workflow_id)
        for v in versions:
            v["id"] = str(v["_id"])
            v.pop("_id", None)
        return versions

    async def get_version(
        self,
        workspace_id: str,
        workflow_id: str,
        version: int,
    ) -> Optional[Dict[str, Any]]:
        """Get exact immutable version definition."""
        v = await self.repo.get_version(workspace_id, workflow_id, version)
        if v:
            v["id"] = str(v["_id"])
            v.pop("_id", None)
        return v

    async def resolve_execution_version(
        self,
        workspace_id: str,
        workflow_id: str,
        execution_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve exact immutable workflow version for an execution or new trigger.

        Requirement 7:
        - Existing executions continue using their original workflow version.
        - New triggers use the newly activated version.
        """
        if execution_id:
            execution = await self.repo.get_execution(workspace_id, execution_id)
            if not execution:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
            exec_version = execution["workflow_version"]
            version_doc = await self.repo.get_version(workspace_id, workflow_id, exec_version)
            return {
                "execution_id": execution_id,
                "workflow_id": workflow_id,
                "pinned_version": exec_version,
                "definition": version_doc.get("definition") if version_doc else None,
                "source": "existing_execution",
            }

        # For a new trigger event: must resolve against active_version of active workflow
        wf = await self.repo.get_workflow(workspace_id, workflow_id)
        if not wf:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        if wf.get("status") != WorkflowStatus.ACTIVE.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot trigger inactive workflow (status: {wf.get('status')})",
            )
        active_ver = wf.get("active_version")
        if not active_ver:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workflow is active but has no active_version specified",
            )

        version_doc = await self.repo.get_version(workspace_id, workflow_id, active_ver)
        return {
            "workflow_id": workflow_id,
            "pinned_version": active_ver,
            "definition": version_doc.get("definition") if version_doc else None,
            "source": "active_trigger",
        }
