"""Loop 5 — VERIFICATION LOOP.

Validate worker output before it is accepted: schema check, evidence check,
quality check. PASS -> aggregate; FAIL -> retry (quality-retryable) or dead-letter.

Wired into the worker between EXECUTE and COMPLETED.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class VerificationError(RuntimeError):
    """Raised when worker output fails verification (loop 5 -> loop 6)."""

    def __init__(self, reason: str, retryable: bool = True):
        super().__init__(reason)
        self.retryable = retryable


@dataclass
class VerificationResult:
    passed: bool
    reason: str = ""
    retryable: bool = True


Validator = Callable[[Any], str]  # returns "" if OK, else failure reason


def schema_check(result: Any) -> str:
    """Result must be a non-empty string (canonical worker output contract)."""
    if not isinstance(result, str):
        return "schema error: result is not a string"
    if not result.strip():
        return "schema error: empty result"
    return ""


def evidence_check(result: str) -> str:
    """Result must look like evidence (a URI/marker the aggregator can store)."""
    if len(result) < 4:
        return "evidence check: result too short to be evidence"
    return ""


def contains_check(needle: str) -> Validator:
    def _check(result: str) -> str:
        return "" if needle.lower() in result.lower() else f"quality check: missing '{needle}'"
    return _check


# Universal contract for every worker output: a non-empty string.
# Domain-specific evidence/quality checks are opt-in via Verifier.by_task_type
# (e.g. require a "DONE" marker on report tasks).
DEFAULT_VALIDATORS: list[Validator] = [schema_check]


class Verifier:
    """Runs a validator chain; first failure wins (fail fast, fail loudly)."""

    def __init__(self, validators: list[Validator] | None = None,
                 by_task_type: dict[str, list[Validator]] | None = None,
                 max_length: int = 100_000):
        self.validators = validators if validators is not None else list(DEFAULT_VALIDATORS)
        self.by_task_type = by_task_type or {}
        self.max_length = max_length

    def verify(self, task, result: Any) -> VerificationResult:
        if isinstance(result, str) and len(result) > self.max_length:
            return VerificationResult(False, "schema error: result exceeds max length")
        for v in self.validators + self.by_task_type.get(task.task_type, []):
            reason = v(result)
            if reason:
                return VerificationResult(False, reason, retryable="quality" in reason)
        return VerificationResult(True)
