"""The Hermes agentic pipeline — all 8 loops in one entry point.

    Context → Plan → Dispatch (RabbitMQ) → Execute → Verify
            → Recover → Evaluate → Learn/Audit (feeds back into 1 & 2)

`run_agent_workflow` is the demo/portfolio surface: give it a natural-language
request and it runs the full loop, returning the aggregate + evaluation +
learned policy. RabbitMQ is only the implementation of loop 3.
"""
from __future__ import annotations

from typing import Any, Callable

from .audit import LearningLoop
from .context import ContextBuilder, ExecutionContext
from .evaluate import workflow_report
from .planner import Planner


def run_agent_workflow(
    request: str,
    store,
    bus,
    events=None,
    handlers: dict[str, Callable[[Any], str]] | None = None,
    llm=None,
    workers: int = 2,
    source: str = "pipeline",
    user_id: str = "",
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Run loops 1-8 for one user request and return the full report."""
    from ..metrics import build_metrics
    from ..orchestrator import AsyncOrchestrator

    # loop 8 (read side): learned policy shapes planning
    learning = LearningLoop(store)
    policy = learning.load_policy()

    # loop 1: context
    context_builder = ContextBuilder(store, audit=learning)
    ctx: ExecutionContext = context_builder.build(request, source=source, user_id=user_id)

    # loop 2: planning (LLM hook optional, deterministic fallback)
    planner = Planner(llm=llm, policy=policy)
    graph = planner.plan(request)
    ContextBuilder.attach(graph, ctx)

    # loops 3-6: dispatch -> execute -> verify -> recover (orchestrator + workers)
    if handlers is None:
        handlers = _default_handlers()
    orch = AsyncOrchestrator(store, bus, events=events, metrics=build_metrics(False))
    agg = orch.run_workflow(graph, handlers, workers=workers, timeout=120.0,
                            task_timeout_seconds=timeout_seconds)

    # loop 5/6 operate inside the worker (verify + retry/timeout/breaker)

    # loop 7: evaluation
    report = workflow_report(store, agg["workflow_id"])

    # loop 8 (write side): mine this run into the policy for the next request
    try:
        policy = learning.refresh_policy()
    except Exception:
        pass

    return {
        "request": request,
        "loops": {
            "1_context": ctx.to_dict(),
            "2_planning": {"nodes": [{"task_id": n["task_id"], "task_type": n["task_type"],
                                      "deps": n.get("deps", [])} for n in graph]},
            "3_dispatch": {"queue_routing": "hermes.tasks -> q.agent.<type>"},
            "4_5_6_execution": agg,
            "7_evaluation": report,
            "8_learning": {"policy": policy},
        },
        "aggregate": agg,
    }


def _default_handlers() -> dict[str, Callable[[Any], str]]:
    """Deterministic agent handlers (LLM stub-mode, same as Project 2 agents).

    Procurement handlers are store-aware; the generic fallback below covers
    legacy research/analyze/report/notify graphs. Procurement runs should pass
    `build_procurement_handlers(store)` explicitly so join nodes read siblings.
    """
    import time

    def _agent(task, kind):
        time.sleep(0.01)
        return f"s3://hermes/{kind}/{task.task_id}"

    try:
        from ...procurement.handlers import build_procurement_handlers as _build_proc
        proc = _build_proc(None)
    except Exception:
        proc = {}

    handlers = {
        "research": lambda t: _agent(t, "research"),
        "analyze": lambda t: _agent(t, "analyze"),
        "report": lambda t: _agent(t, "report"),
        "notify": lambda t: _agent(t, "notify"),
    }
    handlers.update(proc)
    return handlers