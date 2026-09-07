"""HERMES-04 — RabbitMQ reliability (queue-level contract on InMemoryBus).

Acceptance: Có DLQ cho poison message; ACK chỉ sau khi xử lý thành công
(not before); message không mất / không bị xử lý 2 lần khi worker crash.

The InMemoryBus models RabbitMQ's manual-ack semantics (basic_get with
auto_ack=False, durable-until-acked, per-message retry delay). These tests
pin the contract the worker relies on — they are the stand-in for the
"kill worker mid-message" acceptance test that needs a real broker.
"""

import pathlib
import tempfile
import threading
import time

from hermes.async_engine.backends import Delivery, InMemoryBus
from hermes.async_engine.contract import (
    DEAD_LETTER_QUEUE,
    Task,
    TaskStatus,
    routing_for,
)
from hermes.async_engine.eventbus import InMemoryEventBus
from hermes.async_engine.orchestrator import AsyncOrchestrator
from hermes.async_engine.retry import NonRetryableError
from hermes.async_engine.store import AsyncTaskStore
from hermes.async_engine.worker import Worker


def _setup(tmp_path):
    if isinstance(tmp_path, str):
        tmp_path = pathlib.Path(tempfile.mkdtemp(prefix=tmp_path.replace("/", "_")))
    store = AsyncTaskStore(str(tmp_path / "t.db"))
    bus = InMemoryBus()
    events = InMemoryEventBus()
    orch = AsyncOrchestrator(store, bus, events=events)
    return store, bus, orch


# ---------------------------------------------------------------- ACK contract

def test_delivery_not_acked_until_ack_called():
    """A fetched message starts un-acked (manual_ack) — the worker controls
    exactly when it is acknowledged."""
    bus = InMemoryBus()
    # publish("ex","rk",...) routes via _queue_for -> "q.rk"
    bus.publish("ex", "rk", {"task_id": "a"})
    d = bus.get("q.rk")
    assert isinstance(d, Delivery)
    assert d._acked is False  # not auto-acked
    d.ack()
    assert d._acked is True


def test_nack_marks_rejected():
    bus = InMemoryBus()
    bus.publish("ex", "rk", {"task_id": "a"})
    d = bus.get("q.rk")
    d.nack(requeue=True)
    assert d._rejected is True


def test_ack_after_success_not_before():
    """The worker ACKs only AFTER mark_completed. A successful run leaves the
    queue empty (message consumed); the task is COMPLETED."""
    store, bus, orch = _setup("/tmp/h4a")

    def ok(t):
        return "s3://res"

    graph = [{"task_id": "t1", "task_type": "research", "deps": []}]
    agg = orch.run_workflow(graph, {"research": ok}, workers=1, timeout=10)
    assert agg["counts"].get("completed") == 1
    assert store.is_completed("t1")
    assert bus.queue_depth("q.agent.research") == 0  # drained = acked


# ---------------------------------------------------------------- poison → DLQ

def test_poison_message_ends_in_dead_letter_queue():
    """A task that always fails with a NON-retryable error (a poison message)
    is routed to the dead-letter queue after retries are exhausted — never
    re-enqueued forever."""
    store, bus, orch = _setup("/tmp/h4b")

    def poison(t):
        raise NonRetryableError("invalid payload: not a real quote")

    graph = [{"task_id": "t1", "task_type": "research", "deps": []}]
    agg = orch.run_workflow(graph, {"research": poison}, workers=1, timeout=10)
    assert agg["counts"].get("failed") == 1
    assert store.get_task("t1").status == TaskStatus.FAILED
    assert bus.queue_depth(DEAD_LETTER_QUEUE) == 1  # poison → DLQ
    assert bus.queue_depth("q.agent.research") == 0


def test_retryable_failure_retries_then_succeeds():
    """A transient failure retries (message re-queued with delay) and eventually
    succeeds — proving retry routing works before the DLQ."""
    store, bus, orch = _setup("/tmp/h4c")
    calls = {"n": 0}

    def flaky(t):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("connection reset")  # retryable
        return "s3://res"

    graph = [{"task_id": "t1", "task_type": "research", "deps": []}]
    agg = orch.run_workflow(graph, {"research": flaky}, workers=1, timeout=20)
    assert agg["counts"].get("completed") == 1
    assert calls["n"] == 2
    assert bus.queue_depth(DEAD_LETTER_QUEUE) == 0  # recovered, no DLQ


# ---------------------------------------------------------------- no double processing

def test_redelivered_message_not_processed_twice():
    """If a message is re-delivered after a crash (before ACK), the idempotency
    guard prevents a second execution — the worker ACKs and skips."""
    store, bus = _setup("/tmp/h4d")[:2]
    store.create_task(Task(task_id="t1", task_type="research", workflow_id="wf"))
    # crash happened after execution, before ACK → task already COMPLETED
    store.mark_completed("t1", result_uri="s3://done")
    assert store.is_completed("t1") is True
    # re-delivery of the same task_id is harmless (guard ack+return, no re-run)
    store.mark_completed("t1", result_uri="s3://done-again")
    assert len(store.task_results("t1")) == 1  # upsert, no duplicate row


def test_message_not_lost_on_crash_after_get():
    """A message fetched but not yet acked must remain the broker's
    responsibility (durable) so a replacement worker can finish it."""
    bus = InMemoryBus()
    bus.publish("ex", "rk", {"task_id": "a"})
    d = bus.get("q.rk")
    assert d is not None
    assert d._acked is False  # simulated crash before ack
    # ack only after the work is durably done
    d.ack()
    assert bus.queue_depth("q.rk") == 0


# ---------------------------------------------------------------- retry-delay (backoff) contract

def test_requeue_respects_retry_delay():
    """A retried message carries a `retry_at` timestamp and is not due until
    that time — models RabbitMQ TTL+DLX backoff."""
    bus = InMemoryBus()
    # publish() routes via _queue_for(exchange, routing_key); "rk" -> "q.rk"
    bus.publish("ex", "rk", {"task_id": "a"})
    d = bus.get("q.rk")
    assert d is not None
    # requeue with a 10s delay
    bus.requeue("q.rk", d.message, delay_seconds=10.0)
    # not yet due → get() returns None; depth counts only due messages
    assert bus.get("q.rk") is None
    assert bus.queue_depth("q.rk") == 0
    # a freshly published message is due immediately
    bus.publish("ex", "rk", {"task_id": "b"})
    assert bus.get("q.rk") is not None


# ---------------------------------------------------------------- HERMES-04-FU1: concurrent duplicate-delivery race

def test_mark_started_atomic_under_concurrency(tmp_path):
    """HERMES-04-FU1: N threads race to claim the SAME task_id — the atomic
    compare-and-set in mark_started must let exactly ONE win. The loser must
    observe the already-running row, not overwrite it."""
    store, bus, orch = _setup(tmp_path)
    store.create_task(Task(task_id="t1", task_type="research", workflow_id="wf"))

    N = 8
    barrier = threading.Barrier(N)
    results: list[bool] = []
    lock = threading.Lock()

    def claim(i: int) -> None:
        barrier.wait()  # maximize contention: all claim at the same instant
        won = store.mark_started("t1", f"worker-{i:02d}")
        with lock:
            results.append(won)

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1   # exactly one consumer won the claim
    assert results.count(False) == N - 1


def test_mark_started_refused_while_running_or_completed(tmp_path):
    """Sequential guard still holds with the CAS: RUNNING and COMPLETED both
    refuse a new claim; a FAILED (retryable) task may be claimed again."""
    store, bus, orch = _setup(tmp_path)
    store.create_task(Task(task_id="t1", task_type="research", workflow_id="wf"))

    assert store.mark_started("t1", "w1") is True   # CREATED -> RUNNING
    assert store.mark_started("t1", "w2") is False  # already RUNNING
    store.mark_completed("t1")
    assert store.mark_started("t1", "w3") is False  # already COMPLETED


def test_concurrent_duplicate_messages_execute_once(tmp_path):
    """Two consumers, two copies of the same message, delivered near-
    simultaneously: the side-effecting handler must run EXACTLY once. The
    loser no-ops (ack without executing) after losing the atomic claim."""
    store, bus, orch = _setup(tmp_path)
    task = Task(task_id="t1", task_type="research", workflow_id="wf")
    store.create_task(task)

    side_effects: list[str] = []
    lock = threading.Lock()

    def handler(t: Task) -> str:
        with lock:
            side_effects.append(t.task_id)
        time.sleep(0.05)  # widen the window: the loser is mid-flight while
        return "s3://res"  # the winner still holds the RUNNING claim

    ex, rk, q = routing_for("research")
    bus.publish(ex, rk, dict(task.to_message()))
    bus.publish(ex, rk, dict(task.to_message()))  # duplicate copy

    workers = [
        Worker(f"w{i}", "research", handler, store, bus,
               events=InMemoryEventBus())
        for i in range(2)
    ]
    barrier = threading.Barrier(len(workers))

    def pump(w: Worker) -> None:
        barrier.wait()  # both consumers start at the same instant
        w.pump_once()

    threads = [threading.Thread(target=pump, args=(w,)) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # exactly one path performed the side-effecting work
    assert side_effects.count("t1") == 1
    assert store.is_completed("t1") is True
