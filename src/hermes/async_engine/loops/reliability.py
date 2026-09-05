"""Loop 6 — RELIABILITY LOOP.

Retry/backoff/DLQ live in retry.py + the worker; this module adds the missing
reliability primitives:

- TaskTimeout        per-task deadline enforcement around handler execution
- CircuitBreaker     per-task-type failure isolation (closed/open/half-open)
- escalation         terminal failures escalate (event + metadata) for review
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable


class TaskTimeoutError(RuntimeError):
    """Handler exceeded its deadline — retryable (transient stall)."""

    def __init__(self, task_type: str, timeout: float):
        super().__init__(f"timeout: {task_type} exceeded {timeout}s deadline")


class CircuitOpenError(RuntimeError):
    """Circuit breaker open for this task type — fail fast, retry later."""

    def __init__(self, task_type: str):
        super().__init__(f"circuit open for {task_type} — temporarily unavailable")


class CircuitBreaker:
    """Per-task-type breaker. threshold failures -> open for cooldown_seconds,
    then half-open (single probe). Success closes, failure re-opens."""

    def __init__(self, threshold: int = 5, cooldown_seconds: float = 10.0):
        self.threshold = threshold
        self.cooldown = cooldown_seconds
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._probing: set[str] = set()

    def allow(self, task_type: str) -> bool:
        if task_type not in self._opened_at:
            return True
        if time.time() - self._opened_at[task_type] < self.cooldown:
            return False  # open
        self._probing.add(task_type)  # half-open: allow one probe
        return True

    def record_success(self, task_type: str) -> None:
        self._failures.pop(task_type, None)
        self._opened_at.pop(task_type, None)
        self._probing.discard(task_type)

    def record_failure(self, task_type: str) -> None:
        n = self._failures.get(task_type, 0) + 1
        self._failures[task_type] = n
        if task_type in self._probing or n >= self.threshold:
            self._opened_at[task_type] = time.time()
            self._probing.discard(task_type)

    def state(self, task_type: str) -> str:
        if task_type not in self._opened_at:
            return "closed"
        if time.time() - self._opened_at[task_type] < self.cooldown:
            return "open"
        return "half-open"


def run_with_timeout(fn: Callable[[], Any], timeout_seconds: float,
                     task_type: str = "") -> Any:
    """Execute fn with a hard deadline (loop 6). Runs in a worker thread; on
    timeout the future is abandoned and TaskTimeoutError raised (the task is
    retried with backoff — never blocks the queue)."""
    if timeout_seconds <= 0:
        return fn()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeout:
            future.cancel()
            raise TaskTimeoutError(task_type or "task", timeout_seconds)


def escalation_metadata(task, error: str) -> dict[str, Any]:
    """Terminal failure after exhausting retries -> escalate for review."""
    return {
        "escalation": "manual_review",
        "escalated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "attempts_used": task.attempt,
        "reason": error[:200],
    }
