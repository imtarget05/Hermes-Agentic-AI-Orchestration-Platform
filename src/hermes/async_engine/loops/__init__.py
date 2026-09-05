"""Hermes 8-loop agentic architecture (see README_P1.md §Architecture).

1 Context → 2 Planning → 3 Dispatch (RabbitMQ) → 4 Execute → 5 Verify
→ 6 Reliability → 7 Evaluate → 8 Learn/Audit (feedback into 1 & 2)
"""
from .audit import LearningLoop
from .context import ContextBuilder, ExecutionContext
from .evaluate import rolling_report, workflow_report
from .pipeline import run_agent_workflow
from .planner import DEFAULT_MAX_ATTEMPTS, VALID_TASK_TYPES, Planner
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
    "run_agent_workflow",
]