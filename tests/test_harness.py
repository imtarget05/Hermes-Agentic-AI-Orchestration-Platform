"""Harness Engineering — guardrails & evaluation metrics (Phase 0)."""
from hermes.harness import EvaluationRegistry, HarnessEvaluator, check_output
from hermes.harness.guardrails import OutputGuardrail


class _FakeStore:
    """Minimal TaskStore-shaped object for lifecycle metrics."""

    def __init__(self, rows):
        self._rows = rows

    def list_tasks(self, limit=10000):
        return self._rows


def test_guardrail_advisor_requires_citation():
    # claim without citation -> fail
    v = check_output("advisor", "Tôi khuyên chi 5000 usd để đầu tư.")
    assert v.passed is False
    assert v.violations


def test_guardrail_knowledge_with_citation_passes():
    v = check_output("knowledge",
                     "Theo SOP, onboarding mất 14 ngày [source=sop/onboarding.md].")
    assert v.passed is True


def test_guardrail_uncited_number_fails():
    v = check_output("competitor", "Đối thủ giảm giá còn 999 usd cho gói Pro.")
    assert v.passed is False


def test_guardrail_registered_rule():
    g = OutputGuardrail()
    g.register_rule("custom", "always_fail", lambda _: False)
    assert g.check("custom", "anything").passed is False


def test_evaluator_aggregates():
    reg = EvaluationRegistry()
    reg.record(domain="knowledge", success=True, accuracy=1.0, latency_ms=10,
               cost_tokens=50)
    reg.record(domain="knowledge", success=False, error_trace="timeout",
               latency_ms=25)
    ev = HarnessEvaluator(reg)
    metrics = {m.domain: m for m in ev.compute_metrics()}
    assert metrics["knowledge"].success_rate == 0.5
    assert metrics["knowledge"].cost_tokens == 50
    assert metrics["knowledge"].error_trace == "timeout"


def test_lifecycle_success_rate():
    store = _FakeStore([
        {"id": "a", "status": "completed"},
        {"id": "b", "status": "failed"},
    ])
    from hermes.harness.eval import lifecycle_success_rate

def test_orchestrator_records_into_eval_registry(tmp_path):
    """AsyncOrchestrator should feed task executions into the harness
    EvaluationRegistry (wired from runtime.services())."""
    from hermes.async_engine.backends import InMemoryBus
    from hermes.async_engine.orchestrator import (
        AsyncOrchestrator,
        set_default_eval_registry,
    )
    from hermes.async_engine.store import AsyncTaskStore
    from hermes.harness import EvaluationRegistry

    reg = EvaluationRegistry()
    set_default_eval_registry(reg)
    try:
        store = AsyncTaskStore(str(tmp_path / "t.db"))
        bus = InMemoryBus()
        orch = AsyncOrchestrator(store, bus, eval_registry=reg)
        graph = [
            {"task_id": "a-1", "task_type": "research", "deps": []},
            {"task_id": "b-1", "task_type": "analyze", "deps": ["a-1"]},
        ]
        handlers = {"research": lambda t: "ok", "analyze": lambda t: "ok"}
        agg = orch.run_workflow(graph, handlers, workers=2, timeout=30)
        assert agg["counts"].get("completed") == 2
        recs = reg.records()
        assert len(recs) == 2
        by_domain = {r["domain"]: r for r in recs}
        assert set(by_domain) == {"research", "analyze"}
        assert all(r["success"] for r in recs)
        assert all(r["latency_ms"] >= 0 for r in recs)
    finally:
        set_default_eval_registry(None)