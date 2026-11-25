"""Bounded LangGraph Workflow Generation Pipeline.

Implements the 6 required stages:
1. Understand the user's request.
2. Identify required applications.
3. Retrieve tools permitted for the user's workspace.
4. Generate a workflow definition (or ask for clarifications if information is missing).
5. Validate the generated schema deterministically.
6. Explain the generated workflow.

All tool executions during generation are strictly prohibited.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, TypedDict
from pydantic import BaseModel, Field

from langgraph.graph import StateGraph, END

from ..integrations.registry import ToolRegistry
from ..logger import logger
from ..models.workflow import (
    ClarificationQuestion,
    WorkflowDefinition,
    WorkflowExplanation,
    WorkflowGenerationResult,
)
from .llm import LLMClient
from .prompts import (
    build_explanation_prompt,
    build_intent_prompt,
    build_workflow_prompt,
    build_workflow_revise_prompt,
)
from .safety import sanitize_tool_text, sanitize_user_prompt
from .validator import validate_workflow_definition


# --- Pydantic models for intermediate LLM calls ---

class IntentAnalysisResponse(BaseModel):
    user_intent: str = Field(description="Summary of what automation the user wants to achieve")
    detected_apps: List[str] = Field(default_factory=list, description="Application names required (e.g. github, slack)")
    clarification_needed: bool = Field(default=False, description="Whether essential missing information prevents workflow generation")
    clarifications: List[ClarificationQuestion] = Field(
        default_factory=list,
        description="List of specific questions for essential missing information",
    )


# --- Pipeline State ---

class PipelineState(TypedDict):
    raw_prompt: str
    sanitized_prompt: str
    workspace_id: str
    user_id: str
    detected_threats: List[str]
    is_safe: bool
    user_intent: str
    detected_apps: List[str]
    clarification_needed: bool
    clarifications: List[Dict[str, Any]]
    permitted_tools: List[Dict[str, Any]]
    permitted_tool_ids: List[str]
    existing_workflow: Optional[Dict[str, Any]]
    workflow_definition: Optional[Dict[str, Any]]
    validation_errors: List[str]
    explanation: Optional[Dict[str, Any]]
    status: str
    model_metadata: Dict[str, Any]


class WorkflowGenerationPipeline:
    """Bounded LangGraph-based workflow generation pipeline."""

    def __init__(self, llm_client: LLMClient, tool_registry: ToolRegistry):
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(PipelineState)

        # Stage 1: Understand request & prompt sanitization
        workflow.add_node("understand_request", self._node_understand_request)
        # Stage 2: Identify applications & check clarifications
        workflow.add_node("identify_applications", self._node_identify_applications)
        # Stage 3: Retrieve permitted tools from tool registry
        workflow.add_node("retrieve_permitted_tools", self._node_retrieve_permitted_tools)
        # Stage 4: Generate workflow definition
        workflow.add_node("generate_workflow", self._node_generate_workflow)
        # Stage 5: Validate generated schema and tool references
        workflow.add_node("validate_workflow", self._node_validate_workflow)
        # Stage 6: Explain generated workflow
        workflow.add_node("explain_workflow", self._node_explain_workflow)

        # Entry point
        workflow.set_entry_point("understand_request")

        # Edges
        workflow.add_edge("understand_request", "identify_applications")

        # Conditional route: If clarifications needed or threats detected, route to END
        def route_after_intent(state: PipelineState) -> str:
            if not state.get("is_safe", True):
                return "END"
            if state.get("clarification_needed", False):
                return "END"
            return "retrieve_permitted_tools"

        workflow.add_conditional_edges(
            "identify_applications",
            route_after_intent,
            {
                "END": END,
                "retrieve_permitted_tools": "retrieve_permitted_tools",
            },
        )

        workflow.add_edge("retrieve_permitted_tools", "generate_workflow")
        workflow.add_edge("generate_workflow", "validate_workflow")

        def route_after_validation(state: PipelineState) -> str:
            errors = state.get("validation_errors", [])
            if errors:
                return "END"
            return "explain_workflow"

        workflow.add_conditional_edges(
            "validate_workflow",
            route_after_validation,
            {
                "END": END,
                "explain_workflow": "explain_workflow",
            },
        )

        workflow.add_edge("explain_workflow", END)

        return workflow.compile()

    # --- Node implementations ---

    async def _node_understand_request(self, state: PipelineState) -> Dict[str, Any]:
        """Stage 1: Sanitize prompt against prompt injection and understand the user's request."""
        raw_prompt = state["raw_prompt"]
        sanitization = sanitize_user_prompt(raw_prompt)

        threats = sanitization.detected_threats
        is_safe = sanitization.is_safe

        if not is_safe:
            return {
                "sanitized_prompt": sanitization.sanitized_text,
                "detected_threats": threats,
                "is_safe": False,
                "status": "error",
                "validation_errors": [
                    f"Prompt injection or security policy violation detected: {', '.join(threats)}"
                ],
            }

        return {
            "sanitized_prompt": sanitization.sanitized_text,
            "detected_threats": [],
            "is_safe": True,
        }

    async def _node_identify_applications(self, state: PipelineState) -> Dict[str, Any]:
        """Stage 2: Identify required applications and check for missing essential parameters."""
        if not state.get("is_safe", True):
            return {
                "user_intent": "Blocked due to security policy violation",
                "detected_apps": [],
                "clarification_needed": False,
                "status": "error",
            }

        prompt_messages = build_intent_prompt(state["sanitized_prompt"])
        analysis: IntentAnalysisResponse = await self.llm_client.generate_structured(
            messages=prompt_messages,
            response_model=IntentAnalysisResponse,
        )

        # Check if missing parameters require clarification
        clarifications_list = [c.model_dump() for c in analysis.clarifications]
        clarification_needed = analysis.clarification_needed or len(clarifications_list) > 0

        status = "needs_clarification" if clarification_needed else "in_progress"

        return {
            "user_intent": analysis.user_intent,
            "detected_apps": analysis.detected_apps,
            "clarification_needed": clarification_needed,
            "clarifications": clarifications_list,
            "status": status,
        }

    async def _node_retrieve_permitted_tools(self, state: PipelineState) -> Dict[str, Any]:
        """Stage 3: Retrieve tools permitted for the user's workspace from ToolRegistry."""
        workspace_id = state.get("workspace_id")
        tools = await self.tool_registry.list_tools(workspace_id=workspace_id, enabled_only=True)

        permitted_tools: List[Dict[str, Any]] = []
        permitted_tool_ids: List[str] = []

        for t in tools:
            # Prevent indirect prompt injection by sanitizing tool metadata
            sanitized_desc = sanitize_tool_text(t.description)
            sanitized_name = sanitize_tool_text(t.name)
            tool_data = {
                "id": t.id,
                "provider": t.provider.value if hasattr(t.provider, "value") else str(t.provider),
                "name": sanitized_name,
                "description": sanitized_desc,
                "input_schema": t.input_schema.model_dump() if hasattr(t.input_schema, "model_dump") else t.input_schema,
                "risk_category": t.risk_category.value if hasattr(t.risk_category, "value") else str(t.risk_category),
                "connection_required": t.connection_required,
                "scopes": t.scopes,
            }
            permitted_tools.append(tool_data)
            permitted_tool_ids.append(t.id)

        return {
            "permitted_tools": permitted_tools,
            "permitted_tool_ids": permitted_tool_ids,
        }

    async def _node_generate_workflow(self, state: PipelineState) -> Dict[str, Any]:
        """Stage 4: Generate or revise a workflow definition referencing only permitted tools."""
        existing_wf = state.get("existing_workflow")
        if existing_wf:
            messages = build_workflow_revise_prompt(
                sanitized_user_prompt=state["sanitized_prompt"],
                existing_workflow=existing_wf,
                permitted_tools=state["permitted_tools"],
            )
        else:
            messages = build_workflow_prompt(
                sanitized_user_prompt=state["sanitized_prompt"],
                user_intent=state["user_intent"],
                permitted_tools=state["permitted_tools"],
            )

        wf_def: WorkflowDefinition = await self.llm_client.generate_structured(
            messages=messages,
            response_model=WorkflowDefinition,
        )

        return {
            "workflow_definition": wf_def.model_dump(),
        }

    async def _node_validate_workflow(self, state: PipelineState) -> Dict[str, Any]:
        """Stage 5: Validate the generated schema and tool authorizations."""
        wf_dict = state.get("workflow_definition")
        if not wf_dict:
            return {
                "status": "error",
                "validation_errors": ["Workflow definition was not generated"],
            }

        try:
            wf_obj = WorkflowDefinition.model_validate(wf_dict)
        except Exception as e:
            return {
                "status": "error",
                "validation_errors": [f"Pydantic validation failed: {str(e)}"],
            }

        permitted_tool_ids = set(state.get("permitted_tool_ids", []))
        errors = validate_workflow_definition(wf_obj, permitted_tool_ids)

        if errors:
            logger.warning("Workflow validation errors detected", count=len(errors), errors=errors)
            return {
                "status": "error",
                "validation_errors": errors,
            }

        return {
            "status": "valid",
            "validation_errors": [],
        }

    async def _node_explain_workflow(self, state: PipelineState) -> Dict[str, Any]:
        """Stage 6: Explain the generated workflow in human-readable terms."""
        wf_dict = state.get("workflow_definition")
        if not wf_dict:
            return {}

        messages = build_explanation_prompt(
            sanitized_user_prompt=state["sanitized_prompt"],
            workflow_dict=wf_dict,
        )

        explanation: WorkflowExplanation = await self.llm_client.generate_structured(
            messages=messages,
            response_model=WorkflowExplanation,
        )

        return {
            "explanation": explanation.model_dump(),
            "status": "complete",
        }

    # --- Public Execution Entry Point ---

    async def run(
        self,
        prompt: str,
        workspace_id: str,
        user_id: str,
        existing_workflow: Optional[Dict[str, Any]] = None,
    ) -> WorkflowGenerationResult:
        initial_state: PipelineState = {
            "raw_prompt": prompt,
            "sanitized_prompt": "",
            "workspace_id": workspace_id,
            "user_id": user_id,
            "detected_threats": [],
            "is_safe": True,
            "user_intent": "",
            "detected_apps": [],
            "clarification_needed": False,
            "clarifications": [],
            "permitted_tools": [],
            "permitted_tool_ids": [],
            "existing_workflow": existing_workflow,
            "workflow_definition": None,
            "validation_errors": [],
            "explanation": None,
            "status": "started",
            "model_metadata": self.llm_client.get_metadata(),
        }

        final_state: PipelineState = await self.graph.ainvoke(initial_state)

        status_result = final_state.get("status", "error")
        wf_dict = final_state.get("workflow_definition")
        wf_obj = WorkflowDefinition.model_validate(wf_dict) if wf_dict else None

        clarifications = [
            ClarificationQuestion.model_validate(c)
            for c in final_state.get("clarifications", [])
        ]

        exp_dict = final_state.get("explanation")
        exp_obj = WorkflowExplanation.model_validate(exp_dict) if exp_dict else None

        success = status_result == "complete" and wf_obj is not None

        return WorkflowGenerationResult(
            success=success,
            status=status_result,
            workflow=wf_obj,
            clarifications=clarifications,
            explanation=exp_obj,
            errors=final_state.get("validation_errors", []),
            raw_prompt=final_state.get("raw_prompt", prompt),
            sanitized_prompt=final_state.get("sanitized_prompt", ""),
            model_metadata=final_state.get("model_metadata", {}),
        )
