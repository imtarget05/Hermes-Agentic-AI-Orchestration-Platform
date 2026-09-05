"""Hermes 8-loop agentic architecture (see README_P1.md §Architecture).

1 Context → 2 Planning → 3 Dispatch (RabbitMQ) → 4 Execute → 5 Verify
→ 6 Reliability → 7 Evaluate → 8 Learn/Audit (feedback into 1 & 2)

+ Guardrails (input/output/tool-call) + Policy Engine + HITL + Model Gateway
"""
from .audit import LearningLoop
from .context import ContextBuilder, ExecutionContext
from .evaluate import rolling_report, workflow_report
from .guardrails import (
    GuardrailResult,
    InputGuardrail,
    OutputGuardrail,
    ToolCallGuardrail,
)
from .hitl import ApprovalStore, HumanInTheLoop
from .pipeline import run_agent_workflow
from .planner import DEFAULT_MAX_ATTEMPTS, VALID_TASK_TYPES, Planner
from .policy import (
    Decision,
    PolicyDecision,
    PolicyDeniedError,
    PolicyEngine,
    PolicyRequiredError,
    RiskLevel,
    policy_aware_call,
)
from .reliability import (
    CircuitBreaker,
    CircuitOpenError,
    TaskTimeoutError,
    escalation_metadata,
    run_with_timeout,
)
from .verify import DEFAULT_VALIDATORS, VerificationError, VerificationResult, Verifier

__all__ = [
    "ContextBuilder", "ExecutionContext",
    "Planner", "DEFAULT_MAX_ATTEMPTS", "VALID_TASK_TYPES",
    "Verifier", "VerificationError", "VerificationResult", "DEFAULT_VALIDATORS",
    "CircuitBreaker", "CircuitOpenError", "TaskTimeoutError",
    "run_with_timeout", "escalation_metadata",
    "workflow_report", "rolling_report",
    "LearningLoop",
    "InputGuardrail", "OutputGuardrail", "ToolCallGuardrail", "GuardrailResult",
    "PolicyEngine", "PolicyDecision", "PolicyRequiredError", "PolicyDeniedError",
    "RiskLevel", "Decision", "policy_aware_call",
    "HumanInTheLoop", "ApprovalStore",
    "run_agent_workflow",
]