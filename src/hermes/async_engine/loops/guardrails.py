"""Guardrails — input / output / tool-call validation and sanitization.

Stateless safety layer that sits between the user and the agentic loops:

    User → InputGuardrail → [loops] → OutputGuardrail → User
                                    ↕
                          ToolCallGuardrail (inside ToolExecutor)

Each guardrail returns a GuardrailResult(passed, reason). Failures are
non-fatal to the platform — they produce a clear rejection/escalation
instead of propagating bad data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Prompt-injection / jailbreak patterns (defense-in-depth with ToolExecutor.DENY_PATTERN).
INJECTION_PATTERNS = [
    re.compile(r"ignore (all |previous |above )?instructions", re.IGNORECASE),
    re.compile(r"you are now", re.IGNORECASE),
    re.compile(r"system prompt", re.IGNORECASE),
    re.compile(r"<\s*/\s*instruction", re.IGNORECASE),
    re.compile(r"DAN\b|jailbreak|do anything now", re.IGNORECASE),
]

# Secrets that must never appear in agent output.
SECRET_PATTERNS = [
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\b(sk|rk)-[a-zA-Z0-9]{32,48}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bghp_[a-zA-Z0-9]{36}\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"(?i)Bearer\s+[a-zA-Z0-9\-._~+/]+=*"), "Bearer [REDACTED_TOKEN]"),
]


@dataclass
class GuardrailResult:
    passed: bool
    reason: str = ""


# --------------------------------------------------------------------------- #
# Input guardrail
# --------------------------------------------------------------------------- #
class InputGuardrail:
    def __init__(self, max_chars: int = 8000,
                 allowed_task_types: set[str] | None = None,
                 injection_patterns: list[re.Pattern] | None = None):
        self.max_chars = max_chars
        self.allowed_task_types = allowed_task_types
        self.injection_patterns = injection_patterns or INJECTION_PATTERNS

    def evaluate(self, request: str) -> GuardrailResult:
        if not isinstance(request, str) or not request.strip():
            return GuardrailResult(False, "malformed: empty or non-string request")
        if len(request) > self.max_chars:
            return GuardrailResult(False, f"malformed: input exceeds {self.max_chars} chars")
        for pat in self.injection_patterns:
            if pat.search(request):
                return GuardrailResult(False, f"injection detected: pattern '{pat.pattern[:40]}'")
        return GuardrailResult(True)

    def sanitize(self, request: str) -> str:
        return request.strip()


# --------------------------------------------------------------------------- #
# Output guardrail
# --------------------------------------------------------------------------- #
class OutputGuardrail:
    def __init__(self, max_length: int = 100_000,
                 secret_patterns: list[tuple[re.Pattern, str]] | None = None):
        self.max_length = max_length
        self.secret_patterns = secret_patterns or SECRET_PATTERNS

    def evaluate(self, result: str) -> GuardrailResult:
        if not isinstance(result, str):
            return GuardrailResult(False, "schema: output is not a string")
        if len(result) > self.max_length:
            return GuardrailResult(False, f"schema: output exceeds {self.max_length} chars")
        return GuardrailResult(True)

    def sanitize(self, result: str) -> str:
        for pat, repl in self.secret_patterns:
            result = pat.sub(repl, result)
        return result


# --------------------------------------------------------------------------- #
# Tool-call guardrail
# --------------------------------------------------------------------------- #
class ToolCallGuardrail:
    def __init__(self, max_args_size: int = 4000,
                 injection_patterns: list[re.Pattern] | None = None):
        self.max_args_size = max_args_size
        self.injection_patterns = injection_patterns or INJECTION_PATTERNS

    def evaluate(self, tool_name: str, args: dict) -> GuardrailResult:
        if not tool_name or not isinstance(tool_name, str):
            return GuardrailResult(False, "malformed: empty tool name")
        blob = tool_name + " " + " ".join(str(v) for v in args.values())
        if len(blob) > self.max_args_size:
            return GuardrailResult(False, f"malformed: tool args exceed {self.max_args_size} chars")
        for pat in self.injection_patterns:
            if pat.search(blob):
                return GuardrailResult(False, f"injection in tool call: '{pat.pattern[:40]}'")
        return GuardrailResult(True)