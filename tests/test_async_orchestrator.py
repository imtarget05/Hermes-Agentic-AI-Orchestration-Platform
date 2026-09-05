import time

from hermes.async_engine.backends import InMemoryBus
from hermes.async_engine.eventbus import InMemoryEventBus
from hermes.async_engine.metrics import NoopMetrics
from hermes.async_engine.orchestrator import AsyncOrchestrator
from hermes.async_engine.store import AsyncTaskStore


def _orch(tmp_path):
    store = AsyncTaskStore(str(tmp_path / "t.db"))
    bus = InMemoryBus()
    events = InMemoryEventBus()
    metrics = NoopMetrics()
    return AsyncOrchestrator(store, bus, events=events, metrics=metrics), store, bus, events


def _handlers():
    def research(t):
        time.sleep(0.02)
        return f"s3://res/{t.task_id}"

    def analyze(t):
        time.sleep(0.02)
        return f"s3://an/{t.task_id}"

    def report(t):
        return f"s3://rep/{t.task_id}"

    return {"research": research, "analyze": analyze, "report": report}


PARALLEL_GRAPH = [
    {"task_id": "research-1", "task_type": "research", "deps": []},
    {"task_id": "analyze-1", "task_type": "analyze", "deps": ["research-1"]},
    {"task_id": "analyze-2", "task_type": "analyze", "deps": ["research-1"]},
    {"task_id": "report-1", "task_type": "report", "deps": ["analyze-1", "analyze-2"]},
]


def test_run_workflow_parallel_dag_completes(tmp_path):
    orch, store, bus, events = _orch(tmp_path)
    agg = orch.run_workflow(PARALLEL_GRAPH, _handlers(), workers=4)
    assert agg["status"] == "completed"
    assert agg["task_count"] == 4
    assert agg["counts"].get("completed") == 4
    for tid in ("research-1", "analyze-1", "analyze-2", "report-1"):
        assert store.is_completed(tid)
        assert store.task_results(tid)[0]["result_uri"]


def test_report_starts_after_both_analyze_complete(tmp_path):
    orch, store, bus, events = _orch(tmp_path)
    orch.run_workflow(PARALLEL_GRAPH, _handlers(), workers=4)
    started = [e for e in events.events if e["event_type"] == "task.started"]
    analyze_done = {t["task_id"] for t in started if t["task_id"] in ("analyze-1", "analyze-2")}
    report = [e for e in started if e["task_id"] == "report-1"]
    assert len(analyze_done) == 2  # both analyze branches ran
    # report.task.started must exist and come after both analyze completions
    done = [e for e in events.events if e["event_type"] == "task.completed"
            and e["task_id"] in ("analyze-1", "analyze-2")]
    assert report and done
    report_received_ts = _ts(report[0]["timestamp"])
    for d in done:
        assert report_received_ts >= _ts(d["timestamp"])


def test_all_lifecycle_events_emitted(tmp_path):
    orch, store, bus, events = _orch(tmp_path)
    orch.run_workflow(PARALLEL_GRAPH, _handlers(), workers=4)
    types = {e["event_type"] for e in events.events}
    assert {"task.created", "task.started", "task.completed"} <= types


def test_nonretryable_handler_fails_workflow(tmp_path):
    orch, store, bus, events = _orch(tmp_path)

    def base(t):
        time.sleep(0.01)
        return "r"

    def bad(t):
        raise RuntimeError("schema error")  # non-retryable

    handlers = {"research": base, "analyze": bad, "report": base}
    agg = orch.run_workflow(PARALLEL_GRAPH, handlers, workers=2)
    assert agg["status"] == "failed"
    assert agg["counts"].get("failed", 0) >= 1


def _ts(iso: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()