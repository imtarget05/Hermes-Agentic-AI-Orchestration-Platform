import time

import pytest

from hermes.async_engine.contract import Task, TaskStatus
from hermes.async_engine.loops import (
    CircuitBreaker,
    ContextBuilder,
    LearningLoop,
    Planner,
    TaskTimeoutError,
    Verifier,
    escalation_metadata,
    run_with_timeout,
)
from hermes.async_engine.loops.verify import evidence_check
from hermes.async_engine.store import AsyncTaskStore


def _store(tmp_path):
    return AsyncTaskStore(str(tmp_path / "t.db"))


# ---- Loop 1: Context ------------------------------------------------------ #
def test_context_builds_evidence_and_patterns(tmp_path):
    store = _store(tmp_path)
    learning = LearningLoop(store, policy_path=str(tmp_path / "p.json"))
    store.create_task(Task(task_id="old1", workflow_id="wf0", task_type="analyze",
                           status=TaskStatus.QUEUED))
    store.mark_started("old1", "w1")
    store.mark_completed("old1", result_uri="s3://e/old1")

    cb = ContextBuilder(store, audit=learning)
    ctx = cb.build("do a thing", source="telegram", user_id="u1")
    assert ctx.request == "do a thing" and ctx.source == "telegram"
    assert any(e["result_uri"] == "s3://e/old1" for e in ctx.prior_evidence)
    assert "failure_patterns" in ctx.failure_patterns or ctx.failure_patterns == {}


def test_context_attach_injects_payload(tmp_path):
    ctx = ContextBuilder(AsyncTaskStore(str(tmp_path / "x.db"))).build("req")
    graph = [{"task_id": "a", "task_type": "research", "deps": []}]
    out = ContextBuilder.attach(graph, ctx)
    assert out[0]["payload"]["context"]["request"] == "req"


# ---- Loop 2: Planning ------------------------------------------------------ #
def test_planner_templates():
    p = Planner()
    fanout = p.plan("compare A versus B")
    assert [n["task_type"] for n in fanout] == ["research", "analyze", "analyze", "report"]
    assert fanout[3]["deps"] == ["analyze-1", "analyze-2"]  # join waits for both
    notify = p.plan("send me an alert")
    assert [n["task_type"] for n in notify] == ["research", "report", "notify"]
    default = p.plan("just do it")
    assert [n["task_type"] for n in default] == ["research", "analyze", "report"]


def test_planner_llm_invalid_falls_back():
    p = Planner(llm=lambda prompt: "not json at all")
    assert p.plan("hello")[0]["task_type"] == "research"  # template fallback


def test_planner_llm_valid_plan_used():
    plan = ('[{"task_id":"r","task_type":"research","deps":[]},'
            '{"task_id":"n","task_type":"notify","deps":["r"]}]')
    p = Planner(llm=lambda prompt: plan)
    nodes = p.plan("weird request")
    assert [n["task_id"] for n in nodes] == ["r", "n"]


def test_planner_applies_learned_policy():
    p = Planner(policy={"max_attempts": {"analyze": 4}})
    nodes = p.plan("do it")
    analyze = next(n for n in nodes if n["task_type"] == "analyze")
    assert analyze["max_attempts"] == 4


# ---- Loop 5: Verification -------------------------------------------------- #
def test_verifier_pass_and_quality_fail():
    v = Verifier()
    assert v.verify(Task(task_type="analyze"), "s3://x/1").passed is True
    bad = v.verify(Task(task_type="analyze"), "   ")
    assert bad.passed is False and bad.retryable is False  # schema error -> DLQ
    # evidence check is opt-in per task type, not universal
    v_evidence = Verifier(by_task_type={"analyze": [evidence_check]})
    assert v_evidence.verify(Task(task_type="analyze"), "ok").passed is False


def test_verifier_per_task_type_quality_check():
    v = Verifier(by_task_type={"report": [lambda r: "" if "DONE" in r else "quality check: no marker"]})
    ok = v.verify(Task(task_type="report"), "DONE s3://x")
    assert ok.passed
    failed = v.verify(Task(task_type="report"), "s3://x")
    assert failed.passed is False and failed.retryable is True  # quality -> retry


def test_timeout_loop6():
    def slow():
        time.sleep(0.4)
        return "x"
    with pytest.raises(TaskTimeoutError):
        run_with_timeout(slow, 0.05, "analyze")
    assert run_with_timeout(lambda: "fast", 1.0) == "fast"


def test_circuit_breaker_loop6():
    b = CircuitBreaker(threshold=3, cooldown_seconds=0.1)
    for _ in range(3):
        b.record_failure("analyze")
    assert b.state("analyze") == "open"
    assert b.allow("analyze") is False
    time.sleep(0.15)
    assert b.state("analyze") == "half-open"
    b.record_success("analyze")
    assert b.state("analyze") == "closed" and b.allow("analyze") is True


def test_escalation_metadata():
    t = Task(task_type="analyze", attempt=3, max_attempts=3)
    meta = escalation_metadata(t, "boom")
    assert meta["escalation"] == "manual_review" and meta["attempts_used"] == 3