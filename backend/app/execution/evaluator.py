"""Data Mapping and Step Condition Evaluator for Workflow Execution.

Deterministic evaluation of input arguments, dynamic references, and step conditions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def resolve_json_path(data: Any, path: str) -> Any:
    """Resolve a dot-separated or bracketed path from a nested dictionary.

    Examples:
        - "trigger.data.issue.title" -> data["trigger"]["data"]["issue"]["title"]
        - "steps.step_1.output.id" -> data["steps"]["step_1"]["output"]["id"]
    """
    if not path or not isinstance(data, dict):
        return None

    tokens = path.split(".")
    current = data
    for token in tokens:
        if isinstance(current, dict):
            if token in current:
                current = current[token]
            else:
                return None
        elif isinstance(current, list):
            try:
                idx = int(token)
                if 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None
            except ValueError:
                return None
        else:
            return None
    return current


def resolve_step_arguments(
    static_arguments: Dict[str, Any],
    data_mappings: List[Dict[str, Any]],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Combine static arguments with dynamic data mappings evaluated against context.

    Context contains:
    - "trigger": {"event_id": ..., "data": ...}
    - "steps": {"step_1": {"output": ..., "status": ...}}
    """
    resolved = dict(static_arguments)

    for mapping in data_mappings:
        target_field = mapping.get("target_field")
        source = mapping.get("source", "")
        if not target_field:
            continue

        if source == "literal":
            resolved[target_field] = mapping.get("value")
        else:
            val = resolve_json_path(context, source)
            resolved[target_field] = val if val is not None else mapping.get("value")

    return resolved


def evaluate_step_conditions(
    conditions: List[Dict[str, Any]],
    context: Dict[str, Any],
) -> Tuple[bool, Optional[str]]:
    """Evaluate whether all conditions for a step are satisfied.

    Returns:
        (is_met, failure_reason)
    """
    for idx, cond in enumerate(conditions):
        field_path = cond.get("field", "")
        op = cond.get("operator", "eq").lower()
        expected = cond.get("value")

        actual = resolve_json_path(context, field_path)

        if op == "eq":
            if actual != expected:
                return False, f"Condition {idx+1} failed: {field_path} ({actual}) != {expected}"
        elif op == "neq":
            if actual == expected:
                return False, f"Condition {idx+1} failed: {field_path} ({actual}) == {expected}"
        elif op == "contains":
            if actual is None:
                return False, f"Condition {idx+1} failed: {field_path} is None"
            if isinstance(actual, (str, list, dict)):
                if expected not in actual:
                    return False, f"Condition {idx+1} failed: '{expected}' not in {field_path}"
            else:
                return False, f"Condition {idx+1} failed: {field_path} does not support contains"
        elif op == "in":
            if expected is None or not isinstance(expected, (list, tuple, set, str)):
                return False, f"Condition {idx+1} failed: invalid 'in' target {expected}"
            if actual not in expected:
                return False, f"Condition {idx+1} failed: {actual} not in {expected}"
        elif op == "gt":
            if actual is None or expected is None or actual <= expected:
                return False, f"Condition {idx+1} failed: {actual} <= {expected}"
        elif op == "lt":
            if actual is None or expected is None or actual >= expected:
                return False, f"Condition {idx+1} failed: {actual} >= {expected}"
        elif op == "is_null":
            if actual is not None:
                return False, f"Condition {idx+1} failed: {field_path} is not null"
        elif op == "not_null":
            if actual is None:
                return False, f"Condition {idx+1} failed: {field_path} is null"
        else:
            return False, f"Condition {idx+1} failed: unknown operator '{op}'"

    return True, None
