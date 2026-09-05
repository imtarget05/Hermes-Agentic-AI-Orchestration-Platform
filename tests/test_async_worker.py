import time

from hermes.async_engine.backends import DEAD_LETTER_QUEUE, InMemoryBus
from hermes.async_engine.contract import (
    EVENT_COMPLETED,
    EVENT_RETRIED,
    ROUTING,
    Task,
    TaskStatus,
)
from hermes.async_engine.eventbus import InMemoryEventBus
from hermes.async_engine.metrics import NoopMetrics
from hermes.async_engine.retry import NonRetryableError, RetryableError, RetryPolicy
from hermes.async_engine.store import AsyncTaskStore
from hermes.async_engine.worker import Worker, WorkerPool


def _make_env(tmp_path):
    store = AsyncTaskStore(str(tmp_path / "t.db"))
    bus = InMemoryBus()
    events = InMemoryEventBus()
    metrics = NoopMetrics()
    return store, bus, events, metrics


def _publish(bus, task):
    ex, rk, q = ROUTING[task.task_type]
    bus.publish(ex, rk, task.to_message())


def test_worker_manual_ack_on_success(tmp_path):
    store, bus, events, metrics = _make_env(tmp_path)
    task = Task(task_id="ok", workflow_id="wf", task_type="research")
    store.create_task(task)
    _publish(bus, task)

    handled = []
    w = Worker("w1", "research", lambda t: handled.append(t.task_id) or "s3://r/ok",
               store, bus, events=events, metrics=metrics)
    assert w.pump_once() == 1
    assert store.is_completed("ok") is True
    assert handled == ["ok"]  # handler executed exactly once


def test_worker_deadletters_invalid_payload(tmp_path):
    store, bus, events, metrics = _make_env(tmp_path)
    ex, rk, q = ROUTING["research"]
    bus.publish(ex, rk, {"no": "task_type"})  # invalid contract

    w = Worker("w1", "research", lambda t: "x", store, bus, events=events, metrics=metrics)
    w.pump_once()
    assert bus.queue_depth(q) == 0
    assert bus.queue_depth(DEAD_LETTER_QUEUE) == 1  # deadlettered


def test_worker_retries_retryable_failure_then_succeeds(tmp_path):
    store, bus, events, metrics = _make_env(tmp_path)
    task = Task(task_id="flaky", workflow_id="wf", task_type="research", max_attempts=3)
    store.create_task(task)
    _publish(bus, task)

    calls = {"n": 0}

    def handler(t):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryableError("provider temporarily unavailable")
        return "s3://r/flaky"

    w = Worker("w1", "research", handler, store, bus, events=events, metrics=metrics,
               retry_policy=RetryPolicy(max_attempts=3, schedule=(0, 0, 0)))
    w.pump_once()  # attempt 1 -> retry (requeued for immediate delivery)
    assert calls["n"] == 1
    assert store.get_task("flaky").status == TaskStatus.RETRY
    assert len(events.of(EVENT_RETRIED)) == 1

    w.pump_once()  # attempt 2 -> success
    assert calls["n"] == 2
    assert store.is_completed("flaky") is True
    assert len(events.of(EVENT_COMPLETED)) == 1


def test_worker_deadletters_after_exhausting_retries(tmp_path):
    store, bus, events, metrics = _make_env(tmp_path)
    task = Task(task_id="doomed", workflow_id="wf", task_type="research", max_attempts=2)
    store.create_task(task)
    _publish(bus, task)

    def handler(t):
        raise RetryableError("503 unavailable")  # always transient, but exhausted

def test_idempotency_skips_reredelivered_completed_task(tmp_path):
    # simulate: worker completed task but crashed before ACK -> message redelivered
    store, bus, events, metrics = _make_env(tmp_path)
    task = Task(task_id="twice", workflow_id="wf", task_type="research")
    store.create_task(task)
    store.mark_started("twice", "worker-01")
    store.mark_completed("twice", result_uri="s3://r/twice")

    _publish(bus, task)  # duplicate delivery
    calls = {"n": 0}
    w = Worker("w1", "research", lambda t: calls.__setitem__("n", calls["n"] + 1) or "x",
               store, bus, events=events, metrics=metrics)
    w.pump_once()
    assert calls["n"] == 0  # NOT re-executed
    assert store.task_results("twice")[0]["result_uri"] == "s3://r/twice"


def test_metrics_recorded(tmp_path):
    store, bus, events, metrics = _make_env(tmp_path)
    task = Task(task_id="m", workflow_id="wf", task_type="report")
    store.create_task(task)
    _publish(bus, task)
    w = Worker("w1", "report", lambda t: "out", store, bus, events=events, metrics=metrics)
    w.pump_once()
    assert metrics.counter_total("tasks_total") == 1
    assert metrics.counter_total("tasks_completed_total") == 1


def test_pool_parallel_drains_queue(tmp_path):
    store, bus, events, metrics = _make_env(tmp_path)
    n = 12
    for i in range(n):
        t = Task(task_id=f"p{i}", workflow_id="wf", task_type="analyze")
        store.create_task(t)
        _publish(bus, t)

    def build(name):
        return Worker(name, "analyze", lambda t: "r", store, bus, events=events, metrics=metrics)

    pool = WorkerPool(build, size=4)
    pool.start()
    pool_thread_count = pool.active
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if store.task_counts().get("completed", 0) >= n or \
               store.task_counts().get("failed", 0) >= n:
                break
            time.sleep(0.01)
    finally:
        pool.stop()
    assert store.task_counts().get("completed", 0) == n
    assert pool_thread_count == 4  # 4 worker threads ran


def test_worker_deadletters_non_retryable_immediately(tmp_path):
    store, bus, events, metrics = _make_env(tmp_path)
    task = Task(task_id="auth", workflow_id="wf", task_type="research")
    store.create_task(task)
    _publish(bus, task)

    def handler(t):
        raise NonRetryableError("authentication failed")

    w = Worker("w1", "research", handler, store, bus, events=events, metrics=metrics,
               retry_policy=RetryPolicy(max_attempts=3, schedule=(0, 0, 0)))
    w.pump_once()
    assert store.get_task("auth").status == TaskStatus.FAILED
    assert bus.queue_depth(DEAD_LETTER_QUEUE) == 1