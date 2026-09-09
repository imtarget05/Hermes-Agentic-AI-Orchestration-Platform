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
from typing import TYPE_CHECKING, Any

from .budgets import BudgetExceededError
from .contract import (
    DEAD_LETTER_QUEUE,
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_RETRIED,
    EVENT_STARTED,
    Task,
    TaskStatus,
    routing_for,
)
from .eventbus import emit_best_effort
from .loops.verify import VerificationError
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
from .tracing import (
    maybe_trace_task,
    maybe_trace_verification,
    record_task_duration,
    record_task_execution,
)
from .retry import RetryPolicy, classify_failure

if TYPE_CHECKING:
    from .loops.reliability import CircuitBreaker
    from .loops.verify import Verifier

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
        # ---- loop 5 (verification) + loop 6 (reliability) hooks ----
        verifier: Verifier | None = None,
        breaker: CircuitBreaker | None = None,
        timeout_seconds: float = 30.0,
        cost_tracker=None,
    ):
        from .loops.reliability import CircuitBreaker
        from .loops.verify import (
            Verifier,  # lazy: loops depend on engine, not vice versa
        )

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
        self.verifier = verifier if verifier is not None else Verifier()
        self.breaker = breaker if breaker is not None else CircuitBreaker()
        self.timeout_seconds = timeout_seconds
        self.cost_tracker = cost_tracker  # HERMES-09 workflow budget
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

        # --- IDEMPOTENCY KEY CHECK: check if this specific execution was already processed ---
        idempotency_key = raw.get("idempotency_key") or task.idempotency_key
        if idempotency_key and self.verify_idempotency:
            existing_task_id = self.store.check_idempotency_key(idempotency_key)
            if existing_task_id and existing_task_id != task.task_id:
                delivery.ack()  # duplicate execution - skip
                return

        # --- RELIABILITY: circuit breaker (loop 6) ---
        if not self.breaker.allow(task.task_type):
            delivery.ack()
            self.bus.requeue(queue, raw, delay_seconds=self.policy.backoff_seconds(1))
            return

        # --- MARK STARTED (atomic claim) ---
        if not self.store.mark_started(task.task_id, self.name):
            delivery.ack()  # another worker owns it, or it's completed
            return

        # Store idempotency key after successful claim
        if idempotency_key:
            self.store.store_idempotency_key(idempotency_key, task.task_id)

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
        # HERMES-09: hard stop when the workflow budget is exhausted.
        # BudgetExceededError is NonRetryableError -> dead-letter, no retry loop.
        if self.cost_tracker is not None:
            stop = self.cost_tracker.exceeded()
            if stop:
                raise BudgetExceededError(stop)
        self.store.set_attempt(task.task_id, task.attempt)
        emit_best_effort(self.events, EVENT_STARTED, task_id=task.task_id,
                         workflow_id=task.workflow_id, worker_id=self.name, attempt=task.attempt)

        # Update execution state at start
        exec_state = task.execution_state.copy()
        exec_state["attempt"] = task.attempt
        exec_state["status"] = TaskStatus.RUNNING.value
        exec_state["started_at"] = time.time()
        exec_state["worker_id"] = self.name
        self.store.update_task_execution_state(task.task_id, exec_state)

        start = time.time()
        
        # T7.1: Trace task execution
        with maybe_trace_task(task.task_id, task.task_type, task.workflow_id) as task_span:
            if task_span:
                task_span.set_attribute("worker.id", self.name)
                task_span.set_attribute("task.attempt", task.attempt)
            
            # loop 6: hard deadline around handler execution
            from .loops.reliability import run_with_timeout

            result_uri = run_with_timeout(lambda: self.handler(task),
                                          self.timeout_seconds, task.task_type)
        
        elapsed = time.time() - start
        record_task_duration(task.task_type, elapsed * 1000, "success")

        # Update execution state with partial result
        exec_state["partial_result"] = result_uri
        exec_state["elapsed"] = elapsed
        self.store.update_task_execution_state(task.task_id, exec_state)

        # loop 5: verification before the result is accepted
        with maybe_trace_verification(task.task_id, task.task_type) as verify_span:
            verdict = self.verifier.verify(task, result_uri)
            if verify_span:
                verify_span.set_attribute("verification.passed", verdict.passed)
                verify_span.set_attribute("verification.retryable", verdict.retryable)
            if not verdict.passed:
                raise VerificationError(verdict.reason, retryable=verdict.retryable)
        self._pending_result_uri = result_uri
        return elapsed

    def _complete(self, task: Task, delivery, duration: float) -> None:
        result_uri = getattr(self, "_pending_result_uri", "") or ""
        self.store.mark_completed(task.task_id, result_uri=result_uri, worker_id=self.name)
        self.breaker.record_success(task.task_type)  # loop 6
        self.metrics.inc(TASKS_COMPLETED, 1.0)
        self.metrics.observe(TASK_DURATION, duration)
        record_task_execution(task.task_type, "completed")
        record_task_duration(task.task_type, duration * 1000, "completed")
        emit_best_effort(self.events, EVENT_COMPLETED, task_id=task.task_id,
                         workflow_id=task.workflow_id, worker_id=self.name,
                         attempt=task.attempt, duration_ms=int(duration * 1000))
        delivery.ack()
        self.processed += 1
        if self.on_task_completed:
            self.on_task_completed(task, result_uri)

    def _on_failure(self, task: Task, delivery, queue: str, error: Exception) -> None:
        self.failed += 1
        self.breaker.record_failure(task.task_type)  # loop 6
        retryable = classify_failure(error) or getattr(error, "retryable", False)
        should_retry = retryable and self.policy.should_retry(task.attempt, task.max_attempts)

        if should_retry:
            new_attempt = task.attempt + 1
            delay = self.policy.backoff_seconds(task.attempt, task.max_attempts)
            raw = task.to_message()
            raw["attempt"] = new_attempt
            raw["retry_at"] = time.time() + delay
            raw["metadata"] = {**(raw.get("metadata") or {}), "last_error": str(error)[:300]}

            # Update execution state for retry
            exec_state = task.execution_state.copy()
            exec_state["attempt"] = new_attempt
            exec_state["status"] = TaskStatus.RETRYING.value
            exec_state["last_error"] = str(error)[:300]
            exec_state["retry_at"] = time.time() + delay
            self.store.update_task_execution_state(task.task_id, exec_state)

            self.store.mark_retried(task.task_id, new_attempt, worker_id=self.name)
            self.metrics.inc(TASKS_RETRIED, 1.0)
            record_task_execution(task.task_type, "retried")
            emit_best_effort(self.events, EVENT_RETRIED, task_id=task.task_id, workflow_id=task.workflow_id,
                             worker_id=self.name, attempt=new_attempt, error=str(error)[:300])
            # Generate new idempotency key for retry
            new_key = f"{task.workflow_id}:{task.task_id}:{new_attempt}"
            import hashlib
            raw["idempotency_key"] = hashlib.sha256(new_key.encode()).hexdigest()[:32]
            # requeue for delivery after backoff, then ACK the consumed copy
            self.bus.requeue(queue, raw, delay_seconds=delay)
            delivery.ack()
            return

        record_task_execution(task.task_type, "failed")
        self._deadletter_and_ack(delivery, task.to_message(),
                                 f"{type(error).__name__}: {error}")
        if self.on_task_failed:
            self.on_task_failed(task, f"{type(error).__name__}: {error}")

    def _deadletter_and_ack(self, delivery, raw: dict[str, Any], reason: str) -> None:
        task_id = raw.get("task_id", "?")
        raw["metadata"] = {**(raw.get("metadata") or {}), "dead_letter_reason": reason}
        # loop 6: escalate terminal failures after retries are exhausted
        if int(raw.get("attempt", 1)) >= int(raw.get("max_attempts", 1)):
            try:
                from .loops.reliability import escalation_metadata
                probe = Task.from_message(raw)
                raw["metadata"].update(escalation_metadata(probe, reason))
                emit_best_effort(self.events, "task.escalated", task_id=task_id,
                                 workflow_id=raw.get("workflow_id", ""),
                                 worker_id=self.name, reason=reason[:200])
            except Exception:
                pass  # best-effort escalation metadata
        try:
            if hasattr(self.bus, "publish_deadletter"):
                self.bus.publish_deadletter(raw)
            else:
                self.bus.publish_to_queue(DEAD_LETTER_QUEUE, raw)
            self.store.mark_failed(task_id, reason, worker_id=self.name)
            self.metrics.inc(TASKS_FAILED, 1.0)
            emit_best_effort(self.events, EVENT_FAILED, task_id=task_id, workflow_id=raw.get("workflow_id", ""),
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
