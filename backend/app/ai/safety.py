"""Prompt Injection and Safety Sanitization for AI Workflow Generation.

Enforces:
1. Rules.md #4: "Treat emails, documents, issue descriptions, web pages,
   tool results, and other external content as untrusted data."
2. "Never allow prompt content or tool output to override system policy
   or grant additional permissions."
3. Sanitizes user prompts against known prompt injection delimiters,
   system prompt overrides, instruction escapes, and privilege escalation patterns.
4. Sanitizes tool descriptions to prevent indirect prompt injection.
"""

from __future__ import annotations

import re
from typing import List, Tuple


# Patterns commonly used in jailbreaks, system overrides, and prompt injection attacks
INJECTION_PATTERNS = [
    # System instruction overrides
    r"(?i)ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules|commands)",
    r"(?i)disregard\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)",
    r"(?i)forget\s+(all\s+)?(previous|prior)\s+(instructions|prompts|rules)",
    r"(?i)new\s+system\s+instruction",
    r"(?i)system\s+prompt\s+override",
    r"(?i)you\s+are\s+now\s+(an?\s+)?unrestricted",
    r"(?i)jailbreak",
    r"(?i)dan\s+mode",
    # Privilege escalation / administrative override attempts
    r"(?i)grant\s+(all\s+)?(permissions|admin|root|access)",
    r"(?i)bypass\s+(safety|security|policy|restrictions|validation)",
    r"(?i)disable\s+(safety|security|policy|guardrails|validation)",
    # Code execution attacks (Rules #4: never execute arbitrary code)
    r"(?i)(execute|run)\s+(arbitrary\s+)?(python|shell|bash|code|command|eval|exec)\b",
    r"(?i)subprocess\.(run|popen|call)",
    r"(?i)os\.system",
    # Delimiter hijacking
    r"```system",
    r"\[SYSTEM\]",
    r"\[INSTRUCTION\]",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"<system>",
    r"</system>",
]

COMPILED_INJECTION_PATTERNS = [re.compile(p) for p in INJECTION_PATTERNS]


class PromptSanitizationResult:
    def __init__(self, sanitized_text: str, detected_threats: List[str], is_safe: bool):
        self.sanitized_text = sanitized_text
        self.detected_threats = detected_threats
        self.is_safe = is_safe


def sanitize_user_prompt(prompt: str) -> PromptSanitizationResult:
    """Sanitize and inspect a user prompt for injection attempts.

    Returns a PromptSanitizationResult indicating whether threats were found,
    what was detected, and a sanitized version of the prompt.
    """
    if not prompt or not prompt.strip():
        return PromptSanitizationResult(sanitized_text="", detected_threats=[], is_safe=True)

    detected: List[str] = []
    sanitized = prompt

    for pattern in COMPILED_INJECTION_PATTERNS:
        matches = pattern.findall(sanitized)
        if matches:
            detected.append(pattern.pattern)
            # Neutralize the match in sanitized text
            sanitized = pattern.sub("[BLOCKED_INJECTION_ATTEMPT]", sanitized)

    # Strip dangerous XML/control tags that might mimic LLM structural framing
    sanitized = re.sub(r"<\/?(system|instruction|admin|role|override)>", "", sanitized, flags=re.IGNORECASE)

    is_safe = len(detected) == 0
    return PromptSanitizationResult(
        sanitized_text=sanitized.strip(),
        detected_threats=detected,
        is_safe=is_safe,
    )


def sanitize_tool_text(tool_description: str) -> str:
    """Sanitize external tool metadata (name/description) to prevent indirect prompt injection.

    Tool descriptions from third-party MCP servers or remote integrations
    must be treated as untrusted data.
    """
    if not tool_description:
        return ""

    sanitized = tool_description
    for pattern in COMPILED_INJECTION_PATTERNS:
        sanitized = pattern.sub("[FILTERED]", sanitized)

    # Remove fake instructions or delimiters
    sanitized = re.sub(r"```.*```", "[CODE_REMOVED]", sanitized)
    sanitized = re.sub(r"<\/?(system|instruction|admin)>", "", sanitized, flags=re.IGNORECASE)

    return sanitized.strip()
