"""Deterministic Validation and Security Policy Engine for Workflows.

Performs strict server-side validation on generated workflow definitions:
1. JSON Schema & Pydantic conformance.
2. Server-side Tool Registry authorization check (AI may only reference permitted tools).
3. Dependency graph acyclicity and reference integrity (DAG validation).
4. Data mapping validity (source paths must reference valid triggers/steps).
5. Placeholder / fabricated identifier detection.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set
import jsonschema

from ..models.workflow import WorkflowDefinition, get_workflow_json_schema


class WorkflowValidationError(Exception):
    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def validate_workflow_schema(workflow_dict: Dict[str, Any]) -> List[str]:
    """Validate raw workflow dictionary against official JSON Schema."""
    errors: List[str] = []
    schema = get_workflow_json_schema()
    validator = jsonschema.Draft202012Validator(schema)
    for err in validator.iter_errors(workflow_dict):
        path = ".".join(str(p) for p in err.path) if err.path else "root"
        errors.append(f"Schema error at '{path}': {err.message}")
    return errors


def validate_workflow_dag(workflow: WorkflowDefinition) -> List[str]:
    """Validate that workflow steps form a valid Directed Acyclic Graph (DAG)."""
    errors: List[str] = []
    step_ids: Set[str] = set()

    for step in workflow.steps:
        if step.id in step_ids:
            errors.append(f"Duplicate step ID '{step.id}' found in workflow")
        step_ids.add(step.id)

    # Check dependencies point to existing step IDs and don't self-reference
    for step in workflow.steps:
        for dep in step.dependencies:
            if dep == step.id:
                errors.append(f"Step '{step.id}' cannot depend on itself")
            elif dep not in step_ids:
                errors.append(f"Step '{step.id}' has unresolved dependency '{dep}'")

    # Cycle detection via DFS
    visited: Dict[str, int] = {}  # 0 = unvisited, 1 = visiting, 2 = visited

    def has_cycle(node_id: str) -> bool:
        visited[node_id] = 1
        node_step = next((s for s in workflow.steps if s.id == node_id), None)
        if node_step:
            for dep in node_step.dependencies:
                if dep in step_ids:
                    if visited.get(dep, 0) == 1:
                        return True
                    if visited.get(dep, 0) == 0:
                        if has_cycle(dep):
                            return True
        visited[node_id] = 2
        return False

    for s_id in step_ids:
        if visited.get(s_id, 0) == 0:
            if has_cycle(s_id):
                errors.append("Cyclic dependency detected in workflow steps")
                break

    # Unreachable nodes detection
    if workflow.steps:
        entry_steps = {s.id for s in workflow.steps if not s.dependencies}
        if not entry_steps:
            errors.append("Unreachable nodes detected: workflow has no entry step with empty dependencies")
        else:
            reachable: Set[str] = set(entry_steps)
            changed = True
            while changed:
                changed = False
                for step in workflow.steps:
                    if step.id not in reachable:
                        if step.dependencies and all(dep in reachable for dep in step.dependencies):
                            reachable.add(step.id)
                            changed = True

            unreachable = step_ids - reachable
            if unreachable:
                for u_id in sorted(unreachable):
                    errors.append(f"Unreachable step '{u_id}' detected: dependencies cannot be satisfied from workflow entry points")

    return errors


def validate_workflow_tools(
    workflow: WorkflowDefinition,
    permitted_tool_ids: Set[str],
) -> List[str]:
    """Validate that all tools referenced by workflow steps are in the permitted tool registry.

    Enforces Rules.md: 'The AI may reference only tools available in the
    server-side tool registry and permitted for the current user/workspace.'
    """
    errors: List[str] = []
    for step in workflow.steps:
        if step.tool_id not in permitted_tool_ids:
            errors.append(
                f"Unauthorized or unregistered tool '{step.tool_id}' in step '{step.id}'. "
                f"Must be one of permitted tools: {sorted(list(permitted_tool_ids)) or 'None'}"
            )
    return errors


def validate_data_mappings(workflow: WorkflowDefinition) -> List[str]:
    """Validate data mappings point to known triggers, steps, or literals."""
    errors: List[str] = []
    step_ids = {s.id for s in workflow.steps}

    for step in workflow.steps:
        for mapping in step.data_mappings:
            source = mapping.source.strip()
            if source == "literal":
                if mapping.value is None:
                    errors.append(
                        f"Step '{step.id}' has literal data mapping for '{mapping.target_field}' with null value"
                    )
            elif source.startswith("steps."):
                parts = source.split(".")
                dep_step_id = parts[1]
                if dep_step_id not in step_ids:
                    errors.append(
                        f"Step '{step.id}' references non-existent step '{dep_step_id}' in mapping '{source}'"
                    )
                elif dep_step_id not in step.dependencies:
                    # Enforce dependency declaration for mapped steps
                    errors.append(
                        f"Step '{step.id}' maps data from '{dep_step_id}' but does not declare it in dependencies"
                    )
            elif source.startswith("trigger."):
                # Valid trigger source
                pass
            else:
                errors.append(
                    f"Step '{step.id}' has unrecognized source format '{source}' for target '{mapping.target_field}'"
                )
    return errors


def validate_placeholder_values(workflow: WorkflowDefinition) -> List[str]:
    """Ensure no invented placeholders like '<CHANNEL_ID>' were used instead of asking for clarification."""
    errors: List[str] = []
    placeholder_patterns = ["<insert", "<replace", "<todo", "<placeholder", "YOUR_", "TODO_", "EXAMPLE_"]

    for step in workflow.steps:
        for k, v in step.arguments.items():
            if isinstance(v, str):
                for p in placeholder_patterns:
                    if p.lower() in v.lower():
                        errors.append(
                            f"Step '{step.id}' argument '{k}' contains placeholder value '{v}'. "
                            f"Missing information must trigger a clarification request instead of placeholders."
                        )
    return errors


def validate_tool_arguments(
    workflow: WorkflowDefinition,
    tool_descriptors: Dict[str, Any],
) -> List[str]:
    """Validate step arguments against declared tool input schemas if available."""
    errors: List[str] = []
    for step in workflow.steps:
        desc = tool_descriptors.get(step.tool_id)
        if desc and hasattr(desc, "input_schema") and desc.input_schema:
            schema = desc.input_schema
            required_props = getattr(schema, "required", []) or []
            mapped_fields = {m.target_field for m in step.data_mappings}
            provided_fields = set(step.arguments.keys()) | mapped_fields

            for req in required_props:
                if req not in provided_fields:
                    errors.append(
                        f"Step '{step.id}' using tool '{step.tool_id}' is missing required argument '{req}'"
                    )
    return errors


def validate_workflow_definition(
    workflow: WorkflowDefinition,
    permitted_tool_ids: Set[str],
    tool_descriptors: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Comprehensive validation running all checks."""
    errors: List[str] = []
    errors.extend(validate_workflow_schema(workflow.model_dump()))
    errors.extend(validate_workflow_dag(workflow))
    errors.extend(validate_workflow_tools(workflow, permitted_tool_ids))
    errors.extend(validate_data_mappings(workflow))
    errors.extend(validate_placeholder_values(workflow))
    if tool_descriptors:
        errors.extend(validate_tool_arguments(workflow, tool_descriptors))
    return errors
