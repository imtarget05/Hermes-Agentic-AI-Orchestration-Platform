"""Audit tests for the Hermes risk matrix (HERMES-01..10).

Each test maps to an audit TODO item and verifies the acceptance criteria:
  HERMES-01  spawn limits (MAX_TASKS / MAX_AGENTS / MAX_ITERATIONS)
  HERMES-03  echo chamber: agreement != verification (independent recompute)
  HERMES-05  idempotency at DB level (no duplicate result rows)
  HERMES-06  DAG deadlock: failed dep cascade ends the workflow deterministically
  HERMES-08  state machine rejects illegal transitions (incl. TIMEOUT/CANCELLED)
  HERMES-09  cost budget STOP (non-retryable, observable fallback)
"""
import time

import pytest

from hermes.async_engine.backends import InMemoryBus
from hermes.async_engine.budgets import (
    BudgetExceededError,
    BudgetLimits,
    CostTracker,
    validate_graph_budget,
)
from hermes.async_engine.contract import Task, TaskStatus, validate_transition
from hermes.async_engine.eventbus import InMemoryEventBus
from hermes.async_engine.loops.verify import PROCUREMENT_VALIDATORS, Verifier
from hermes.async_engine.orchestrator import AsyncOrchestrator
from hermes.async_engine.store import AsyncTaskStore


def _orch(tmp_path, **kw):
    store = AsyncTaskStore(str(tmp_path / "t.db"))
    bus = InMemoryBus()
    events = InMemoryEventBus()
    return AsyncOrchestrator(store, bus, events=events, **kw), store


# ---------------------------------------------------------------- HERMES-01

def test_oversized_graph_rejected_before_persist(tmp_path):
    orch, store = _orch(tmp_path)
    limits = BudgetLimits(max_tasks=5, max_agents=4, max_iterations=3)
    big = [{"task_id": f"n{i}", "task_type": "analyze", "deps": []}
           for i in range(10)]
    with pytest.raises(BudgetExceededError):
        validate_graph_budget(big, limits)
    # also enforced with DEFAULT limits via create_tasks (60 > 50 MAX_TASKS)
    with pytest.raises(BudgetExceededError):
        orch.create_tasks([{"task_id": f"n{i}", "task_type": "analyze", "deps": []}
                           for i in range(60)], "wf-big")
    # nothing was persisted — the spawn never happened
    assert store.list_tasks() == []


def test_too_many_agent_types_rejected(tmp_path):
    limits = BudgetLimits(max_tasks=50, max_agents=2, max_iterations=3)
    graph = [{"task_id": "a", "task_type": "research", "deps": []},
             {"task_id": "b", "task_type": "analyze", "deps": []},
             {"task_id": "c", "task_type": "report", "deps": []}]
    with pytest.raises(BudgetExceededError):
        validate_graph_budget(graph, limits)


def test_iterations_capped(tmp_path):
    graph = [{"task_id": "a", "task_type": "research", "deps": [],
              "max_attempts": 99}]
    with pytest.raises(BudgetExceededError):
        validate_graph_budget(graph, BudgetLimits(max_iterations=5))


def test_run_workflow_enforces_budget(tmp_path):
    orch, _ = _orch(tmp_path)
    handlers = {"research": lambda t: "ok", "analyze": lambda t: "ok"}
    graph = [{"task_id": f"n{i}", "task_type": "research", "deps": []}
             for i in range(60)]  # > default MAX_TASKS_PER_WORKFLOW=50
    with pytest.raises(BudgetExceededError):
        orch.run_workflow(graph, handlers)


def test_planner_caps_llm_graph():
    from hermes.async_engine.loops.planner import Planner

    def runaway_llm(_prompt):
        import json
        return json.dumps([
            {"task_id": f"n{i}", "task_type": "research", "deps": []}
            for i in range(500)
        ])

    nodes = Planner(llm=runaway_llm).plan("anything")
    assert len(nodes) <= 50  # MAX_TASKS_PER_WORKFLOW


# ---------------------------------------------------------------- HERMES-03

QUOTES = [
    {"vendor": "Dell", "total": 60000, "unit_price": 1200},
    {"vendor": "Lenovo", "total": 54000, "unit_price": 1080},
]


def _task_with_quotes(task_type="analysis"):
    return Task(task_id="analysis-1", task_type=task_type,
                payload={"quotes": QUOTES})


def test_echo_chamber_two_agents_same_wrong_claim_caught():
    """Two agents both (wrongly) recommend a vendor not in the source quotes.
    The independent verifier recomputes from the ORIGINAL evidence and rejects
    the claim — agreement between agents is not verification."""
    verifier = Verifier(by_task_type=dict(PROCUREMENT_VALIDATORS))
    task = _task_with_quotes()
    wrong = ('{"vendor": "Asus", "total": 40000, "unit_price": 800, '
             '"reasons": [{"claim": "cheapest", "evidence_ref": "quote-1"}], '
             '"evidence_refs": ["quote-1"]}')
    verdict = verifier.verify(task, wrong)
    assert not verdict.passed
    assert "echo-chamber" in verdict.reason or "not in source quotes" in verdict.reason


def test_verifier_recomputes_price_from_source():
    """Recommendation total must match the recomputed best quote (54000),
    even if the analysis agent claimed a different number."""
    verifier = Verifier(by_task_type=dict(PROCUREMENT_VALIDATORS))
    task = _task_with_quotes()
    wrong_price = ('{"vendor": "Lenovo", "total": 59000, "unit_price": 1180, '
                   '"reasons": [{"claim": "best", "evidence_ref": "q"}], '
                   '"evidence_refs": ["q"]}')
    verdict = verifier.verify(task, wrong_price)
    assert not verdict.passed
    assert "recomputed best quote" in verdict.reason
    ok = ('{"vendor": "Lenovo", "total": 54000, "unit_price": 1080, '
          '"reasons": [{"claim": "best", "evidence_ref": "q"}], '
          '"evidence_refs": ["q"]}')
    assert verifier.verify(task, ok).passed


def test_verifier_accepts_grounded_recommendation():
    verifier = Verifier(by_task_type=dict(PROCUREMENT_VALIDATORS))
    task = _task_with_quotes()
    ok = ('{"vendor": "Lenovo", "total": 54000, "unit_price": 1080, '
          '"reasons": [{"claim": "cheapest", "evidence_ref": "quote-lenovo"}], '
          '"evidence_refs": ["quote-lenovo"]}')
    assert verifier.verify(task, ok).passed


# ---------------------------------------------------------------- HERMES-05

def test_duplicate_result_upserted_not_appended(tmp_path):
    """Timeout-then-retry of the same task must not create duplicate result
    rows — enforced by a UNIQUE index + upsert at the DB level."""
    s = AsyncTaskStore(str(tmp_path / "t.db"))
    s.create_task(Task(task_id="t1", task_type="analyze", workflow_id="wf"))
    s.mark_completed("t1", result_uri="s3://first")
    s.mark_completed("t1", result_uri="s3://second")  # redelivery/retry
    rows = s.task_results("t1")
    assert len(rows) == 1
    assert rows[0]["result_uri"] == "s3://second"


def test_retry_after_failure_keeps_one_row_per_status(tmp_path):
    s = AsyncTaskStore(str(tmp_path / "t.db"))
    s.create_task(Task(task_id="t1", task_type="analyze", workflow_id="wf"))
    s.mark_failed("t1", "timeout")            # attempt 1 failed
    s.mark_failed("t1", "timeout")            # attempt 2 failed (redelivery)
    s.mark_completed("t1", result_uri="s3://done")  # attempt 3 succeeded
    rows = s.task_results("t1")
    assert len(rows) == 2  # exactly one 'failed' + one 'completed'
    assert {r["status"] for r in rows} == {"failed", "completed"}


# ---------------------------------------------------------------- HERMES-06

def test_failed_dep_cascades_dependents_workflow_terminates(tmp_path):
    """A stuck/failed agent must not deadlock the DAG: dependents are
    terminated and the workflow ends deterministically, within the deadline."""
    orch, store = _orch(tmp_path)

    def ok(t):
        return "s3://res"

    def boom(t):
        raise RuntimeError("timeout: agent hung and was killed")

    graph = [
        {"task_id": "root", "task_type": "research", "deps": []},
        {"task_id": "mid", "task_type": "analyze", "deps": ["root"]},
        {"task_id": "leaf", "task_type": "report", "deps": ["mid"]},
    ]
    handlers = {"research": boom, "analyze": ok, "report": ok}
    start = time.time()
    agg = orch.run_workflow(graph, handlers, workers=2, timeout=30)
    elapsed = time.time() - start
    assert agg["status"] == "failed"
    counts = agg["counts"]
    assert counts.get("failed") == 3  # root + cascaded mid + leaf
    t = store.get_task("leaf")
    assert t.status == TaskStatus.FAILED
    row = next(r for r in store.list_tasks() if r["task_id"] == "leaf")
    assert "upstream" in (row.get("error") or "")
    assert elapsed < 15  # deterministic termination, no hang


# ---------------------------------------------------------------- HERMES-08

def test_illegal_transitions_rejected():
    with pytest.raises(ValueError):
        validate_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING)
    with pytest.raises(ValueError):
        validate_transition(TaskStatus.CANCELLED, TaskStatus.RUNNING)
    with pytest.raises(ValueError):
        validate_transition(TaskStatus.DEADLETTER, TaskStatus.QUEUED)


def test_new_lifecycle_transitions_legal():
    validate_transition(TaskStatus.RUNNING, TaskStatus.TIMEOUT)
    validate_transition(TaskStatus.TIMEOUT, TaskStatus.RETRYING)
    validate_transition(TaskStatus.TIMEOUT, TaskStatus.DEADLETTER)
    validate_transition(TaskStatus.QUEUED, TaskStatus.CANCELLED)
    validate_transition(TaskStatus.FAILED, TaskStatus.DEADLETTER)


# ---------------------------------------------------------------- HERMES-09

def test_budget_snapshot_in_aggregate(tmp_path):
    orch, _ = _orch(tmp_path)
    handlers = {"research": lambda t: "s3://res"}
    graph = [{"task_id": "r1", "task_type": "research", "deps": []}]
    agg = orch.run_workflow(graph, handlers, workers=1)
    assert "budget" in agg
    assert agg["budget"]["exceeded"] is False


def test_cost_tracker_stops_runaway_worker(tmp_path):
    """A token-hungry workflow hits a hard STOP: BudgetExceededError is raised
    (non-retryable, dead-lettered) instead of burning unbounded budget."""
    orch, _ = _orch(tmp_path)
    tracker = CostTracker(limits=BudgetLimits(max_tokens=100, max_time_seconds=300))
    tracker.record(tokens=500)  # way over budget
    assert tracker.exceeded()   # stop reason present
    t = Task(task_id="x1", task_type="research", workflow_id="wf")
    orch.store.create_task(t)
    from hermes.async_engine.worker import Worker
    w = Worker("w1", "research", lambda task: "s3://r", orch.store, InMemoryBus(),
               cost_tracker=tracker)
    with pytest.raises(BudgetExceededError):
        w._execute(t, None)


def test_budget_exceeded_is_non_retryable():
    from hermes.async_engine.retry import classify_failure
    assert classify_failure(BudgetExceededError("budget: token budget reached")) is False
