"""Harness Engineering schemas — evaluation metrics & guardrail results.

Covers the observability/eval surface from the Harness Engineering brief:
Context accuracy, success rate, latency, cost, error/trace.
"""
from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class EvalMetric(BaseModel):
    """One evaluation sample tied to a task id (or a synthetic evaluation row)."""
    task_id: str = ""
    domain: str = "general"  # procurement | knowledge | advisor | ops | competitor | harness
    success: bool = True
    success_rate: float = 0.0      # 0..1 for the aggregate bucket this sample spans
    accuracy: float = 0.0          # 0..1 attributed/grounded-answer accuracy
    latency_ms: float = 0.0
    cost_tokens: int = 0
    error_trace: str = ""          # short error/rejection message when not successful
    evaluated_at: str = Field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return self.model_dump()


class GuardrailViolation(BaseModel):
    """A single rule failure produced by an output guardrail."""
    rule: str = ""
    message: str = ""
    offending_text: str = ""
    severity: str = "warn"  # warn | error


class OutputVerdict(BaseModel):
    """Enforcement result for one domain output."""
    domain: str = ""
    passed: bool = True
    violations: list[GuardrailViolation] = Field(default_factory=list)
    checked_at: str = Field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return self.model_dump()