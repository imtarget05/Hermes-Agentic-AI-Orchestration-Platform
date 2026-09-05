"""Worker lifecycle — RECEIVE → VALIDATE → MARK STARTED → EXECUTE → complete/retry/DLX.

These are *separate processes/threads* that pull from the queue; the
orchestrator never executes worker logic directly.

    RECEIVE (basic_get, manual_ack)
      → VALIDATE contract
      → MARK STARTED (idempotency claim)
      → EXECUTE
           success ───────────────────────────> COMPLETED (ack)
           failure → retryable & attempts left → RETRY (requeue w/ backoff)
           failure → non-retryable / exhausted → dead-letter (ack)

Key reliability properties:
  * manual_ack — a worker that crashes before ACK leaves the message
    unacknowledged, so RabbitMQ requeues it and another worker retries.
  * Idempotency — even if a message is re-delivered after a crash, a task
    already in the `execution_state` table as COMPLETED is not run again.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from .contract import (
    DEAD_LETTER_QUEUE,
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_RETRIED,
    EVENT_STARTED,
    Task,
    routing_for,
)
from .metrics import (
    TASK_DURATION,
    TASKS_COMPLETED,
    TASKS_FAILED,
    TASKS_RETRIED,
    TASKS_TOTAL,
    WORKER_ACTIVE,
    WORKER_UTILIZATION,
    BaseMetrics,
    build_metrics,
)
from .retry import RetryPolicy, classify_failure

Handler = Callable[[Task], str]  # returns a result URI (e.g. s3://..., in-memory marker)


class Worker:
    def __init__(
        self,
        name: str,
        task_types: list[str] | str,
        handler: Handler,
        store,
        bus,
        events=None,
        metrics: BaseMetrics | None = None,
        retry_policy: RetryPolicy | None = None,
        poll_interval: float = 0.005,
        verify_idempotency: bool = True,
    ):
        self.name = name
        self.task_types = [task_types] if isinstance(task_types, str) else list(task_types)
        self.handler = handler
        self.store = store
        self.bus = bus
        self.events = events if events is not None else _NoopEvents()
        self.metrics = metrics or build_metrics(False)
        self.policy = retry_policy or RetryPolicy()
        self.poll_interval = poll_interval
        self.verify_idempotency = verify_idempotency
        self.busy = False
        self.processed = 0
        self.failed = 0
        # optional hooks injected by an orchestrator (DAG advancement)
        self.on_task_completed: Callable[[Task, str], None] | None = None
        self.on_task_failed: Callable[[Task, str], None] | None = None

        self._queues: list[str] = []
        for tt in self.task_types:
            _ex, _rk, queue = routing_for(tt)
            self._queues.append(queue)

    def pump_once(self) -> int:
        """Fetch + process a single message. Returns 0 (no work) or 1."""
        for queue in self._queues:
            delivery = self.bus.get(queue)
            if delivery is None:
                continue
            self._process(queue, delivery, delivery.message)
            return 1
        return 0

    def _process(self, queue: str, delivery, raw: dict[str, Any]) -> None:
        # --- VALIDATE ---
        self.metrics.inc(TASKS_TOTAL, 1.0)
        try:
            task = Task.from_message(raw)
        except Exception as e:
            self._deadletter_and_ack(delivery, raw, f"validation error: {e}")
            return

        # --- recent-requeue guard (RabbitMQ immediate-requeue path) ---
        retry_at = raw.get("retry_at")
        if retry_at and time.time() < float(retry_at):
            self.bus.requeue(queue, raw, delay_seconds=float(retry_at) - time.time())
            delivery.ack()
            return

        # --- IDEMPOTENCY: never re-execute a completed task ---
        if self.verify_idempotency and self.store.is_completed(task.task_id):
            delivery.ack()  # already done — just acknowledge the duplicate
            return

        # --- MARK STARTED (atomic claim) ---
        if not self.store.mark_started(task.task_id, self.name):
            delivery.ack()  # another worker owns it, or it's completed
            return

        self.busy = True
        self.metrics.inc(WORKER_ACTIVE, 1.0)
        try:
            duration = self._execute(task, delivery)
            self.metrics.set_gauge(WORKER_UTILIZATION, 1.0)
            self._complete(task, delivery, duration)
        except Exception as e:
            self._on_failure(task, delivery, queue, e)
        finally:
            self.busy = False
            self.metrics.dec(WORKER_ACTIVE, 1.0)

    def _execute(self, task: Task, delivery) -> float:
        self.store.set_attempt(task.task_id, task.attempt)
        self.events.emit(EVENT_STARTED, task_id=task.task_id,
                         workflow_id=task.workflow_id, worker_id=self.name, attempt=task.attempt)
        start = time.time()
        result_uri = self.handler(task)
        elapsed = time.time() - start
        self._pending_result_uri = result_uri
        return elapsed

    def _complete(self, task: Task, delivery, duration: float) -> None:
        result_uri = getattr(self, "_pending_result_uri", "") or ""
        self.store.mark_completed(task.task_id, result_uri=result_uri, worker_id=self.name)
        self.metrics.inc(TASKS_COMPLETED, 1.0)
        self.metrics.observe(TASK_DURATION, duration)
        self.events.emit(EVENT_COMPLETED, task_id=task.task_id,
                         workflow_id=task.workflow_id, worker_id=self.name,
                         attempt=task.attempt, duration_ms=int(duration * 1000))
        delivery.ack()
        self.processed += 1
        if self.on_task_completed:
            self.on_task_completed(task, result_uri)

    def _on_failure(self, task: Task, delivery, queue: str, error: Exception) -> None:
        self.failed += 1
        retryable = classify_failure(error)
        should_retry = retryable and self.policy.should_retry(task.attempt, task.max_attempts)

        if should_retry:
            new_attempt = task.attempt + 1
            delay = self.policy.backoff_seconds(task.attempt, task.max_attempts)
            raw = task.to_message()
            raw["attempt"] = new_attempt
            raw["retry_at"] = time.time() + delay
            raw["metadata"] = {**(raw.get("metadata") or {}), "last_error": str(error)[:300]}
            self.store.mark_retried(task.task_id, new_attempt, worker_id=self.name)
            self.metrics.inc(TASKS_RETRIED, 1.0)
            self.events.emit(EVENT_RETRIED, task_id=task.task_id, workflow_id=task.workflow_id,
                             worker_id=self.name, attempt=new_attempt, error=str(error)[:300])
            # requeue for delivery after backoff, then ACK the consumed copy
            self.bus.requeue(queue, raw, delay_seconds=delay)
            delivery.ack()
            return

        self._deadletter_and_ack(delivery, task.to_message(),
                                 f"{type(error).__name__}: {error}")
        if self.on_task_failed:
            self.on_task_failed(task, f"{type(error).__name__}: {error}")

    def _deadletter_and_ack(self, delivery, raw: dict[str, Any], reason: str) -> None:
        task_id = raw.get("task_id", "?")
        try:
            raw["metadata"] = {**(raw.get("metadata") or {}), "dead_letter_reason": reason}
            if hasattr(self.bus, "publish_deadletter"):
                self.bus.publish_deadletter(raw)
            else:
                self.bus.publish_to_queue(DEAD_LETTER_QUEUE, raw)
            self.store.mark_failed(task_id, reason, worker_id=self.name)
            self.metrics.inc(TASKS_FAILED, 1.0)
            self.events.emit(EVENT_FAILED, task_id=task_id, workflow_id=raw.get("workflow_id", ""),
                             worker_id=self.name, attempt=raw.get("attempt", 1), error=reason)
        except Exception:
            pass
        finally:
            delivery.ack()
            self.failed += 1

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        stop = stop_event if stop_event is not None else threading.Event()
        while not stop.is_set():
            try:
                self.pump_once()
                time.sleep(self.poll_interval)
            except Exception:
                time.sleep(self.poll_interval)


class WorkerPool:
    """Spawn `size` worker threads sharing the same task-handling behaviour."""

    def __init__(self, worker_builder: Callable[[str], Worker], size: int = 1):
        self._builder = worker_builder
        self.size = size
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    def start(self) -> None:
        self._threads = []
        for i in range(self.size):
            w = self._builder(f"worker-{i + 1:02d}")
            t = threading.Thread(target=w.run_forever, args=(self._stop,), daemon=True)
            self._threads.append(t)
            t.start()

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads.clear()

    @property
    def active(self) -> int:
        return len(self._threads)


class _NoopEvents:
    def emit(self, event_type, **fields):
        return None