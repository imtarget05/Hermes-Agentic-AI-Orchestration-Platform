"""Harness Engineering package — guardrails, eval, and observability.

Surface for the Harness brief (Context / Tools & Guardrails / Evaluation &
Observability). Deterministic, offline-first, mirrors the repo's grounded-price
policy so all tests and local runs behave consistently.
"""
from .eval import (
    EvaluationRegistry,
    HarnessEvaluator,
    lifecycle_success_rate,
)
from .guardrails import OutputGuardrail, check_output
from .schemas import EvalMetric, GuardrailViolation, OutputVerdict

__all__ = [
    "EvalMetric",
    "EvaluationRegistry",
    "GuardrailViolation",
    "HarnessEvaluator",
    "OutputGuardrail",
    "OutputVerdict",
    "check_output",
    "lifecycle_success_rate",
]