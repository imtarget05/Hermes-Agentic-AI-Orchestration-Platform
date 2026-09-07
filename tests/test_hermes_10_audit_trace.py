"""HERMES-10 — Audit/Observability + Kafka-not-critical-dependency (R16).

Acceptance: trace workflow→supervisor→task→queue→worker→tool→result→verifier→
aggregator→decision; and the main pipeline (RabbitMQ + Postgres) still completes
when Kafka is down — only the audit trail is lost.

The event bus is already off the critical path by construction
(KafkaEventBus.emit is fire-and-forget and swallows all exceptions; the default
is NoopEvents). These tests prove it.
"""

from hermes.async_engine.contract import (
    EVENT_COMPLETED,
    EVENT_CREATED,
    EVENT_FAILED,
    EVENT_RETRIED,
    EVENT_STARTED,
)
from hermes.async_engine.eventbus import InMemoryEventBus
from hermes.async_engine.orchestrator import AsyncOrchestrator
from hermes.async_engine.retry import NonRetryableError
from hermes.async_engine.store import AsyncTaskStore


def _orch(tmp_path, events=None):
    store = AsyncTaskStore(str(tmp_path / "t.db"))
    from hermes.async_engine.backends import InMemoryBus

    bus = InMemoryBus()
    if events is None:
        events = InMemoryEventBus()
    return AsyncOrchestrator(store, bus, events=events), store


# ---------------------------------------------------------------- trace shape

def test_trace_chain_emits_lifecycle_events_per_task(tmp_path):
    """Every task emits the full lifecycle: created -> started -> completed,
    all sharing the same workflow_id — the traceability chain is observable."""
    orch, store = _orch(tmp_path)

    def ok(t):
        return "s3://res"

    graph = [{"task_id": "t1", "task_type": "research", "deps": []}]
    agg = orch.run_workflow(graph, {"research": ok}, workers=1, timeout=10)

    wf_id = agg["workflow_id"]  # real id, not a hardcoded one
    ev = orch.events

    created = [e for e in ev.events if e["event_type"] == EVENT_CREATED]
    started = [e for e in ev.events if e["event_type"] == EVENT_STARTED]
    completed = [e for e in ev.events if e["event_type"] == EVENT_COMPLETED]

    assert len(created) == 1
    assert len(started) == 1
    assert len(completed) >= 1  # worker + orchestrator both emit completed
    # all events correlate to the same workflow
    assert created[0]["workflow_id"] == wf_id
    assert started[0]["workflow_id"] == wf_id
    assert completed[0]["workflow_id"] == wf_id
    # and to the same task
    assert created[0]["task_id"] == "t1"
    assert completed[0]["task_id"] == "t1"


def test_trace_includes_failure_and_retry_events(tmp_path):
    """A failing-then-succeeding task emits retried events in the trace before
    the final completion; a non-retryable failure emits a failed event."""
    orch, store = _orch(tmp_path)
    calls = {"n": 0}

    def flaky(t):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("connection reset")  # retryable
        return "s3://res"

    graph = [{"task_id": "t1", "task_type": "research", "deps": []}]
    orch.run_workflow(graph, {"research": flaky}, workers=1, timeout=20)

    ev = orch.events
    # the transient failure is retried (not marked failed) then completes.
    # (completed may be emitted by both the worker and the orchestrator)
    assert len(ev.of(EVENT_RETRIED)) >= 1
    assert len(ev.of(EVENT_COMPLETED)) >= 1
    assert len(ev.of(EVENT_FAILED)) == 0  # recovered, so no terminal failure


def test_trace_includes_failed_event_for_poison_message(tmp_path):
    """A non-retryable failure emits a failed event in the trace."""
    orch, store = _orch(tmp_path)

    def poison(t):
        raise NonRetryableError("invalid payload")

    graph = [{"task_id": "t1", "task_type": "research", "deps": []}]
    orch.run_workflow(graph, {"research": poison}, workers=1, timeout=10)

    ev = orch.events
    assert len(ev.of(EVENT_FAILED)) >= 1  # worker + orchestrator both emit
    assert len(ev.of(EVENT_COMPLETED)) == 0


# ---------------------------------------------------------------- Kafka not critical (R16)

class _BrokenEventBus:
    """Simulates Kafka being down: every emit raises."""

    def __init__(self):
        self.events = []
        self.failed = 0

    def emit(self, event_type, **fields):
        self.failed += 1
        raise RuntimeError("Kafka broker unavailable:9092")


def test_main_pipeline_completes_when_kafka_down(tmp_path):
    """With the event bus broken (Kafka down), the main pipeline — RabbitMQ
    (InMemoryBus) + Postgres (store) — still completes the workflow correctly.
    Only the audit trail is lost. This proves Kafka is NOT a critical
    execution dependency (R16)."""
    store = AsyncTaskStore(str(tmp_path / "t.db"))
    from hermes.async_engine.backends import InMemoryBus

    bus = InMemoryBus()
    broken = _BrokenEventBus()
    orch = AsyncOrchestrator(store, bus, events=broken)

    def ok(t):
        return "s3://res"

    graph = [{"task_id": "t1", "task_type": "research", "deps": []}]
    # The workflow must still complete despite every event emit raising.
    agg = orch.run_workflow(graph, {"research": ok}, workers=1, timeout=10)

    assert agg["counts"].get("completed") == 1
    assert store.is_completed("t1")
    # audit trail is lost (every emit failed) — that's the expected tradeoff
    assert broken.failed >= 1


def test_noop_events_also_lets_pipeline_complete(tmp_path):
    """The default NoopEvents path (no broker at all) completes the workflow —
    confirming the orchestrator never blocks on the event bus."""
    from hermes.async_engine.orchestrator import _NoopEvents

    store = AsyncTaskStore(str(tmp_path / "t.db"))
    from hermes.async_engine.backends import InMemoryBus

    bus = InMemoryBus()
    orch = AsyncOrchestrator(store, bus, events=_NoopEvents())

    def ok(t):
        return "s3://res"

    graph = [{"task_id": "t1", "task_type": "research", "deps": []}]
    agg = orch.run_workflow(graph, {"research": ok}, workers=1, timeout=10)
    assert agg["counts"].get("completed") == 1
    assert store.is_completed("t1")
