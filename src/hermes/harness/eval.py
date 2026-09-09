"""Evaluation & Observability — compute reliability/accuracy metrics.

Consumes the existing TaskStore (`src/hermes/tasks/store.py`) plus per-domain
call metadata. Mirrors the "Evaluation & Observability" brief: success rate,
accuracy, latency, cost, and error traces — all derived deterministically.
"""
from __future__ import annotations

import time

from .schemas import EvalMetric


class EvaluationRegistry:
    """Per-domain call recording that later feeds HarnessEvaluator."""

    def __init__(self) -> None:
        self._samples: list[dict] = []

    def record(self, *, domain: str = "general", callable_name: str = "",
               success: bool = True, accuracy: float = 0.0,
               latency_ms: float = 0.0, cost_tokens: int = 0,
               error_trace: str = "", task_id: str = "") -> dict:
        sample = {
            "domain": domain,
            "callable": callable_name,
            "success": success,
            "accuracy": accuracy,
            "latency_ms": latency_ms,
            "cost_tokens": cost_tokens,
            "error_trace": error_trace,
            "task_id": task_id,
        }
        self._samples.append(sample)
        return sample

    def records(self) -> list[dict]:
        return list(self._samples)


class HarnessEvaluator:
    """Aggregates task lifecycle + explicit samples into EvalMetric rows."""

    def __init__(self, registry: EvaluationRegistry | None = None,
                 store=None) -> None:
        self.registry = registry or EvaluationRegistry()
        self.store = store  # optional TaskStore for lifecycle-derived metrics

    def compute_metrics(self, task_ids: list[str] | None = None) -> list[EvalMetric]:
        """Build per-domain EvalMetric rows from registry samples."""
        rows: list[EvalMetric] = []
        by_domain: dict[str, list[dict]] = {}
        for s in self._samples_for(task_ids):
            by_domain.setdefault(s["domain"], []).append(s)
        for domain, samples in by_domain.items():
            total = len(samples)
            ok = sum(1 for s in samples if s["success"])
            lat = _avg(s["latency_ms"] for s in samples)
            toks = sum(s["cost_tokens"] for s in samples)
            acc = sum(s["accuracy"] for s in samples) / total if total else 0.0
            trace = next((s["error_trace"] for s in samples
                          if not s["success"] and s["error_trace"]), "")
            rows.append(EvalMetric(
                domain=domain, success=ok == total,
                success_rate=ok / total if total else 0.0,
                accuracy=acc, latency_ms=lat, cost_tokens=toks,
                error_trace=trace))
        return rows

    def _samples_for(self, task_ids: list[str] | None) -> list[dict]:
        if task_ids is None:
            return self.registry.records()
        allow = set(task_ids)
        return [s for s in self.registry.records()
                if not s["task_id"] or s["task_id"] in allow]

    def success_rates(self) -> dict[str, float]:
        """Compute success rate per domain from probes."""
        return {m.domain: m.success_rate for m in self.compute_metrics()}


def _avg(it):
    vals = list(it)
    return (sum(vals) / len(vals)) if vals else 0.0


# ---- lifecycle-derived metrics (uses TaskStore, optional) -----------------

def lifecycle_success_rate(store, task_ids: list[str] | None = None) -> float:
    """Fraction of completed tasks over all terminal tasks in store."""
    if store is None:
        return 0.0
    tasks = store.list_tasks(limit=10000)
    if task_ids:
        allow = set(task_ids)
        tasks = [t for t in tasks if t["id"] in allow]
    completed = [t for t in tasks if t["status"] == "completed"]
    terminal = [t for t in tasks if t["status"] in ("completed", "failed")]
    return (len(completed) / len(terminal)) if terminal else 0.0