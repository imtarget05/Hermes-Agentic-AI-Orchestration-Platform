"""Retry policy — which failures are retryable, and the backoff schedule.

Retryable (transient):           Non-retryable (permanent):
- timeout                        - invalid payload
- connection reset               - invalid task type
- temporary provider unavailable - authentication failure
- HTTP 429 / 502 / 503 / 504     - schema error
"""
from __future__ import annotations

import time

_RETRYABLE_MARKERS = (
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "temporarily unavailable",
    "temporary provider unavailable",
    "429",
    "502",
    "503",
    "504",
    "rate limit",
    "overloaded",
)

_NON_RETRYABLE_MARKERS = (
    "invalid payload",
    "invalid task type",
    "unknown task_type",
    "authentication failed",
    "auth failure",
    "unauthorized",
    "forbidden",
    "schema error",
    "validation error",
    "malformed",
)


class RetryableError(RuntimeError):
    """Transient failure — safe to retry."""


class NonRetryableError(RuntimeError):
    """Permanent failure — send to dead-letter queue."""


def classify_failure(error: Exception) -> bool:
    """Return True if the failure should be retried, else False (dead-letter)."""
    if isinstance(error, NonRetryableError):
        return False
    if isinstance(error, RetryableError):
        return True
    msg = str(error).lower()
    if any(m in msg for m in _NON_RETRYABLE_MARKERS):
        return False
    if any(m in msg for m in _RETRYABLE_MARKERS):
        return True
    # Unknown failures default to *not* retryable to avoid infinite loops.
    return False


class RetryPolicy:
    """Backoff schedule: attempt 1 -> 1s, 2 -> 5s, 3 -> 30s, then give up."""

    SCHEDULE: tuple[int, ...] = (1, 5, 30)

    def __init__(self, max_attempts: int = 3, schedule: tuple[int, ...] | None = None):
        self.max_attempts = max_attempts
        if schedule is not None:
            self.SCHEDULE = schedule

    def backoff_seconds(self, attempt: int, max_attempts: int | None = None) -> int:
        """Return the backoff delay for the given attempt number (1-based)."""
        idx = min(attempt - 1, len(self.SCHEDULE) - 1)
        return self.SCHEDULE[max(0, idx)]

    def should_retry(self, attempt: int, max_attempts: int | None = None) -> bool:
        cap = max_attempts or self.max_attempts
        return attempt < cap

    def retry_at_epoch(self, attempt: int, max_attempts: int | None = None) -> float:
        return time.time() + float(self.backoff_seconds(attempt, max_attempts))