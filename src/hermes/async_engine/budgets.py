"""Loop 6 extension — hard budgets (HERMES-01) + cost control (HERMES-09).

The supervisor/orchestrator must never spawn unbounded work. Every entry
point that creates tasks goes through `validate_graph_budget`, and every
worker execution goes through a shared `CostTracker` so a runaway agent hits
a hard stop (non-retryable) instead of looping forever.

Limits are env-overridable (see `.env.example`):

    HERMES_MAX_TASKS_PER_WORKFLOW   (default 50)
    HERMES_MAX_AGENTS               (default 8)   # distinct agent types per workflow
    HERMES_MAX_ITERATIONS           (default 5)   # max_attempts cap per task
    HERMES_MAX_TOKEN_BUDGET         (default 200_000)
    HERMES_MAX_TIME_BUDGET_SECONDS  (default 300)
    HERMES_MAX_COST_USD             (default 10.0)

Exceeding any limit raises `BudgetExceededError`, a NON-retryable failure:
the task is dead-lettered, never retried in a loop.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

from .retry import NonRetryableError


class BudgetExceededError(NonRetryableError):
    """A hard budget limit was hit — fail fast, never retry."""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class BudgetLimits:
    max_tasks: int = field(default_factory=lambda: _env_int("HERMES_MAX_TASKS_PER_WORKFLOW", 50))
    max_agents: int = field(default_factory=lambda: _env_int("HERMES_MAX_AGENTS", 8))
    max_iterations: int = field(default_factory=lambda: _env_int("HERMES_MAX_ITERATIONS", 5))
    max_tokens: float = field(default_factory=lambda: _env_float("HERMES_MAX_TOKEN_BUDGET", 200_000))
    max_time_seconds: float = field(default_factory=lambda: _env_float("HERMES_MAX_TIME_BUDGET_SECONDS", 300))
    max_cost_usd: float = field(default_factory=lambda: _env_float("HERMES_MAX_COST_USD", 10.0))


DEFAULT_LIMITS = BudgetLimits()


def validate_graph_budget(graph: list[dict], limits: BudgetLimits | None = None) -> None:
    """Reject a task graph that would exceed spawn limits (HERMES-01).

    Called by the orchestrator before any task row is persisted, so an
    oversized or recursive plan never reaches the queue.
    """
    lim = limits or DEFAULT_LIMITS
    if not isinstance(graph, list):
        raise BudgetExceededError("budget: graph must be a list of nodes")
    if len(graph) > lim.max_tasks:
        raise BudgetExceededError(
            f"budget: graph has {len(graph)} tasks > MAX_TASKS_PER_WORKFLOW={lim.max_tasks}")
    agent_types = {n.get("task_type") for n in graph if isinstance(n, dict)}
    if len(agent_types) > lim.max_agents:
        raise BudgetExceededError(
            f"budget: {len(agent_types)} distinct agent types > MAX_AGENTS={lim.max_agents}")
    for n in graph:
        if isinstance(n, dict) and int(n.get("max_attempts", 1) or 1) > lim.max_iterations:
            raise BudgetExceededError(
                f"budget: task {n.get('task_id')} requests {n.get('max_attempts')} attempts "
                f"> MAX_ITERATIONS={lim.max_iterations}")


class CostTracker:
    """Thread-safe token/cost/time budget per workflow (HERMES-09).

    Workers record usage via `record()`; every execution checks `exceeded()`.
    When a limit is hit, `exceeded()` returns a reason string and the worker
    raises `BudgetExceededError` (non-retryable -> dead-letter + escalation):
    a deterministic STOP, never an infinite spend loop.
    """

    def __init__(self, limits: BudgetLimits | None = None, workflow_id: str = ""):
        self.limits = limits or DEFAULT_LIMITS
        self.workflow_id = workflow_id
        self._lock = threading.Lock()
        self.tokens = 0.0
        self.cost_usd = 0.0
        self.started_at = time.time()
        self._stop_reason: str = ""

    def record(self, task_id: str = "", tokens: float = 0.0, cost_usd: float = 0.0) -> None:
        with self._lock:
            self.tokens += float(tokens or 0)
            self.cost_usd += float(cost_usd or 0)

    def elapsed_seconds(self) -> float:
        return time.time() - self.started_at

    def exceeded(self) -> str:
        """Return the exceeded-budget reason, or '' if within budget."""
        if self._stop_reason:
            return self._stop_reason
        lim = self.limits
        with self._lock:
            if self.tokens >= lim.max_tokens:
                self._stop_reason = (
                    f"budget: token budget {lim.max_tokens} reached "
                    f"(used {int(self.tokens)}) — STOP")
            elif self.cost_usd >= lim.max_cost_usd:
                self._stop_reason = (
                    f"budget: cost budget ${lim.max_cost_usd} reached "
                    f"(used ${self.cost_usd:.4f}) — STOP")
        if not self._stop_reason and self.elapsed_seconds() >= lim.max_time_seconds:
            self._stop_reason = (
                f"budget: time budget {lim.max_time_seconds}s reached "
                f"({self.elapsed_seconds():.1f}s elapsed) — STOP")
        return self._stop_reason

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "workflow_id": self.workflow_id,
                "tokens_used": int(self.tokens),
                "token_budget": self.limits.max_tokens,
                "cost_usd": round(self.cost_usd, 6),
                "cost_budget_usd": self.limits.max_cost_usd,
                "elapsed_seconds": round(self.elapsed_seconds(), 3),
                "time_budget_seconds": self.limits.max_time_seconds,
                "exceeded": bool(self._stop_reason),
                "stop_reason": self._stop_reason,
            }
